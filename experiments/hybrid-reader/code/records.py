"""Shared record/context helpers for the hybrid prototype.

A context is the Stellamem-style memory block: a header line followed by one
JSON record per line. Records carry exactly six fields in eval2:
id / scope / source / predicate / value / date. The record `date` field is
the session (recording) date; an event date exists only when the value text
itself states one. Keeping those two apart is the point of det_module's
provenance tags.
"""

import json
import re
from datetime import date

HEADER_PREFIX = "Stellamem task-relevant memory"


def parse_context(context):
    """Return (header_line, [(raw_line, record_dict), ...]) preserving order
    and the original line text (so filtered contexts stay byte-identical for
    kept records)."""
    lines = (context or "").split("\n")
    header = lines[0] if lines else ""
    out = []
    for line in lines[1:]:
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except Exception:
            continue
        if isinstance(obj, dict) and obj.get("id"):
            out.append((line, obj))
    return header, out


def build_context(header, kept_pairs):
    return "\n".join([header] + [raw for raw, _ in kept_pairs])


def record_date(rec):
    """Session date from the record `date` field (ISO timestamp)."""
    d = (rec.get("date") or "")[:10]
    try:
        y, m, dd = (int(x) for x in d.split("-"))
        return date(y, m, dd)
    except Exception:
        return None


def parse_question_date(qd):
    """'2024/08/09 (Fri) 10:00' -> date(2024, 8, 9)."""
    m = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", qd or "")
    if not m:
        return None
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def is_zh(text):
    return any("\u4e00" <= ch <= "\u9fff" for ch in (text or ""))


_EN_NAME = re.compile(r"([A-Z][a-z]+(?: [A-Z][a-z]+)+)")
_ZH_STOP = set("的把在于是养最图泳合围晨担喜通骑参去买放收持")


def person_token(value):
    """Best-effort person name stated in a record value. Used ONLY as a
    grouping key inside one question's context (prefilter pairing), never
    as an answer. English: first Capitalized multi-word name. Chinese:
    leading CJK run up to a grammatical stop character."""
    v = (value or "").strip()
    for pfx in ("Correction:", "更正：", "更正:"):
        if v.startswith(pfx):
            v = v[len(pfx) :].strip()
            break
    m = _EN_NAME.search(v)
    if m:
        return m.group(1)
    if v and "\u4e00" <= v[0] <= "\u9fff":
        out = []
        for ch in v:
            if ch in _ZH_STOP or not ("\u4e00" <= ch <= "\u9fff"):
                break
            out.append(ch)
        if 2 <= len(out) <= 4:
            return "".join(out)
    return None
