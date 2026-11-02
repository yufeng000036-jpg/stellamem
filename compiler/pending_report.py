"""Dry-run pending screening report. Read-only by construction.

This function takes record dicts and returns counts; it never writes,
never changes a status, never activates a pending record, and never
deletes anything. The caller (local agent, staging only) is responsible
for feeding it records read from a staging copy -- this module itself
opens no memory directory, no index, and no production path.

Report fields:
- total_pending: pending records seen
- by_kind: pending counts grouped by kind
- by_status: counts for every status seen (so a non-pending record
  accidentally fed in is visible, not silently dropped from the total)
- duplicate_id_groups: claim_id values appearing >1 time among pending
  records, with their row counts (the inbox duplicate pattern)
- oldest / newest: min/max observed_at-or-occurred_at among pending
"""

from __future__ import annotations

from collections import Counter


def _when(rec: dict):
    return rec.get("observed_at") or rec.get("occurred_at") or ""


def screening_report(records) -> dict:
    records = list(records or [])
    pending = [r for r in records if r.get("status") == "pending"]
    by_kind = dict(Counter(str(r.get("kind") or "") for r in pending))
    by_status = dict(Counter(str(r.get("status") or "") for r in records))
    id_counts = Counter(str(r.get("claim_id") or "") for r in pending)
    dup_groups = [
        {"claim_id": cid, "rows": n} for cid, n in sorted(id_counts.items()) if n > 1 and cid
    ]
    whens = sorted(w for w in (_when(r) for r in pending) if w)
    return {
        "total_records_seen": len(records),
        "total_pending": len(pending),
        "by_kind": by_kind,
        "by_status": by_status,
        "duplicate_id_groups": dup_groups,
        "duplicate_id_group_count": len(dup_groups),
        "duplicate_extra_rows": sum(g["rows"] - 1 for g in dup_groups),
        "oldest": whens[0] if whens else None,
        "newest": whens[-1] if whens else None,
        "changed_anything": False,
        "activated_anything": False,
    }


def render_text(report: dict) -> str:
    lines = [
        f"pending: {report['total_pending']} / seen: {report['total_records_seen']}",
        f"by_kind: {report['by_kind']}",
        f"by_status: {report['by_status']}",
        f"duplicate_id_groups: {report['duplicate_id_group_count']} "
        f"(extra rows: {report['duplicate_extra_rows']})",
        f"oldest: {report['oldest']}  newest: {report['newest']}",
        "changed_anything: False  activated_anything: False",
    ]
    return "\n".join(lines)
