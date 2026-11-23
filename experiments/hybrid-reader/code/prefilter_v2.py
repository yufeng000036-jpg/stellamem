"""Prefilter v2 (V6.2 stage 3 tightening of the V6 corrected prefilter).

Changes vs prefilter.py (v1), all driven by the V6.2 ruling and the
independent-verification finding on E0076:

  1. Context-assisted person resolution. v1 extracted a person token from
     each value in isolation; for Chinese values the name is glued to the
     following activity text (e.g. a correction value '更正：谭景行园艺修剪
     去听涛泳池' yields a 7-char leading run -> None), so R2 pairing failed
     and the filter conservatively kept everything. v2 first collects the
     candidate persons that DO extract cleanly from the question's own
     context, then resolves a glued value by unique prefix match against
     those candidates. Ambiguous (zero or several) prefix matches resolve
     to None -> conservative retention, as before.

  2. R2 removes only records that CONFLICT with the correction. v1 removed
     every earlier same-scope same-person record; that batch-revokes by
     date, which V6.2 forbids. v2 extracts the fact the correction asserts
     (the corrected value stated in the correction record itself) and
     removes only earlier same-scope same-person records whose value does
     NOT contain the asserted value. Records that agree with the
     correction stay (they are consistent evidence, and the correction
     record itself stays too). If the asserted value cannot be isolated
     (the correction merely flags an error without stating the corrected
     fact), the correction's scope is unclear -> remove nothing.

  3. R2 never targets validity records (a correction of a fact value is
     not a revocation of a validity statement) and never crosses scope.

Unchanged from v1 (user细则):
  - R1 self-marked expired, current-state questions only.
  - R3 credential/validity join, current-state questions only, pairing
    must be unique.
  - NO 'same key keep latest by date' supersession anywhere.
  - The filter only changes the evidence VIEW for one question; it never
    deletes or edits memory data.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
_CORRECTION_PREDICATES = ("correction", "更正")
_BOILERPLATE = (
    "此前记录有误",
    "记录有误",
    "有误",
    "此前的记录有误",
    "the earlier record was wrong",
    "earlier record was wrong",
    "was wrong",
    "is wrong",
    "was incorrect",
    "is incorrect",
)
_ZH_FUNCTION = set("去在是的就把被")


def question_stance(question):
    q = question or ""
    return "historical" if any(m in q for m in _HISTORICAL_MARKERS) else "current"


def _is_correction(rec):
    if (rec.get("predicate") or "") in _CORRECTION_PREDICATES:
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


def _strip_correction_prefix(value):
    v = (value or "").strip()
    for pfx in ("Correction:", "更正：", "更正:"):
        if v.startswith(pfx):
            return v[len(pfx) :].strip()
    return v


def _strip_scope(value, scope):
    """Remove the record's own scope string (attribute/activity name --
    known metadata, not answer data) so a glued Chinese name+activity
    value exposes the name boundary: '谭景行园艺修剪去听涛泳池' with scope
    '园艺修剪' -> '谭景行去听涛泳池'."""
    v = _strip_correction_prefix(value)
    if scope:
        v = v.replace(scope, "", 1)
    return v


def candidate_persons(records):
    """Persons that extract cleanly from at least one record in this
    question's context (plain or scope-stripped extraction). Used only as
    pairing keys inside the context."""
    out = set()
    for rec in records:
        for v in (rec.get("value"), _strip_scope(rec.get("value"), rec.get("scope"))):
            p = person_token(v)
            if p:
                out.add(p)
    return out


def person_of(value, candidates, scope=None):
    """Resolve the person a value is about: direct extraction (plain and
    scope-stripped) first, then unique prefix match against the context's
    candidate persons (handles Chinese name+activity gluing). None when
    unresolved or ambiguous."""
    for v in (value, _strip_scope(value, scope)):
        p = person_token(v)
        if p:
            return p
    v = _strip_correction_prefix(value)
    hits = [c for c in candidates if v.startswith(c)]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        # several candidates prefix-match: only safe if one is a prefix of
        # all the others (the longest is the actual name)
        longest = max(hits, key=len)
        if all(longest.startswith(h) for h in hits):
            return longest
    return None


def correction_assertion(corr, person):
    """The corrected value a correction record asserts, or None when it
    cannot be isolated (correction flags an error but states no fact).

    Method: strip the correction prefix, the person name and boilerplate;
    drop the scope string (it names the attribute, not the value); then
    take the longest remaining content span (capitalized span for English,
    CJK run for Chinese)."""
    text = _strip_correction_prefix(corr.get("value"))
    if person and text.startswith(person):
        text = text[len(person) :]
    for bp in _BOILERPLATE:
        text = text.replace(bp, " ")
    scope = corr.get("scope") or ""
    if scope:
        text = text.replace(scope, " ")
    text = text.strip(" \t;；,，。:：")
    if not text:
        return None
    # English: capitalized spans are the entity/value candidates.
    spans = re.findall(r"[A-Z][a-z]+(?: [A-Z][a-z]+)*", text)
    if spans:
        return max(spans, key=len)
    # English role phrases: 'serves as convener in ...', 'is the photo
    # editor of ...' -- the asserted value is a lowercase role noun.
    for pat in (
        r"serves as (?:an? |the )?([A-Za-z][A-Za-z\- ]+?)"
        r"(?:\s+in\s+|\s+at\s+|;|,|$)",
        r"is the ([A-Za-z][A-Za-z\- ]+?)(?:\s+of\s+|\s+in\s+|;|,|$)",
        r"works as (?:an? |the )?([A-Za-z][A-Za-z\- ]+?)"
        r"(?:\s+in\s+|\s+at\s+|;|,|$)",
    ):
        m = re.search(pat, text)
        if m and len(m.group(1).strip()) >= 3:
            return m.group(1).strip()
    # Chinese: longest CJK run after dropping function words.
    runs = []
    cur = []
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff" and ch not in _ZH_FUNCTION:
            cur.append(ch)
        else:
            if cur:
                runs.append("".join(cur))
                cur = []
    if cur:
        runs.append("".join(cur))
    runs = [r for r in runs if len(r) >= 2]
    if runs:
        return max(runs, key=len)
    return None


def prefilter_v2(records, question, qdate):
    """Returns (kept_records, removals); removals are {id, rule, reason}."""
    stance = question_stance(question)
    candidates = candidate_persons(records)
    removed = {}

    def drop(rec, rule, reason):
        if rec["id"] not in removed:
            removed[rec["id"]] = {"id": rec["id"], "rule": rule, "reason": reason}

    # R1: self-marked expired (current stance only) -- unchanged from v1
    if stance == "current":
        for rec in records:
            v = rec.get("value") or ""
            if any(m in v for m in _EXPIRED_MARKERS):
                drop(rec, "R1_marked_expired", "record's own value states it has expired")

    # R2 v2: explicit correction supersedes only CONFLICTING earlier
    # same-scope same-person fact records.
    for corr in [r for r in records if _is_correction(r)]:
        person = person_of(corr.get("value"), candidates, corr.get("scope"))
        cdate = record_date(corr)
        if person is None or cdate is None:
            continue  # relation unprovable -> keep everything
        asserted = correction_assertion(corr, person)
        if asserted is None:
            continue  # correction states no corrected fact -> scope
            # unclear -> conservative retention (V6.2 negative case)
        for rec in records:
            if rec["id"] == corr["id"] or rec["id"] in removed:
                continue
            if rec.get("scope") != corr.get("scope"):
                continue  # never cross scope/attribute
            if _is_validity(rec) or _is_correction(rec):
                continue  # corrections revoke fact assertions, not
                # validity statements or other corrections
            if person_of(rec.get("value"), candidates, rec.get("scope")) != person:
                continue
            rdate = record_date(rec)
            if rdate is None or rdate > cdate:
                continue  # only records the correction can refer to
            if asserted in (rec.get("value") or ""):
                continue  # agrees with the correction -> keep
            drop(
                rec,
                "R2_correction_conflict",
                f"contradicts explicit correction {corr['id']} (asserted value: {asserted})",
            )

    # R3: credential validity join (current stance only) -- v1 semantics,
    # pairing via the context-assisted person resolution.
    if stance == "current" and qdate is not None:
        codes = [r for r in records if _is_code(r) and r["id"] not in removed]
        valids = [r for r in records if _is_validity(r)]
        for code in codes:
            person = person_of(code.get("value"), candidates, code.get("scope"))
            cdate = record_date(code)
            if person is None or cdate is None:
                continue
            pairs = [
                v
                for v in valids
                if v.get("scope") == code.get("scope")
                and person_of(v.get("value"), candidates, v.get("scope")) == person
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
