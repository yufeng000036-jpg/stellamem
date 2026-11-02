"""Legacy subject -> namespace tagging plan (PATCH2). DRY-RUN ONLY.

The governance boundary needs a `namespace` field (user / persona / viewer)
on every record. Existing records only have `subject`, which was often a
free-text person/topic name, so it cannot be treated as a namespace.

This module computes a tagging PLAN and never writes: it does not open the
memory directory, the index, or any production path, and nothing here does
an UPDATE/INSERT. The caller may feed it records read from a staging copy
and inspect the report; actually writing tags is a separate, explicitly
approved step (not implemented in this round).

Rules (PATCH2):
- namespace present -> keep it (it is authoritative, see subjects.subject_of);
- subject already a namespace/alias -> namespace = that namespace;
- subject is persona/人格 or viewer/观众 -> ``manual_hold``: a human decides,
  because those two namespaces are the restricted side of the boundary;
- everything else (free-text subject, empty, missing) -> namespace = "user"
  (the owner's personal memory). This is the PATCH2 default for legacy data.
- ``subject`` is never modified.
"""

from __future__ import annotations

import hashlib
import json

from subjects import ALIASES, SUBJECTS, USER, subject_of

DEFAULT_NAMESPACE = USER
MANUAL_HOLD = "manual_hold"


def _status(record: dict) -> str:
    return str(record.get("status") or "")


def classify_record(record: dict) -> tuple[str | None, str]:
    """Return (namespace, action).

    action is one of:
    - "keep"        already carries a valid namespace, nothing to do;
    - "set"         plan to add a namespace (dry-run);
    - "manual_hold" restricted namespace found under a legacy spelling,
                    a human must handle it.
    """
    if not isinstance(record, dict):
        return None, MANUAL_HOLD
    if "namespace" in record and record.get("namespace") is not None:
        return subject_of(record), "keep"
    value = record.get("subject")
    if value in SUBJECTS:
        return value, "keep"
    if value in ALIASES:
        canonical = ALIASES[value]
        if canonical in (USER,):
            return canonical, "set"
        return canonical, MANUAL_HOLD
    return DEFAULT_NAMESPACE, "set"


def plan_legacy_tags(records) -> dict:
    """Build the dry-run tagging plan. Pure function, mutates nothing."""
    records = list(records or [])
    by_action = {"keep": 0, "set": 0, MANUAL_HOLD: 0}
    by_namespace = {name: 0 for name in SUBJECTS}
    by_status = {}
    entries = []
    holds = []
    for rec in records:
        namespace, action = classify_record(rec)
        by_action[action] = by_action.get(action, 0) + 1
        if namespace in by_namespace:
            by_namespace[namespace] += 1
        status = _status(rec)
        by_status[status] = by_status.get(status, 0) + 1
        entry = {
            "claim_id": rec.get("claim_id") or rec.get("record_id") or rec.get("id"),
            "from_subject": rec.get("subject"),
            "to_namespace": namespace,
            "action": action,
            "status": status,
        }
        entries.append(entry)
        if action == MANUAL_HOLD:
            holds.append(entry)
    plan = sorted((e["claim_id"], e["to_namespace"]) for e in entries if e["action"] == "set")
    digest = hashlib.sha256(
        json.dumps(plan, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "total_records": len(records),
        "by_action": by_action,
        "by_namespace": by_namespace,
        "by_status": by_status,
        "manual_hold_count": len(holds),
        "manual_hold": holds,
        "plan_size": len(plan),
        "plan_sha256": digest,
        "changed_anything": False,
        "wrote_anything": False,
    }


def tagged_records(records) -> list[dict]:
    """In-memory copies carrying the planned namespace (verification only).

    This is the only "application" in this module and it is deliberately
    memory-only: input dicts are never mutated and nothing is persisted.
    """
    out = []
    for rec in records or []:
        namespace, action = classify_record(rec)
        copy = dict(rec)
        if action == "set":
            copy["namespace"] = namespace
        out.append(copy)
    return out
