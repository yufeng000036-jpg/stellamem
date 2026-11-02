"""Subject namespaces: one governance core, separate subjects.

This is the fusion boundary for the two projects:
- user: the owner's personal memory (personal assistant only);
- persona: the Douyin livestream persona's memory (livestream decider only);
- viewer: individual viewer context (livestream context only, most restricted).

Rules, enforced here and nowhere else by accident:
1. Default retrieval is same-subject only. A requester for "user" never
   sees "persona" records, and vice versa.
2. Contents are NEVER merged. A record keeps its own subject; a bridge
   creates a separate copy record in the target subject (see bridge.py),
   and that copy is what the target subject retrieves. The original
   stays in its own subject and stays invisible to the other subject.
3. The existing record "subject" field is the namespace for records
   created through this module. Legacy records whose subject is a
   free-text person/topic name (not one of the namespaces, and not a
   known alias) are "unclassified" in this module: they are returned to
   NO subject by recall_for_subject(). That is deliberate fail-closed
   behaviour -- silently treating a person name as a namespace would
   merge exactly the things this module exists to keep apart. Such
   records must be explicitly tagged before they enter this boundary.
   This module does NOT rewrite the existing search/recall paths;
   it is the new boundary for fused use, not a silent change to old reads.
"""

from __future__ import annotations

USER = "user"
PERSONA = "persona"
VIEWER = "viewer"

SUBJECTS = (USER, PERSONA, VIEWER)

# Legacy aliases only: the old codebase used the Chinese word for the
# owner as a subject value. Mapping it is not merging contents; it is
# recognising the same namespace under its old spelling.
ALIASES = {
    "用户": USER,
    "人格": PERSONA,
    "观众": VIEWER,
}


def validate_subject(subject) -> str:
    """Return the canonical namespace, or raise ValueError."""
    if subject in SUBJECTS:
        return subject
    if subject in ALIASES:
        return ALIASES[subject]
    raise ValueError(f"unknown subject namespace: {subject!r} (expected one of {SUBJECTS})")


def subject_of(record: dict) -> str | None:
    """Canonical namespace of a record, or None if unclassified.

    PATCH2: `namespace` is a NEW field, added without touching the legacy
    `subject` field. When `namespace` is present it is authoritative: a
    present-but-invalid namespace makes the record unclassified (fail
    closed) instead of silently falling back to `subject` -- the old
    fallback could turn a mis-typed namespace into a different subject's
    visibility (audit finding D13). `subject` is only consulted when the
    record has no `namespace` key at all (legacy records).
    """
    if not isinstance(record, dict):
        return None
    if "namespace" in record and record.get("namespace") is not None:
        value = record.get("namespace")
        if value in SUBJECTS:
            return value
        if value in ALIASES:
            return ALIASES[value]
        return None
    value = record.get("subject")
    if value in SUBJECTS:
        return value
    if value in ALIASES:
        return ALIASES[value]
    return None


def is_bridge_copy(record: dict) -> bool:
    return bool(record.get("bridge_id")) and bool(record.get("origin_id"))


def has_bridge_copy(records, origin_id: str, target_subject: str) -> bool:
    target = validate_subject(target_subject)
    for rec in records or []:
        if is_bridge_copy(rec) and rec.get("origin_id") == origin_id and subject_of(rec) == target:
            return True
    return False


def can_retrieve(record: dict, requester_subject: str) -> bool:
    """Same-subject only. Bridge copies are ordinary target-subject records."""
    try:
        requester = validate_subject(requester_subject)
    except ValueError:
        return False
    return subject_of(record) == requester


def recall_for_subject(records, requester_subject: str, kinds=None) -> list[dict]:
    """Return only records the requester subject may see.

    Cross-subject originals are excluded even if they are in the input
    list. An approved bridge copy appears here only because its own
    subject IS the requester subject -- the original is still excluded.
    Unclassified records are excluded from every subject.
    """
    requester = validate_subject(requester_subject)
    kinds = (
        None if kinds is None else (kinds if isinstance(kinds, (list, tuple, set)) else (kinds,))
    )
    out = []
    for rec in records or []:
        if subject_of(rec) != requester:
            continue
        if kinds is not None and rec.get("kind") not in kinds:
            continue
        out.append(rec)
    return out
