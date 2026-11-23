"""Deterministic computation module for the hybrid reader prototype.

Design rules (user ruling, 2026-11-23):
  - The module only computes FORWARD from evidence the reader selected.
    It never vetoes an answer by reverse lookup ("the number is not in the
    citation, therefore block") -- that scheme was measured to have no
    selectivity (v5.1) and stays dead.
  - Every operand carries a provenance tag:
        event_date     -- a date stated inside the record's own value text
        session_date   -- only the record `date` field (when the statement
                          was recorded, NOT when the event happened)
        text_extracted -- a number/date extracted from the value text
        question_date  -- the date the question was asked
    Operations that need an EVENT date refuse (insufficient_operands) when
    the operand only has session_date provenance: a session date must never
    be silently treated as an event date (the 189-set trap).
  - On any insufficiency, ambiguity or conflict the module refuses with a
    reason code and produces NO number:
        insufficient_operands / ambiguous_date / conflicting_records /
        no_valid_evidence
  - Every successful computation emits a receipt:
        {record_ids, operands, operation, result}
"""

import re
from datetime import date

from records import is_zh, person_token, record_date

REFUSALS = (
    "insufficient_operands",
    "ambiguous_date",
    "conflicting_records",
    "no_valid_evidence",
)

_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ]
    )
}

_DATE_PATTERNS = [
    re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日"),
    re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})"),
    re.compile(
        r"(January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\s+(\d{1,2}),\s+(\d{4})"
    ),
]


def _mkdate(y, m, d):
    try:
        return date(int(y), int(m), int(d))
    except Exception:
        return None


def text_dates(value):
    """All distinct full calendar dates stated in a value text."""
    out = []
    v = value or ""
    for pat in _DATE_PATTERNS:
        for m in pat.finditer(v):
            if pat is _DATE_PATTERNS[2]:
                d = _mkdate(m.group(3), _MONTHS[m.group(1)], m.group(2))
            else:
                d = _mkdate(m.group(1), m.group(2), m.group(3))
            if d and d not in out:
                out.append(d)
    return out


def event_date(rec):
    """(date, provenance) for a record's event date.

    Exactly one distinct date in the value text -> that date, provenance
    event_date. Several distinct dates -> (None, 'ambiguous_date').
    None in text -> the record (session) date, provenance session_date.
    """
    tds = text_dates(rec.get("value"))
    if len(tds) == 1:
        return tds[0], "event_date"
    if len(tds) > 1:
        return None, "ambiguous_date"
    return record_date(rec), "session_date"


_EXPIRY_ANCHORS = (
    "有效期截止到",
    "有效期至",
    "截止到",
    "valid until",
    "expires on",
    "is valid until",
    "expiry",
)


def expiry_date(rec):
    """Expiry date stated in a validity record, or None.

    Looks for a date at/after an expiry anchor; falls back to the single
    text date when the record's predicate is a validity predicate.
    Provenance of a returned date is text_extracted (decided by caller).
    """
    v = rec.get("value") or ""
    for anchor in _EXPIRY_ANCHORS:
        idx = v.find(anchor)
        if idx >= 0:
            tail = v[idx:]
            tds = text_dates(tail)
            if tds:
                return tds[0]
    if (rec.get("predicate") or "") in ("valid until", "有效期"):
        tds = text_dates(v)
        if len(tds) == 1:
            return tds[0]
    return None


def _refuse(code, detail):
    assert code in REFUSALS
    return {"status": "refused", "reason_code": code, "detail": detail}


def _ok(answer, operation, operands, record_ids):
    return {
        "status": "ok",
        "answer": answer,
        "receipt": {
            "record_ids": record_ids,
            "operands": operands,
            "operation": operation,
            "result": answer,
        },
    }


def _event_operands(records, need):
    """Collect event-date operands from records. Returns (operands, refusal).
    Every operand must have event_date provenance; anything else refuses."""
    ops = []
    for rec in records:
        d, prov = event_date(rec)
        if prov == "ambiguous_date":
            return None, _refuse("ambiguous_date", f"record {rec['id']} states several dates")
        if prov != "event_date" or d is None:
            return None, _refuse(
                "insufficient_operands",
                f"record {rec['id']} has no event date in its text "
                f"(only session_date); refusing to compute on it",
            )
        ops.append({"record_id": rec["id"], "value": d.isoformat(), "provenance": "event_date"})
    if len(ops) < need:
        return None, _refuse(
            "insufficient_operands", f"need {need} event-date operands, got {len(ops)}"
        )
    return ops, None


# ------------------------------------------------------------------ ops
def op_date_gap(records, question, qdate):
    if len(records) != 2:
        return _refuse(
            "insufficient_operands",
            f"date_gap needs exactly 2 cited records, got {len(records)}",
        )
    ops, ref = _event_operands(records, 2)
    if ref:
        return ref
    d1 = date.fromisoformat(ops[0]["value"])
    d2 = date.fromisoformat(ops[1]["value"])
    return _ok(str(abs((d2 - d1).days)), "date_gap_days", ops, [r["id"] for r in records])


_WD_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_WD_ZH = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


def op_weekday(records, question, qdate):
    if len(records) != 1:
        return _refuse(
            "insufficient_operands",
            f"weekday needs exactly 1 cited record, got {len(records)}",
        )
    ops, ref = _event_operands(records, 1)
    if ref:
        return ref
    d = date.fromisoformat(ops[0]["value"])
    name = (_WD_ZH if is_zh(question) else _WD_EN)[d.weekday()]
    return _ok(name, "weekday_of_event_date", ops, [records[0]["id"]])


def op_nth_date(records, question, qdate, n, from_last=False):
    if n is None:
        return _refuse("insufficient_operands", "ordinal not found in question")
    ops, ref = _event_operands(records, 1)
    if ref:
        return ref
    if len(records) < n:
        return _refuse("insufficient_operands", f"only {len(records)} cited occurrences, need {n}")
    ordered = sorted(ops, key=lambda o: o["value"])
    pick = ordered[-n] if from_last else ordered[n - 1]
    op_name = (
        f"nth_from_last_occurrence_date(n={n})" if from_last else f"nth_occurrence_date(n={n})"
    )
    return _ok(pick["value"], op_name, ordered, [o["record_id"] for o in ordered])


def op_countdown(records, question, qdate):
    if qdate is None:
        return _refuse("insufficient_operands", "question_date unparsable")
    if not records:
        return _refuse("no_valid_evidence", "no cited records")
    for rec in records:
        exp = expiry_date(rec)
        if exp is not None:
            days = (exp - qdate).days
            if days < 0:
                return _refuse(
                    "insufficient_operands",
                    f"expiry {exp} precedes question date {qdate}; no positive countdown exists",
                )
            ops = [
                {
                    "record_id": rec["id"],
                    "value": exp.isoformat(),
                    "provenance": "text_extracted",
                },
                {
                    "record_id": None,
                    "value": qdate.isoformat(),
                    "provenance": "question_date",
                },
            ]
            return _ok(str(days), "expiry_countdown_days", ops, [rec["id"]])
    return _refuse("insufficient_operands", "no expiry date extractable from cited records")


def op_count(records, question, qdate):
    if not records:
        return _refuse("no_valid_evidence", "no cited records")
    ops, ref = _event_operands(records, 1)
    if ref:
        return ref
    return _ok(str(len(records)), "count_events", ops, [r["id"] for r in records])


_QTY_PATTERNS = [
    re.compile(r"(\d+)\s*copies"),
    re.compile(r"买了\s*(\d+)\s*本"),
    re.compile(r"(\d+)\s*本"),
    re.compile(r"(?:purchased|bought)\s+(\d+)"),
]


def _quantity(rec):
    v = rec.get("value") or ""
    for pat in _QTY_PATTERNS:
        m = pat.search(v)
        if m:
            return int(m.group(1))
    return None


def op_sum(records, question, qdate):
    if not records:
        return _refuse("no_valid_evidence", "no cited records")
    ops = []
    total = 0
    for rec in records:
        q = _quantity(rec)
        if q is None:
            return _refuse(
                "insufficient_operands",
                f"no quantity extractable from record {rec['id']}",
            )
        total += q
        ops.append({"record_id": rec["id"], "value": q, "provenance": "text_extracted"})
    return _ok(str(total), "sum_quantities", ops, [r["id"] for r in records])


_DIST_PATTERNS = [
    re.compile(r"(\d+(?:\.\d+)?)\s*km"),
    re.compile(r"(\d+(?:\.\d+)?)\s*公里"),
    re.compile(r"跑了\s*(\d+(?:\.\d+)?)"),
]


def _distance(rec):
    v = rec.get("value") or ""
    for pat in _DIST_PATTERNS:
        m = pat.search(v)
        if m:
            return float(m.group(1))
    return None


def _named_persons(question):
    """Persons explicitly named in a 'who is max among ...' question, or
    None when they cannot be enumerated from the question text."""
    q = question or ""
    m = re.search(r"Among\s+(.+?),\s*who", q)
    if m:
        parts = re.split(r",\s*|\s+and\s+", m.group(1))
        names = [p.strip() for p in parts if p.strip()]
        return names or None
    m = re.search(r"^(.+?)三个人里", q) or re.search(r"^(.+?)三人里", q)
    if m:
        names = [p.strip() for p in re.split(r"、", m.group(1)) if p.strip()]
        return names or None
    return None


def op_max(records, question, qdate):
    if not records:
        return _refuse("no_valid_evidence", "no cited records")
    ops = []
    best = None
    tie = False
    for rec in records:
        dist = _distance(rec)
        if dist is None:
            return _refuse(
                "insufficient_operands",
                f"no distance extractable from record {rec['id']}",
            )
        person = person_token(rec.get("value"))
        if person is None:
            return _refuse(
                "insufficient_operands",
                f"no person extractable from record {rec['id']}",
            )
        ops.append(
            {
                "record_id": rec["id"],
                "value": dist,
                "person": person,
                "provenance": "text_extracted",
            }
        )
    named = _named_persons(question)
    if named is not None:
        covered = {o["person"] for o in ops}
        missing = [n for n in named if n not in covered]
        if missing:
            return _refuse(
                "insufficient_operands",
                f"cited set misses named person(s) {missing}; "
                "the evidence set is provably incomplete",
            )
    for o in ops:
        dist = o["value"]
        if best is None or dist > best[0]:
            best = (dist, o["person"])
            tie = False
        elif dist == best[0]:
            tie = True
    if tie:
        return _refuse("conflicting_records", "tie for maximum distance")
    return _ok(best[1], "argmax_distance", ops, [r["id"] for r in records])


# ------------------------------------------------------------- classifier
_CN_NUM = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}

COMPUTE_TYPES = ("date_gap", "weekday", "nth_date", "countdown", "count", "sum", "max")


def parse_ordinal(question):
    n, _ = parse_nth(question)
    return n


def parse_nth(question):
    """(n, from_last) for occurrence questions, (None, False) if absent.
    Handles 第N次 / 倒数第N次 / 'Nth time' / 'Nth from last'."""
    q = question or ""
    m = re.search(r"倒数第(\d+)次", q)
    if m:
        return int(m.group(1)), True
    m = re.search(r"倒数第([一二两三四五六七八九十])次", q)
    if m:
        return _CN_NUM[m.group(1)], True
    m = re.search(r"(\d+)(?:st|nd|rd|th)\s+from\s+(?:the\s+)?last", q)
    if m:
        return int(m.group(1)), True
    m = re.search(r"第(\d+)次", q)
    if m:
        return int(m.group(1)), False
    m = re.search(r"第([一二两三四五六七八九十])次", q)
    if m:
        return _CN_NUM[m.group(1)], False
    m = re.search(r"(\d+)(?:st|nd|rd|th)\s+time", q)
    if m:
        return int(m.group(1)), False
    return None, False


def classify_question(question):
    """Deterministic question-type routing (fallback/cross-check for the
    reader's own qtype). Returns (qtype, params)."""
    q = question or ""
    ICASE = re.IGNORECASE
    if re.search(
        r"还有多少天到期|多少天到期|几天到期|days are left before|"
        r"days left before|days until .* expir",
        q,
        ICASE,
    ):
        return "countdown", {}
    if re.search(r"星期几|day of the week", q, ICASE):
        return "weekday", {}
    n, from_last = parse_nth(q)
    if n is not None and re.search(r"哪一天|what date|on what date", q, ICASE):
        return "nth_date", {"n": n, "from_last": from_last}
    if re.search(
        r"相隔多少天|间隔多少天|相差多少天|how many days apart|"
        r"days apart|how many days between",
        q,
        ICASE,
    ):
        return "date_gap", {}
    if re.search(
        r"参加了几次|how many times did .* attend|"
        r"how many times did .* take part|how many times",
        q,
        ICASE,
    ):
        return "count", {}
    if re.search(r"how many copies|一共买了多少本|总共买了多少本|买了多少本", q, ICASE):
        return "sum", {}
    if re.search(r"跑得最远|ran the farthest|who .* farthest|谁.*最远", q, ICASE):
        return "max", {}
    return "lookup", {}


_OPS = {
    "date_gap": op_date_gap,
    "weekday": op_weekday,
    "countdown": op_countdown,
    "count": op_count,
    "sum": op_sum,
    "max": op_max,
}


def compute(qtype, question, cited_records, qdate):
    """Run the deterministic operation for qtype over the reader's cited
    records (deduplicated by id, order preserved)."""
    seen, records = set(), []
    for rec in cited_records:
        if rec["id"] not in seen:
            seen.add(rec["id"])
            records.append(rec)
    if qtype == "nth_date":
        n, from_last = parse_nth(question)
        return op_nth_date(records, question, qdate, n, from_last)
    fn = _OPS.get(qtype)
    if fn is None:
        return _refuse("no_valid_evidence", f"no deterministic op for {qtype}")
    return fn(records, question, qdate)
