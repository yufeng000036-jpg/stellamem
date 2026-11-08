"""Deterministic Memory Health Purge & Low-Cost Conflict Resolution for Stellamem.

Addresses the cost and lifecycle governance bottleneck:
1. Deterministic normalization & deduplication: handles timestamp overwrites,
   whitespace/punctuation normalization, and exact/near-exact duplicate folding
   in pure Python with ZERO LLM API calls.
2. Rule-based contradiction pre-screening: detects clear negation/antonym or
   value-shift conflicts within the same `(subject, topic)` namespace and logs
   only genuine semantic conflicts to `conflict-log.md` for human or LLM review.
3. Safe handle-free archival & purge: closes all file descriptors before
   atomic replacement so Windows never raises `PermissionError` (`WinError 32`).
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote as _url_quote


def _pct(value: str) -> str:
    """Percent-encode a field so embedded separators can't collide."""
    return _url_quote(value, safe="")


from compiler.file_lock import atomic_write_text

# Kinds that never demote: the core portrait (same list as rank.py's
# never-decay kinds). Demoting an identity/user-core record would break
# the persona's identity; they stay active no matter how old or unhit.
NEVER_DEMOTE_KINDS = ("identity", "user-core", "core")

# Default demotion reason recorded on the record.
DEMOTE_REASON_90D_NO_HIT = "90d-no-hit"

NEGATION_PAIRS: tuple[tuple[str, str], ...] = (
    ("喜欢", "讨厌"),
    ("喜欢", "不喜欢"),
    ("开启", "关闭"),
    ("启用", "禁用"),
    ("允许", "禁止"),
    ("总是", "从不"),
    ("偏好深色", "偏好浅色"),
    ("prefer dark", "prefer light"),
    ("enable", "disable"),
    ("always", "never"),
    ("like", "dislike"),
    ("love", "hate"),
)


def normalize_statement(text: str) -> str:
    """Normalize whitespace, casing, and punctuation for deterministic comparison."""
    cleaned = re.sub(r"\s+", " ", text.strip().lower())
    cleaned = re.sub(r"[，。！？；：、“”‘’,.!?;:\"'()\[\]]+", "", cleaned)
    return cleaned


def compute_statement_signature(subject: str, topic: str, text: str) -> str:
    """Generate a deterministic hash signature for deduplication.

    Each field is percent-encoded before joining so that a "::" inside a
    field value can never collide with the separator (cf. Mem0 CVE-2026-116575).
    """
    norm = "%s::%s::%s" % (
        _pct(subject.strip().lower()),
        _pct(topic.strip().lower()),
        _pct(normalize_statement(text)),
    )
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def detect_semantic_contradiction(stmt_a: str, stmt_b: str) -> tuple[bool, str]:
    """Detect whether two statements on the same topic have a polarity/value conflict.

    Returns `(is_conflict, reason)`. Deterministic updates (same polarity or exact match)
    are resolved without invoking an LLM.
    """
    norm_a = normalize_statement(stmt_a)
    norm_b = normalize_statement(stmt_b)
    if not norm_a or not norm_b or norm_a == norm_b:
        return False, "identical_normalized"

    for pos, neg in NEGATION_PAIRS:
        p = normalize_statement(pos)
        n = normalize_statement(neg)
        if (p in norm_a and n in norm_b) or (n in norm_a and p in norm_b):
            return True, f"polarity_inversion({pos}<->{neg})"

    nums_a = re.findall(r"\d+(?:\.\d+)?", stmt_a)
    nums_b = re.findall(r"\d+(?:\.\d+)?", stmt_b)
    if nums_a and nums_b and nums_a != nums_b:
        stem_a = re.sub(r"\d+(?:\.\d+)?", "#", norm_a)
        stem_b = re.sub(r"\d+(?:\.\d+)?", "#", norm_b)
        if stem_a == stem_b:
            return True, f"numeric_contradiction({nums_a} vs {nums_b})"

    return False, "compatible_evolution"


@dataclass
class PurgeReport:
    scanned_records: int = 0
    deduplicated_count: int = 0
    timestamp_fast_forwarded: int = 0
    decayed_archived_count: int = 0
    demoted_count: int = 0
    demoted_ids: list[str] = field(default_factory=list)
    llm_escalated_conflicts: int = 0
    conflicts_logged: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanned_records": self.scanned_records,
            "deduplicated_count": self.deduplicated_count,
            "timestamp_fast_forwarded": self.timestamp_fast_forwarded,
            "decayed_archived_count": self.decayed_archived_count,
            "demoted_count": self.demoted_count,
            "demoted_ids": self.demoted_ids,
            "llm_escalated_conflicts": self.llm_escalated_conflicts,
            "conflicts_logged": self.conflicts_logged,
        }


def demote_decayed(
    records: list[dict[str, Any]],
    *,
    now_ms: int | None = None,
    archive_after_days: int = 90,
    reason: str = DEMOTE_REASON_90D_NO_HIT,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Demote 90-day-no-hit records to the archive layer.

    A record demotes when ALL of these hold: not pinned; kind not in
    ``NEVER_DEMOTE_KINDS``; status is ``"active"``; last update older
    than ``archive_after_days``; zero hits. Demotion sets
    ``status="archived"`` plus ``archived_at`` and ``archived_reason``
    — the record stays in its file, in the index, and in every audit,
    but it is no longer auto-injected: the JS adapter's ``eligible()``
    requires ``status === "active"``, and the Python recall path
    defaults to status ``("active", "pending")``. Reversible: ``status``
    is a mutable field, so restoring a record is setting it back to
    ``"active"`` through the normal edit path (no new code needed).

    ``decayed_candidate`` (the purge's triage mark) and demotion use
    the same age/hit condition, but demotion additionally requires an
    active status and a demotable kind — so ``demoted_count`` is a
    subset of ``decayed_archived_count``, never larger. Returns the
    (mutated) record list and a report dict.
    """
    from datetime import datetime, timezone

    current_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    decay_cutoff_ms = current_ms - (archive_after_days * 86_400 * 1000)
    demoted: list[str] = []
    skipped_pinned = 0
    skipped_core_kind = 0
    skipped_not_active = 0
    for item in records:
        kind = str(item.get("kind") or "").strip().lower()
        if item.get("pinned", False):
            skipped_pinned += 1
            continue
        if kind in NEVER_DEMOTE_KINDS:
            skipped_core_kind += 1
            continue
        if item.get("status", "active") != "active":
            skipped_not_active += 1
            continue
        ts_ms = int(item.get("updated_at_ms") or item.get("occurred_at_ms") or current_ms)
        if ts_ms >= decay_cutoff_ms:
            continue
        if int(item.get("hit_count", 0)) != 0:
            continue
        item["status"] = "archived"
        item["archived_at"] = datetime.fromtimestamp(current_ms / 1000, tz=timezone.utc).isoformat()
        item["archived_reason"] = reason
        demoted.append(str(item.get("id") or item.get("claim_id") or ""))
    return records, {
        "demoted": demoted,
        "demoted_count": len(demoted),
        "skipped_pinned": skipped_pinned,
        "skipped_core_kind": skipped_core_kind,
        "skipped_not_active": skipped_not_active,
    }


def run_memory_purge(
    records: list[dict[str, Any]],
    *,
    conflict_log_path: Path | str | None = None,
    version_chain_path: Path | str | None = None,
    now_ms: int | None = None,
    archive_after_days: int = 90,
    demote: bool = False,
) -> tuple[list[dict[str, Any]], PurgeReport]:
    """Execute deterministic memory health check, deduplication, and conflict triage.

    - Exact normalized duplicates within the same `(subject, topic)` are folded into the
      newest record (`timestamp_fast_forwarded`), saving 100% of LLM tokens.
    - Non-pinned records older than `archive_after_days` with zero recent hits are marked
      `decayed_candidate=True` (never silently deleted, preserving white-box auditability).
    - Deep semantic contradictions are appended to `conflict-log.md` and tracked in
      `version-chain.json`.
    - With `demote=True`, the marked records are additionally demoted to the
      archive layer (`status="archived"` + `archived_at` + `archived_reason`)
      via `demote_decayed` — they stay in place and in the index, but stop
      being auto-injected. Default False: marking only, current behavior.
    """
    current_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    decay_cutoff_ms = current_ms - (archive_after_days * 86_400 * 1000)
    report = PurgeReport(scanned_records=len(records))

    by_sig: dict[str, dict[str, Any]] = {}
    by_topic: dict[tuple[str, str], list[dict[str, Any]]] = {}
    version_chains: dict[str, list[dict[str, Any]]] = {}

    sorted_records = sorted(
        records,
        key=lambda r: int(r.get("updated_at_ms") or r.get("occurred_at_ms") or 0),
    )

    for rec in sorted_records:
        item = dict(rec)
        subject = str(item.get("subject") or item.get("namespace") or "unknown").strip()
        # Core Stellamem semantic records use (subject, predicate, value);
        # preference cards and ledger entries use (subject, topic, statement/content).
        # Map both so core records are never read as empty statements
        # (which would make distinct memories hash-identical and get folded).
        topic = str(
            item.get("topic") or item.get("category") or item.get("predicate") or "general"
        ).strip()
        statement = str(
            item.get("statement") or item.get("content") or item.get("value") or ""
        ).strip()
        rec_id = str(item.get("id") or compute_statement_signature(subject, topic, statement))
        ts_ms = int(item.get("updated_at_ms") or item.get("occurred_at_ms") or current_ms)

        sig = compute_statement_signature(subject, topic, statement)
        if sig in by_sig:
            existing = by_sig[sig]
            existing["updated_at_ms"] = max(int(existing.get("updated_at_ms", 0)), ts_ms)
            existing["hit_count"] = int(existing.get("hit_count", 1)) + int(
                item.get("hit_count", 1)
            )
            report.deduplicated_count += 1
            report.timestamp_fast_forwarded += 1
            continue

        topic_key = (subject, topic)
        prior_list = by_topic.setdefault(topic_key, [])
        for prior in prior_list:
            # Same field mapping as the extraction above: core records store
            # their content in `value`, not `statement`/`content`.
            prior_stmt = str(
                prior.get("statement") or prior.get("content") or prior.get("value") or ""
            )
            is_conflict, reason = detect_semantic_contradiction(prior_stmt, statement)
            if is_conflict:
                conflict_entry = {
                    "subject": subject,
                    "topic": topic,
                    "prior_id": prior.get("id"),
                    "prior_statement": prior_stmt,
                    "new_id": rec_id,
                    "new_statement": statement,
                    "reason": reason,
                    "detected_at_ms": current_ms,
                    "resolution_mode": "needs_llm_or_human_arbitration",
                }
                report.llm_escalated_conflicts += 1
                report.conflicts_logged.append(conflict_entry)
                item["conflict_status"] = "flagged"

        if (
            not item.get("pinned", False)
            and ts_ms < decay_cutoff_ms
            and int(item.get("hit_count", 0)) == 0
        ):
            item["decayed_candidate"] = True
            report.decayed_archived_count += 1

        by_sig[sig] = item
        prior_list.append(item)
        # Delimiter-injection-safe chain key (cf. Mem0 CVE-2026-116575):
        # subject/topic are user-controlled (e.g. livestream nicknames) and may
        # contain ":". Percent-encode each component so "a:b"/"c" can never
        # collide with "a"/"b:c".
        chain_key = f"{_url_quote(subject, safe='')}:{_url_quote(topic, safe='')}"
        version_chains.setdefault(chain_key, []).append(
            {
                "id": rec_id,
                "signature": sig,
                "statement": statement,
                "timestamp_ms": ts_ms,
                "status": item.get("status", "active"),
            }
        )

    retained = list(by_sig.values())

    if demote:
        _, demote_report = demote_decayed(
            retained, now_ms=current_ms, archive_after_days=archive_after_days
        )
        report.demoted_count = demote_report["demoted_count"]
        report.demoted_ids = demote_report["demoted"]

    if conflict_log_path is not None and report.conflicts_logged:
        _write_conflict_log(Path(conflict_log_path), report.conflicts_logged)

    if version_chain_path is not None:
        wrote = _write_version_chain(Path(version_chain_path), version_chains, current_ms)
        if not wrote:
            report.conflicts_logged.append(
                {
                    "subject": "-",
                    "topic": "-",
                    "reason": "version_chain_write_refused_empty_over_nonempty",
                    "detected_at_ms": current_ms,
                    "resolution_mode": "fail_closed_no_overwrite",
                }
            )

    return retained, report


def _write_conflict_log(path: Path, conflicts: list[dict[str, Any]]) -> None:
    path = path.resolve()
    existing = ""
    if path.exists():
        existing = path.read_text(encoding="utf-8")
    lines = (
        [existing.rstrip()]
        if existing.strip()
        else ["# Stellamem Conflict Log (White-Box Audit)\n"]
    )
    for c in conflicts:
        lines.append(
            f"## [{c['subject']} / {c['topic']}] {c['reason']} (ts={c['detected_at_ms']})\n"
            f"- **Prior (`{c['prior_id']}`)**: {c['prior_statement']}\n"
            f"- **New (`{c['new_id']}`)**: {c['new_statement']}\n"
            f"- **Action**: `{c['resolution_mode']}`\n"
        )
    atomic_write_text(path, "\n".join(lines) + "\n")


def _write_version_chain(
    path: Path,
    chains: dict[str, list[dict[str, Any]]],
    updated_at_ms: int,
) -> bool:
    """Write version chains fail-closed, merging with existing history.

    New entries are merged into (not overwriting) any existing chain file,
    so a purge run over partial records can never destroy the full history.
    Returns True on write, False only when refusing to overwrite a non-empty
    existing chain file with empty chains.
    """
    merged: dict[str, list[dict[str, Any]]] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing, dict) and isinstance(existing.get("chains"), dict):
                for k, v in existing["chains"].items():
                    if isinstance(v, list):
                        merged[str(k)] = list(v)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            # Unparseable existing file: never overwrite it, period.
            # The original bytes may be recoverable; an empty write would
            # destroy that chance.
            return False
    if not chains and merged:
        return False
    for key, entries in chains.items():
        bucket = merged.setdefault(key, [])
        seen_ids = {e.get("id") for e in bucket if isinstance(e, dict)}
        for entry in entries:
            eid = entry.get("id") if isinstance(entry, dict) else None
            if eid is None or eid not in seen_ids:
                bucket.append(entry)
                if eid is not None:
                    seen_ids.add(eid)
    payload = {
        "schema_version": 1,
        "updated_at_ms": updated_at_ms,
        "chains": merged,
    }
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return True
