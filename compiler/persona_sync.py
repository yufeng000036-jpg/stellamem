"""Persona sync preparation (Step 3, plan + approval record only).

This module NEVER writes to the user library. It takes the reviewed
events returned by ``PersonaStore.export_pack()`` (reviewed events only)
and builds an approved sync *candidate pack* that a separate, explicitly
approved job may consume. The actual write into the user library is
out of scope here and is not implemented.

Rules, mirroring the bridge's default-deny stance:
- Only reviewed events are candidates. Pending or merely confirmed
  events are refused, not silently promoted.
- Every candidate must carry reviewed_by and reviewed_at.
- Approval requires a non-empty approver AND confirmed_full_text=True
  (the approver has seen the full candidate text).
- Batch approval is all-or-nothing: any invalid candidate aborts the
  whole batch and no candidate is marked approved.
- Duplicate event_id in one batch is refused.
- The returned pack is data, not a write: changed_anything is False
  and wrote_anything is False. A consumer that writes must record its
  own approval separately.
"""

from __future__ import annotations


def _full_text(event: dict) -> str:
    return str(event.get("text") or "").strip()


def validate_candidate(event: dict) -> list[str]:
    """Return a list of validation problems (empty = valid)."""
    problems = []
    if not isinstance(event, dict):
        return ["not a dict"]
    if event.get("status") != "reviewed":
        problems.append(f"status is {event.get('status')!r}, not 'reviewed'")
    if event.get("subject") != "persona":
        problems.append(f"subject is {event.get('subject')!r}, not 'persona'")
    if not _full_text(event):
        problems.append("empty text")
    if not str(event.get("reviewed_by") or "").strip():
        problems.append("missing reviewed_by")
    if not str(event.get("reviewed_at") or "").strip():
        problems.append("missing reviewed_at")
    if not str(event.get("event_id") or "").strip():
        problems.append("missing event_id")
    return problems


def prepare_sync_pack(events, approver: str, confirmed_full_text: bool = False) -> dict:
    """Validate and approve a sync candidate pack (no writes).

    Returns a pack dict with status 'approved' and the candidate list,
    or raises ValueError/KeyError before anything is marked approved.
    """
    if not approver or not str(approver).strip():
        raise ValueError("approver is required")
    if not confirmed_full_text:
        raise ValueError("approval requires confirmed_full_text=True (full text seen)")
    events = list(events or [])
    seen = set()
    candidates = []
    for event in events:
        event_id = str(event.get("event_id") or "").strip() if isinstance(event, dict) else ""
        if event_id in seen:
            raise ValueError(f"sync pack aborted: duplicate event_id {event_id!r}")
        seen.add(event_id)
        problems = validate_candidate(event)
        if problems:
            raise ValueError(f"sync pack aborted: event {event_id!r}: {'; '.join(problems)}")
        candidates.append({
            "event_id": event_id,
            "subject": "persona",
            "text": _full_text(event),
            "reviewed_by": str(event["reviewed_by"]).strip(),
            "reviewed_at": str(event["reviewed_at"]).strip(),
            "source": str(event.get("source") or "").strip(),
        })
    return {
        "status": "approved",
        "approver": str(approver).strip(),
        "candidate_count": len(candidates),
        "candidates": candidates,
        "changed_anything": False,
        "wrote_anything": False,
        "user_library_write_implemented": False,
    }


def dry_run_report(events) -> dict:
    """Counts only; never approves, never writes."""
    events = list(events or [])
    by_status = {}
    valid = 0
    for event in events:
        status = str(event.get("status") or "") if isinstance(event, dict) else ""
        by_status[status] = by_status.get(status, 0) + 1
        if not validate_candidate(event):
            valid += 1
    return {
        "total_events": len(events),
        "by_status": by_status,
        "reviewed_and_valid": valid,
        "changed_anything": False,
        "wrote_anything": False,
    }
