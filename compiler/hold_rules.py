"""Hold rules for namespace tagging (Step 2, dry-run only).

This module does NOT write anything. It takes records (dicts) and the
dry-run plan produced by ``legacy_tags.plan_legacy_tags`` and decides
which planned rows must be held for human sampling before any real
tagging write. Writing tags to real data is a separate, explicitly
approved step and is NOT implemented here.

The two hold families come from the PATCH2 open questions:
1. Role/system free-text rows: records whose role is ``assistant`` or
   ``system`` (or whose source/role field says so) were not written by
   the user in the usual sense, so tagging them ``user`` automatically
   would be wrong. Hold them.
2. Persona/viewer wording rows: records whose subject/text/value
   contains persona- or viewer-related wording, but whose namespace
   is not persona/viewer (e.g. a record *about* the persona, not *from*
   the persona). Tagging them ``user`` automatically would hide a
   boundary case. Hold them for sampling.

A held row is not rejected and not tagged: it stays in ``hold`` with a
reason, and a deterministic sample (sorted by claim_id, first N) is
proposed for human review. Everything else keeps its planned action.

All inputs here are synthetic in tests; no production path is opened.
"""

from __future__ import annotations

ROLE_SYSTEM_VALUES = ("assistant", "system")

# Wording that suggests a persona/viewer boundary case. These are generic
# category words, not personal data.
PERSONA_VIEWER_WORDS = (
    "人格",
    "观众",
    "直播",
    "persona",
    "viewer",
    "鲸鱼",
)

DEFAULT_SAMPLE_SIZE = 10


def _text_of(record: dict) -> str:
    parts = []
    for key in ("subject", "text", "value", "predicate"):
        value = record.get(key)
        if isinstance(value, str):
            parts.append(value)
    return " ".join(parts)


def _is_role_system(record: dict) -> bool:
    for key in ("role", "source_role", "speaker_role"):
        value = record.get(key)
        if isinstance(value, str) and value.strip().lower() in ROLE_SYSTEM_VALUES:
            return True
    # A free-text role field that literally names the role also counts.
    role_text = record.get("role_text")
    if isinstance(role_text, str) and role_text.strip().lower() in ROLE_SYSTEM_VALUES:
        return True
    return False


def _has_persona_viewer_wording(record: dict, planned_namespace) -> bool:
    if planned_namespace in ("persona", "viewer"):
        return False
    text = _text_of(record).lower()
    return any(word.lower() in text for word in PERSONA_VIEWER_WORDS)


def hold_reasons(record: dict, planned_namespace) -> list[str]:
    """Return hold reasons for one record (empty list = no hold)."""
    reasons = []
    if _is_role_system(record):
        reasons.append("role_system")
    if _has_persona_viewer_wording(record, planned_namespace):
        reasons.append("persona_viewer_wording")
    return reasons


def hold_report(records, plan: dict, sample_size: int = DEFAULT_SAMPLE_SIZE) -> dict:
    """Build a hold report from records + a legacy_tags plan.

    ``plan`` is expected to be the dict returned by
    ``legacy_tags.plan_legacy_tags`` (its entries are not re-derived
    here; we only join on claim_id via the original records' order).
    In practice callers pass the same record list used to build the
    plan, so entry order matches record order.
    """
    records = list(records or [])
    if sample_size is not None and sample_size < 0:
        raise ValueError("sample_size must be >= 0")
    # Map claim_id -> planned namespace, from the plan's set entries
    # is not stored in the plan dict, so we re-derive the planned
    # namespace with the same pure function legacy_tags uses.
    try:
        from legacy_tags import classify_record
    except Exception:  # pragma: no cover
        try:
            from compiler.legacy_tags import classify_record
        except Exception:
            classify_record = None  # type: ignore
    holds = []
    for record in records:
        if classify_record is not None:
            namespace, _action = classify_record(record)
        else:
            namespace = record.get("namespace")
        reasons = hold_reasons(record, namespace)
        if not reasons:
            continue
        claim_id = record.get("claim_id") or record.get("record_id") or record.get("id")
        holds.append(
            {
                "claim_id": claim_id,
                "from_subject": record.get("subject"),
                "to_namespace": namespace,
                "reasons": reasons,
                "action": "hold",
            }
        )
    holds_sorted = sorted(holds, key=lambda h: str(h["claim_id"] or ""))
    if sample_size is None:
        sample = holds_sorted
    else:
        sample = holds_sorted[:sample_size]
    by_reason = {}
    for hold in holds:
        for reason in hold["reasons"]:
            by_reason[reason] = by_reason.get(reason, 0) + 1
    return {
        "total_records": len(records),
        "hold_count": len(holds),
        "by_reason": by_reason,
        "holds": holds_sorted,
        "sample": sample,
        "sample_size": len(sample),
        "changed_anything": False,
        "wrote_anything": False,
        "tagging_write_implemented": False,
    }
