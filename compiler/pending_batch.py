"""Pending batch plan + approval (Step 6, plan only, no activation).

This module plans a batch keep/task/drop decision over pending
records and records a batch approval. It NEVER activates, deletes,
or changes the status of any record: applying a plan to a real store
is a separate, explicitly approved job and is not implemented here.

Rules:
- Only records with status 'pending' may be included.
- Action must be one of keep / task / drop.
- Duplicate claim_id in one batch is refused; unknown claim_id is
  refused; a record not in the input set is refused.
- Batch approval is all-or-nothing: any invalid item aborts the
  whole batch and no item is marked approved.
- Approval requires a non-empty approver AND confirmed=True (the
  approver has seen the full batch list).
- The returned plan is data: changed_anything and activated_anything
  are both False.
"""

from __future__ import annotations

ACTIONS = ("keep", "task", "drop")


def plan_batch(
    records, actions: dict, approver: str = "", confirmed: bool = False, approve: bool = False
) -> dict:
    """Build (and optionally approve) a pending batch plan. No writes."""
    records = list(records or [])
    by_id = {}
    for rec in records:
        cid = str(rec.get("claim_id") or rec.get("record_id") or rec.get("id") or "")
        if cid:
            by_id[cid] = rec
    items = []
    seen = set()
    for cid, action in (actions or {}).items():
        cid = str(cid)
        if cid in seen:
            raise ValueError(f"batch aborted: duplicate claim_id {cid!r}")
        seen.add(cid)
        rec = by_id.get(cid)
        if rec is None:
            raise ValueError(f"batch aborted: unknown claim_id {cid!r}")
        if rec.get("status") != "pending":
            raise ValueError(
                f"batch aborted: {cid!r} status is {rec.get('status')!r}, not 'pending'"
            )
        if action not in ACTIONS:
            raise ValueError(f"batch aborted: {cid!r} action {action!r} not in {ACTIONS}")
        items.append({"claim_id": cid, "action": action, "kind": rec.get("kind")})
    by_action = {a: 0 for a in ACTIONS}
    for item in items:
        by_action[item["action"]] += 1
    status = "planned"
    if approve:
        if not approver or not str(approver).strip():
            raise ValueError("batch approval requires a non-empty approver")
        if not confirmed:
            raise ValueError("batch approval requires confirmed=True (full list seen)")
        status = "approved"
    return {
        "status": status,
        "approver": str(approver).strip() if approve else None,
        "item_count": len(items),
        "by_action": by_action,
        "items": sorted(items, key=lambda i: i["claim_id"]),
        "changed_anything": False,
        "activated_anything": False,
        "apply_implemented": False,
    }


def render_summary(plan: dict) -> str:
    return (
        f"pending batch: {plan['item_count']} items, status={plan['status']}, "
        f"by_action={plan['by_action']}, "
        "changed_anything: False  activated_anything: False"
    )
