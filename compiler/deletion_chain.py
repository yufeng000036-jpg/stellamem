"""Deletion derivation-chain: trace and forget a record everywhere it lives.

栖流's paper note (EigenFlux, 2026-11-08; Lei Chen et al.,
arXiv:2606.10062v2): deleting only the original record leaves the
derived summaries able to recover the content in ~20% of cases. A
deletion request must trace the whole derivation chain — original,
summary, index, cache — and either purge the whole pipeline or replace
the content with a tombstone.

Layers traced (and, by ``forget_record``, acted on):

- ``live``: the claim block in a MANAGED file. Tombstone mode replaces
  its value/evidence with a marker and sets ``status="tombstoned"``
  (the claim id, revision id and provenance stay, so the deletion
  itself is auditable); purge mode removes the block entirely.
- ``summary``: synthesis lines in ``long-term/self-changes-<subject>.md``
  that quote the record's value. Matched by normalized text
  containment — a paraphrased summary is NOT found by text match
  (see Limitations below). Tombstone mode replaces the line with a
  deletion marker that keeps its source day; purge mode removes it.
- ``diary``: dated diary lines quoting the value. Traced ONLY, never
  auto-modified: the diary is the user's own journal, and rewriting
  history is the operator's call, not the chain's.
- ``index``: the SQLite index. After the files change, the index is
  rebuilt from the files (``Index.build``) and re-checked: no active
  row for the claim may remain.
- ``cache``: ``DerivedMarkdownCache`` entries for the modified files
  are invalidated explicitly (the cache also self-heals on next read
  via its content hash, but a deletion must not wait for a read).
- ``raw_archive``: the raw session stream holding the record's
  provenance ``record_id`` (when the claim carries one). The record is
  dropped from the stream, the stream rewritten, the manifest rebuilt,
  and the deletion recorded in the ledger — the same pattern as
  ``raw_archive.forget``, but per record instead of per session.

Limitations (honest, by design):

- Summary tracing is by value text, not by id: synthesis lines cite
  their source day, not a claim id. A summary that paraphrases the
  value will not be found; the trace reports the matching method so
  the operator knows what was checked.
- The 4K desktop page is a derived render, not a layer of the chain:
  it is rebuilt from records on demand, so a tombstoned/purged record
  drops out of the next rebuild; a stale page between deletion and
  rebuild is a known lag, not a residue.
- ``forget_record`` is idempotent: calling it twice reports the second
  run as already-tombstoned / clean instead of failing.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

try:
    from .derived_cache import DerivedMarkdownCache
    from .file_lock import atomic_write_text
    from .indexer import MANAGED, Index
    from .storage import load_records, render_records
except Exception:  # pragma: no cover
    from derived_cache import DerivedMarkdownCache  # type: ignore
    from file_lock import atomic_write_text  # type: ignore
    from indexer import MANAGED, Index  # type: ignore
    from storage import load_records, render_records  # type: ignore

try:
    from . import raw_archive
except Exception:  # pragma: no cover
    import raw_archive  # type: ignore

TOMBSTONE_VALUE = "[已删除]"
MODES = ("tombstone", "purge")
_SYNTH_SOURCE_RE = re.compile(r"来源：(\d{4}-\d{2}-\d{2})")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _tombstone_record(record: dict, reason: str) -> dict:
    out = dict(record)
    out["status"] = "tombstoned"
    out["value"] = TOMBSTONE_VALUE
    out["evidence"] = ""
    out["deleted_at"] = datetime.now(timezone.utc).isoformat()
    out["deleted_reason"] = reason
    return out


def _live_locations(root: Path, claim_id: str) -> list[dict]:
    """All live claim blocks with this claim id: [{file, record, index}]."""
    found = []
    for rel_path, _layer in MANAGED:
        path = root / rel_path
        if not path.exists():
            continue
        try:
            _prefix, records = load_records(path)
        except ValueError:
            continue
        for i, rec in enumerate(records):
            if rec.get("claim_id") == claim_id:
                found.append({"file": rel_path, "record": rec, "index": i})
    return found


def _value_lines(path: Path, needle_norm: str) -> list[dict]:
    """Lines in a file whose normalized text contains the needle."""
    if not needle_norm or not path.exists():
        return []
    hits = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for n, line in enumerate(lines):
        if needle_norm in _norm(line):
            hits.append({"file": str(path), "line_no": n + 1, "line": line})
    return hits


def _summary_files(root: Path) -> list[Path]:
    return sorted((root / "long-term").glob("self-changes-*.md"))


def _diary_files(root: Path) -> list[Path]:
    return sorted((root / "daily").glob("*/*.md"))


def _index_has_active(index_db: Path, claim_id: str) -> bool:
    if not index_db.exists():
        return False
    import sqlite3

    try:
        con = sqlite3.connect(str(index_db))
        try:
            row = con.execute(
                "SELECT COUNT(*) FROM records WHERE claim_id=? AND status='active'",
                (claim_id,),
            ).fetchone()
            return bool(row and row[0])
        finally:
            con.close()
    except Exception:
        return False


def trace(root, claim_id: str, *, index_db=None, archive_root=None) -> dict:
    """Trace one claim across the derivation chain. Read-only."""
    root = Path(root)
    live = _live_locations(root, claim_id)
    value_norm = ""
    provenance_record_id = None
    if live:
        first = live[0]["record"]
        value_norm = _norm(first.get("value") or "")
        provenance_record_id = (first.get("provenance") or {}).get("record_id")
    summary: list[dict] = []
    diary: list[dict] = []
    if value_norm:
        for path in _summary_files(root):
            summary.extend(_value_lines(path, value_norm))
        for path in _diary_files(root):
            diary.extend(_value_lines(path, value_norm))
    db_path = Path(index_db) if index_db else root / "index.db"
    index_found = _index_has_active(db_path, claim_id) if db_path.exists() else None
    raw: dict = {"status": "not-checked"}
    if archive_root is not None:
        archive_root = Path(archive_root)
        if provenance_record_id is None:
            raw = {"status": "no-provenance-link"}
        else:
            ids = raw_archive.existing_ids(archive_root)
            raw = {
                "status": "found" if provenance_record_id in ids else "absent",
                "provenance_record_id": provenance_record_id,
            }
    cache_path = root / ".derived_index_cache.json"
    cache = {"status": "no-cache-file"}
    if cache_path.exists():
        try:
            entries = json.loads(cache_path.read_text(encoding="utf-8")).get("entries", {})
            affected = sorted({loc["file"] for loc in live})
            cache = {
                "status": "found" if any(k in entries for k in affected) else "clean",
                "entries_for_affected_files": [k for k in affected if k in entries],
            }
        except (OSError, ValueError):
            cache = {"status": "unreadable"}
    return {
        "claim_id": claim_id,
        "live": [
            {"file": loc["file"], "status": loc["record"].get("status"), "index": loc["index"]}
            for loc in live
        ],
        "summary": summary,
        "diary": diary,
        "index": {"active_present": index_found, "db": str(db_path)},
        "cache": cache,
        "raw_archive": raw,
    }


def forget_record(
    root,
    claim_id: str,
    *,
    mode: str = "tombstone",
    index_db=None,
    archive_root=None,
    reason: str = "deletion-request",
) -> dict:
    """Forget one claim across the whole chain. Idempotent.

    ``mode="tombstone"`` (default) replaces content with a deletion
    marker and keeps the audit trail; ``mode="purge"`` removes the
    content entirely. Returns a per-layer report.
    """
    if mode not in MODES:
        raise ValueError(f"unknown mode: {mode!r} (expected one of {MODES})")
    root = Path(root)
    report: dict = {"claim_id": claim_id, "mode": mode, "layers": {}}
    before = trace(root, claim_id, index_db=index_db, archive_root=archive_root)
    live = _live_locations(root, claim_id)
    if not live:
        report["layers"]["live"] = {"action": "not-found", "files": []}
        report["layers"]["summary"] = {"action": "clean", "files": []}
    else:
        modified_files: set[str] = set()
        already = all(loc["record"].get("status") == "tombstoned" for loc in live)
        if mode == "tombstone" and already:
            report["layers"]["live"] = {
                "action": "already-tombstoned",
                "files": sorted({loc["file"] for loc in live}),
            }
        else:
            by_file: dict[str, list[dict]] = {}
            for loc in live:
                by_file.setdefault(loc["file"], []).append(loc)
            for rel_path, locs in by_file.items():
                path = root / rel_path
                prefix, records = load_records(path)
                for loc in locs:
                    if mode == "tombstone":
                        records[loc["index"]] = _tombstone_record(loc["record"], reason)
                    else:
                        records[loc["index"]] = None
                records = [r for r in records if r is not None]
                atomic_write_text(path, render_records(prefix, records))
                modified_files.add(rel_path)
            report["layers"]["live"] = {
                "action": "tombstoned" if mode == "tombstone" else "purged",
                "files": sorted(modified_files),
            }
        # Summary lines quoting the value go with the record.
        summary_hits = before["summary"]
        if not summary_hits:
            report["layers"]["summary"] = {"action": "clean", "files": []}
        else:
            touched: set[str] = set()
            for hit in summary_hits:
                path = Path(hit["file"])
                lines = path.read_text(encoding="utf-8").splitlines()
                n = hit["line_no"] - 1
                if mode == "tombstone":
                    m = _SYNTH_SOURCE_RE.search(lines[n])
                    source = f"（原来源：{m.group(1)} 日档“变”）" if m else ""
                    lines[n] = f"- [已删除：原文已删，摘要随删]{source}"
                else:
                    lines[n] = None
                lines = [x for x in lines if x is not None]
                atomic_write_text(path, "\n".join(lines) + "\n")
                touched.add(str(path))
            report["layers"]["summary"] = {
                "action": "tombstoned" if mode == "tombstone" else "purged",
                "lines": len(summary_hits),
                "files": sorted(touched),
            }
    # Diary: traced only, never auto-modified.
    report["layers"]["diary"] = {
        "action": "traced-only",
        "lines": len(before["diary"]),
        "note": "diary lines are the user's own journal; the operator decides",
    }
    # Index: rebuild from the (modified) files, then re-check.
    db_path = Path(index_db) if index_db else root / "index.db"
    if db_path.exists():
        Index(db_path).build(root)
        report["layers"]["index"] = {
            "action": "rebuilt",
            "active_remaining": _index_has_active(db_path, claim_id),
        }
    else:
        report["layers"]["index"] = {"action": "no-index-file"}
    # Cache: invalidate entries for every modified file.
    cache = DerivedMarkdownCache(root)
    affected = {loc["file"] for loc in live} | {
        str(Path(h["file"]).relative_to(root)) for h in before["summary"]
    }
    invalidated = cache.invalidate(affected)
    report["layers"]["cache"] = {"action": "invalidated", "entries": invalidated}
    # Raw archive: drop the provenance record from its session stream.
    raw = before["raw_archive"]
    if archive_root is None:
        report["layers"]["raw_archive"] = {"action": "not-checked"}
    elif raw.get("status") == "no-provenance-link":
        report["layers"]["raw_archive"] = {"action": "no-provenance-link"}
    elif raw.get("status") == "absent":
        report["layers"]["raw_archive"] = {"action": "absent"}
    else:
        archive_root = Path(archive_root)
        target = raw["provenance_record_id"]
        removed = 0
        for stream in sorted((archive_root / "sessions").glob("*.jsonl.gz")):
            records = list(raw_archive.gz_read(stream))
            kept = [r for r in records if r.get("record_id") != target]
            if len(kept) != len(records):
                raw_archive.gz_write(stream, kept)
                removed += len(records) - len(kept)
        raw_archive.rebuild_manifest(archive_root)
        ledger = raw_archive.read_ledger(archive_root)
        ledger.setdefault("deletions", []).append(
            {
                "claim_id": claim_id,
                "record_id": target,
                "records": removed,
                "mode": mode,
                "at": datetime.now(timezone.utc).isoformat(),
            }
        )
        raw_archive.write_json(archive_root / "ledger.json", ledger)
        report["layers"]["raw_archive"] = {"action": "removed", "records": removed}
    return report
