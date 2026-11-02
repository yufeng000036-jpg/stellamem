"""Dual-time model (Step 7, part 1): fact time + write time.

A memory fact has two independent times, and confusing them is a
classic source of wrong recall:
- fact time: when the fact was true (valid_from / valid_to). A fact
  can be superseded in fact time while its old version stays in the
  version chain.
- write time: when the fact was recorded (recorded_at). This is the
  audit trail: it never changes when the fact time is corrected.

Functions here are pure and operate on record dicts (synthetic in
tests). They do not open any store and do not write anything.

- as_of_fact(records, at): facts whose fact-time interval covers ``at``
  (valid_from <= at, and valid_to is None or at < valid_to).
- as_of_recorded(records, at): facts recorded at or before ``at``.
- supersede(records, old_id, new_record): returns a new list (inputs
  untouched) where the old record gets valid_to set to the new
  record's valid_from, and the new record is appended. The old
  version is kept, not deleted.
"""

from __future__ import annotations

from datetime import datetime


def _parse(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def as_of_fact(records, at) -> list[dict]:
    moment = _parse(at)
    out = []
    for rec in records or []:
        start = _parse(rec.get("valid_from"))
        end = _parse(rec.get("valid_to"))
        if start is not None and moment < start:
            continue
        if end is not None and moment >= end:
            continue
        out.append(rec)
    return out


def as_of_recorded(records, at) -> list[dict]:
    moment = _parse(at)
    out = []
    for rec in records or []:
        recorded = _parse(rec.get("recorded_at"))
        if recorded is not None and recorded <= moment:
            out.append(rec)
    return out


def supersede(records, old_id: str, new_record: dict) -> list[dict]:
    """Return a new list with old_id fact-time-closed at new valid_from."""
    if not new_record or not isinstance(new_record, dict):
        raise ValueError("new_record is required")
    new_from = _parse(new_record.get("valid_from"))
    if new_from is None:
        raise ValueError("new_record requires valid_from")
    found = False
    out = []
    for rec in records or []:
        copy = dict(rec)
        if copy.get("claim_id") == old_id or copy.get("id") == old_id:
            copy["valid_to"] = new_record["valid_from"]
            found = True
        out.append(copy)
    if not found:
        raise KeyError(old_id)
    out.append(dict(new_record))
    return out
