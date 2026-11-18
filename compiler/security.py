"""Deterministic policy; never interpret importance as write permission."""

import json
import re
from pathlib import Path

RULES = json.loads(
    (Path(__file__).resolve().parents[1] / "shared" / "policy-rules.json").read_text(
        encoding="utf-8"
    )
)


def matches(group, text):
    return any(re.search(pattern, str(text), re.I) for pattern in RULES[group])


def contains_secret(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return matches("secret", text)


def redact(text):
    result = str(text)
    for pattern in RULES["secret"]:
        result = re.sub(pattern, "[REDACTED]", result, flags=re.I)
    return result


def detect_injection(text):
    return matches("injection", text)


def detect_heavy_emotion(text):
    return matches("heavy", text)


def detect_temporary(text):
    return matches("temporary", text)


def detect_temporary_intent(text):
    return matches("temporary_intent", text)


def detect_past_anchor(text):
    return matches("past_anchor", text)


def correct_temporary_flag(claim, message_text=""):
    """Deterministically correct a model-supplied ``temporary`` flag.

    The extraction prompt lists ``temporary`` as an output field but defines
    it nowhere, so the model applies it to any time-bound content at all.
    Round-4 LongMemEval attribution (2026-11-18) decomposed the 1,115 gold
    claims dropped as ``ineligible:temporary-flag``: 1,112 carried the flag
    from the model itself, 2 from the source-message rule match, 0 from the
    claim-text rules — and 92% of all temporary-flagged active claims were
    gold. The system's own definition of temporary is the ``temporary``
    rule group (transient states of mind); a plan stated in the past and an
    event that already happened are not it, yet the model flags both.

    The correction is one-directional and fail-closed. It runs only in the
    extraction pipeline, where the claim's evidence has been located in a
    specific source message, and it clears the flag only when ALL hold:

    - neither the claim text nor that source message matches the system's
      own ``temporary`` rules;
    - the claim carries no plan / hypothesis / unsettled marker
      (``temporary_intent``) — plans and not-yet-happened content keep the
      flag even when a date is mentioned;
    - the claim carries a positive past anchor (``past_anchor``): a
      completed first-person report, an explicit past date, or a
      started-in-the-past perfect form.

    Anything ambiguous keeps the flag. A correction is recorded on the
    claim as ``temporary_correction`` and policy is recomputed, so the
    "temporary" review reason disappears with the flag. Returns
    ``(claim, corrected)``.
    """
    c = dict(claim)
    if not c.get("temporary"):
        return c, False
    text = " ".join(
        str(c.get(k, "")) for k in ("kind", "subject", "predicate", "value", "evidence")
    )
    if detect_temporary(text) or (message_text and detect_temporary(message_text)):
        return c, False
    if detect_temporary_intent(text):
        return c, False
    if not detect_past_anchor(text):
        return c, False
    c["temporary"] = False
    c["temporary_correction"] = "past-anchor-v1"
    return apply_policy(c), True


import calendar
from datetime import date, timedelta

_WEEKDAY_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
_CN_DIGITS = {
    "一": 1,
    "两": 2,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def _cn_number(text):
    if text.isdigit():
        return int(text)
    return _CN_DIGITS.get(text)


def _shift_months(day, months):
    total = day.year * 12 + (day.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last))


def _shift_years(day, years):
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # Feb 29 -> Feb 28 in a non-leap target year
        return day.replace(year=day.year + years, day=28)


def _iso_week(day):
    monday = day - timedelta(days=day.weekday())
    return [(monday + timedelta(days=i)).isoformat() for i in range(7)]


def _month_key(day):
    return day.isoformat()[:7]


def _relative_date_keys(text, base):
    """Resolve relative time expressions in ``text`` against ``base``.

    Returns ``(keys, expressions)``: day keys as ISO dates, month-level
    references as ``YYYY-MM``. Only calendar-precise expressions resolve;
    vague ones ("a few days ago", "recently", 去年) yield nothing —
    fail-closed, never a guessed date.
    """
    low = text.lower()
    found = []  # (start, matched_text, [keys])

    def add(pattern, handler, flags=0):
        for m in re.finditer(pattern, low, flags):
            keys = handler(m)
            if keys:
                found.append((m.start(), m.group(0), keys))

    def days(n):
        return lambda m: [(base + timedelta(days=n)).isoformat()]

    def days_num(offset_of):
        return lambda m: [(base + timedelta(days=offset_of(int(m.group(1))))).isoformat()]

    # English, most specific first.
    add(r"the day before yesterday", days(-2))
    add(r"day after tomorrow", days(2))
    add(r"\b(\d{1,3})\s+days?\s+ago\b", days_num(lambda n: -n))
    add(r"\b(\d{1,3})\s+weeks?\s+ago\b", days_num(lambda n: -7 * n))
    add(
        r"\b(\d{1,3})\s+months?\s+ago\b",
        lambda m: [_shift_months(base, -int(m.group(1))).isoformat()],
    )
    add(
        r"\b(\d{1,3})\s+years?\s+ago\b",
        lambda m: [_shift_years(base, -int(m.group(1))).isoformat()],
    )
    add(r"\ba\s+day\s+ago\b", days(-1))
    add(r"\ba\s+week\s+ago\b", days(-7))
    add(r"\ba\s+month\s+ago\b", lambda m: [_shift_months(base, -1).isoformat()])
    add(r"\byesterday\b", days(-1))
    add(r"\btomorrow\b", days(1))
    add(r"\btoday\b", days(0))
    add(r"\bearlier\s+today\b", days(0))
    add(r"\bthis\s+(?:morning|afternoon|evening)\b", days(0))
    add(r"\blast\s+week\b", lambda m: _iso_week(base - timedelta(days=7)))
    add(r"\bnext\s+week\b", lambda m: _iso_week(base + timedelta(days=7)))
    add(r"\bthis\s+week\b", lambda m: _iso_week(base))
    add(r"\blast\s+month\b", lambda m: [_month_key(_shift_months(base, -1))])
    add(r"\bnext\s+month\b", lambda m: [_month_key(_shift_months(base, 1))])
    add(r"\bthis\s+month\b", lambda m: [_month_key(base)])
    add(
        r"\blast\s+weekend\b",
        lambda m: [
            (base - timedelta(days=base.weekday() + 2)).isoformat(),
            (base - timedelta(days=base.weekday() + 1)).isoformat(),
        ],
    )
    add(
        r"\blast\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        lambda m: [
            (
                base - timedelta(days=((base.weekday() - _WEEKDAY_INDEX[m.group(1)]) % 7) or 7)
            ).isoformat()
        ],
    )
    # Chinese (matched against the lowered text; digits may be hanzi).
    num = r"(\d{1,3}|[一两二三四五六七八九十])"
    add("前天", days(-2))
    add("昨天|昨日", days(-1))
    add("今天|今日", days(0))
    add("明天|明日", days(1))
    add("后天", days(2))
    add(
        num + r"\s*天前",
        lambda m: [(base - timedelta(days=_cn_number(m.group(1)))).isoformat()]
        if _cn_number(m.group(1))
        else [],
    )
    add(
        num + r"\s*(?:周|星期|礼拜)前",
        lambda m: [(base - timedelta(days=7 * _cn_number(m.group(1)))).isoformat()]
        if _cn_number(m.group(1))
        else [],
    )
    add(
        num + r"\s*个月前",
        lambda m: [_shift_months(base, -_cn_number(m.group(1))).isoformat()]
        if _cn_number(m.group(1))
        else [],
    )
    add(
        num + r"\s*年前",
        lambda m: [_shift_years(base, -_cn_number(m.group(1))).isoformat()]
        if _cn_number(m.group(1))
        else [],
    )
    add("上周|上星期|上礼拜", lambda m: _iso_week(base - timedelta(days=7)))
    add("下周|下星期|下礼拜", lambda m: _iso_week(base + timedelta(days=7)))
    add("这周|本周|这星期|本星期", lambda m: _iso_week(base))
    add("上个月|上月", lambda m: [_month_key(_shift_months(base, -1))])
    add("下个月|下月", lambda m: [_month_key(_shift_months(base, 1))])
    add("本月|这个月", lambda m: [_month_key(base)])

    keys = []
    expressions = []
    for _start, expr, ks in sorted(found):
        expressions.append(expr)
        for k in ks:
            if k not in keys:
                keys.append(k)
    return sorted(keys)[:16], expressions[:8]


def derive_date_keys(claim, message_timestamp=""):
    """Derive absolute date keys from a claim's relative time expressions.

    Same post-extraction deterministic-correction pattern as
    ``correct_temporary_flag``: the extraction model paraphrases relative
    times ("last week", 上周) into claim values, and the projection's
    lexical matching then cannot connect a question that names the
    absolute date ("on March 13", 3月13日) — the claim is stored yet
    reads as irrelevant. This pass resolves each relative expression
    against the source message's own timestamp and stores the results as
    ``date_keys`` (ISO day keys and ``YYYY-MM`` month keys), leaving
    value/evidence byte-identical. The derivation is recorded as
    ``date_keys_derivation`` plus the matched expressions, so any key can
    be traced back to the phrase that produced it.

    Fail-closed throughout: no source timestamp, an unparseable one, or
    no calendar-precise expression means the claim is returned unchanged.
    Returns ``(claim, changed)``.
    """
    c = dict(claim)
    ts = message_timestamp or str((c.get("provenance") or {}).get("timestamp") or "")
    if not ts:
        return c, False
    try:
        base = date.fromisoformat(ts.strip().replace("Z", "+00:00")[:10])
    except ValueError:
        return c, False
    text = " ".join(str(c.get(k, "")) for k in ("value", "evidence"))
    if not text.strip():
        return c, False
    keys, expressions = _relative_date_keys(text, base)
    if not keys:
        return c, False
    c["date_keys"] = keys
    c["date_keys_derivation"] = "relative-time-v1"
    c["date_keys_expressions"] = expressions
    return c, True


def detect_sarcasm(text):
    return matches("sarcasm", text)


def detect_outdated(text):
    return matches("outdated", text)


def apply_policy(claim):
    c = dict(claim)
    text = " ".join(
        str(c.get(k, "")) for k in ("kind", "subject", "predicate", "value", "evidence")
    )
    secret = contains_secret(c)
    c["suspicious"] = bool(c.get("suspicious")) or detect_injection(text)
    c["heavy_emotion"] = bool(c.get("heavy_emotion")) or detect_heavy_emotion(text)
    c["temporary"] = bool(c.get("temporary")) or detect_temporary(text)
    c["sarcasm"] = bool(c.get("sarcasm")) or detect_sarcasm(text)
    c["outdated"] = bool(c.get("outdated")) or detect_outdated(text)
    sensitive = (
        c.get("sensitivity") == "SENSITIVE"
        or bool(c.get("sensitive"))
        or c["heavy_emotion"]
        or matches("sensitive", text)
    )
    c["sensitivity"] = "SECRET" if secret else ("SENSITIVE" if sensitive else "PRIVATE")
    c["importance"] = max(c.get("importance", 0.5), 0.9 if c["heavy_emotion"] else 0)
    c["write_policy"] = "allow"
    c["injection_policy"] = "recall"
    reasons = []
    if c["kind"] in {"identity", "user-core"}:
        reasons.append("core identity requires exact-text confirmation")
    if sensitive:
        reasons.append("sensitive")
    for flag in ("suspicious", "temporary", "sarcasm", "outdated"):
        if c[flag]:
            reasons.append(flag)
    if matches("quoted", text):
        reasons.append("quoted statement")
    if c.get("source") != "user":
        # 有逐字核验证据的非 user 来源（assistant 本人提供过的内容、
        # 被引用的原话）不是「冒充用户发言」，而是待审的历史记录；
        # 没有核验证据的来源才按未核验处理。两者都只能人工审批
        # （write_policy=confirm 不变），区别只在理由要说真话。
        if c.get("evidence_verified") is True:
            reasons.append("non-user speaker requires review")
        else:
            reasons.append("unverified user speaker")
    if not c.get("evidence"):
        reasons.append("missing evidence")
    if c.get("evidence_verified") is not True:
        reasons.append("unverified evidence")
    if c.get("valid") is not True:
        reasons.append("invalid claim")
    # Model-provided policy may restrict but cannot grant permission.
    if c.get("requested_write_policy") in {"confirm", "never"}:
        reasons.append("restricted by source")
    if reasons:
        c["write_policy"] = "confirm"
        c["injection_policy"] = "confirm"
    if secret or c.get("requested_write_policy") == "never":
        c["write_policy"] = "never"
        c["injection_policy"] = "never"
        reasons = ["secret"] if secret else ["restricted by source"]
    c["requires_confirmation"] = c["write_policy"] == "confirm"
    c["auto_write"] = c["write_policy"] == "allow"
    c["policy_reasons"] = reasons
    return c


def check_security(claim):
    c = apply_policy(claim)
    return not c["auto_write"], "; ".join(c["policy_reasons"])
