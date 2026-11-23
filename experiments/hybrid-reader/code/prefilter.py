"""Corrected validity prefilter (user细则, 2026-11-23).

Rules -- deliberately narrower than the offline measurement's text rules:
  R1 marked_expired : a record whose own value states it has expired /
                      been invalidated is removed. Current-state questions
                      only.
  R2 correction     : an explicit correction record (predicate
                      'correction'/'更正' or a value opening with
                      'Correction:'/'更正：') is an authoritative version
                      relation: same-scope, same-person records dated on
                      or before the correction are removed (the correction
                      record itself stays). Applies to both stances -- a
                      correction says the earlier record was WRONG, not
                      merely outdated.
  R3 validity_join  : a credential code record paired with a validity
                      record (same scope, same person, same record date)
                      whose stated expiry precedes the question date is
                      removed; the validity record itself stays (it holds
                      no answer value). Current-state questions only.

Explicitly NOT implemented (user ruling):
  - No "same key, keep latest by date" supersession. A later date alone
    never proves replacement; unmarked same-key chains stay whole and the
    reader decides.
  - Nothing is removed for historical-state questions except R2
    corrections, and nothing is removed when person/scope pairing is
    ambiguous -- conservative retention is the default.
"""

import re

from det_module import expiry_date
from records import person_token, record_date

_EXPIRED_MARKERS = (
    "已过期",
    "已失效",
    "已作废",
    "此记录已过期",
    "no longer valid",
    "has expired",
    "is expired",
)
_HISTORICAL_MARKERS = (
    "当时",
    "之前",
    "起初",
    "曾经",
    "原本",
    "used to",
    "at that time",
    "originally",
    "back then",
    "previously",
)
_CODE_PREDICATES = ("code number", "编号")
_VALIDITY_PREDICATES = ("valid until", "有效期")
_CODE_VALUE_RE = re.compile(r"[A-Z]{2}-\d{3,5}")


def question_stance(question):
    q = question or ""
    return "historical" if any(m in q for m in _HISTORICAL_MARKERS) else "current"


def _is_correction(rec):
    if (rec.get("predicate") or "") in ("correction", "更正"):
        return True
    v = (rec.get("value") or "").lstrip()
    return v.startswith("Correction:") or v.startswith("更正：")


def _is_code(rec):
    if (rec.get("predicate") or "") in _CODE_PREDICATES:
        return True
    return bool(_CODE_VALUE_RE.search(rec.get("value") or "")) and (
        "number" in (rec.get("value") or "") or "编号" in (rec.get("value") or "")
    )


def _is_validity(rec):
    return (rec.get("predicate") or "") in _VALIDITY_PREDICATES


def prefilter(records, question, qdate):
    """records: list of record dicts (one question's context).
    Returns (kept_records, removals) where removals are
    {id, rule, reason}. Order of kept records is preserved."""
    stance = question_stance(question)
    removed = {}

    def drop(rec, rule, reason):
        if rec["id"] not in removed:
            removed[rec["id"]] = {"id": rec["id"], "rule": rule, "reason": reason}

    # R1: self-marked expired (current stance only)
    if stance == "current":
        for rec in records:
            v = rec.get("value") or ""
            if any(m in v for m in _EXPIRED_MARKERS):
                drop(
                    rec, "R1_marked_expired", "record's own value states it has expired"
                )

    # R2: explicit correction supersedes earlier same-scope same-person
    for corr in [r for r in records if _is_correction(r)]:
        person = person_token(corr.get("value"))
        cdate = record_date(corr)
        if person is None or cdate is None:
            continue  # cannot establish the relation -> keep everything
        for rec in records:
            if rec["id"] == corr["id"] or rec["id"] in removed:
                continue
            if rec.get("scope") != corr.get("scope"):
                continue
            if person_token(rec.get("value")) != person:
                continue
            rdate = record_date(rec)
            if rdate is not None and rdate <= cdate:
                drop(
                    rec,
                    "R2_correction",
                    f"superseded by explicit correction {corr['id']}",
                )

    # R3: credential validity join (current stance only)
    if stance == "current" and qdate is not None:
        codes = [r for r in records if _is_code(r) and r["id"] not in removed]
        valids = [r for r in records if _is_validity(r)]
        for code in codes:
            person = person_token(code.get("value"))
            cdate = record_date(code)
            if person is None or cdate is None:
                continue
            pairs = [
                v
                for v in valids
                if v.get("scope") == code.get("scope")
                and person_token(v.get("value")) == person
                and record_date(v) == cdate
            ]
            if len(pairs) != 1:
                continue  # ambiguous or unpaired -> conservative keep
            exp = expiry_date(pairs[0])
            if exp is not None and exp < qdate:
                drop(
                    code,
                    "R3_validity_join",
                    f"paired validity record {pairs[0]['id']} expired "
                    f"{exp.isoformat()} before question date "
                    f"{qdate.isoformat()}",
                )

    kept = [r for r in records if r["id"] not in removed]
    return kept, list(removed.values())
