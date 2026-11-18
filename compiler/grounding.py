"""Deterministic evidence-support check for unattended (auto_write) claims.

A verbatim quote match proves the evidence exists in the source message; it
does not prove the model-written ``value`` is supported by that evidence
(``evidence="I like tea"`` with ``value="I dislike tea"`` verifies and used
to auto-write). This module looks for *positive signs of non-support* only:

- ``polarity_flip``: a content word shared by value and evidence is negated
  on one side and not on the other (dislike vs like, 不换 vs 换).
- ``numeric_mismatch``: both sides carry numbers and the sets are disjoint.
- ``numeric_unanchored``: the value carries numbers the evidence never
  mentions (invented quantities).
- ``unanchored_long_value``: a long value shares no content token at all
  with its evidence.

No sign found means "no deterministic objection", not "proven entailment":
faithful paraphrases pass, which is the point -- the previous attempt
(PR #118) demanded ``value == evidence`` byte equality and broke the whole
auto-write channel, because values are normalised assertions and evidence
is a verbatim quote. An LLM judge is deliberately not used (measured
unreliable: it injured positive controls). Anything flagged here is routed
to the same human-review path as every other ``confirm`` claim; this check
can only ever *remove* auto-write permission, never grant it.
"""

import re
import unicodedata

# --- tokenisation -----------------------------------------------------------

_EN_TOKEN = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
_CJK = re.compile(r"[一-鿿]")

_EN_STOP = {
    "i",
    "me",
    "my",
    "we",
    "our",
    "you",
    "your",
    "he",
    "she",
    "it",
    "its",
    "they",
    "them",
    "the",
    "a",
    "an",
    "and",
    "or",
    "but",
    "of",
    "to",
    "in",
    "on",
    "at",
    "for",
    "with",
    "by",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "am",
    "do",
    "does",
    "did",
    "have",
    "has",
    "had",
    "will",
    "would",
    "can",
    "could",
    "should",
    "please",
    "keep",
    "now",
    "just",
    "really",
    "very",
    "so",
    "that",
    "this",
    "there",
    "here",
    "as",
    "from",
    "about",
    "into",
    "than",
    "then",
    "too",
    "also",
}


def _norm(text):
    return unicodedata.normalize("NFC", str(text or "")).casefold()


def _stem(tok):
    """Crude singularisation so like/likes and reply/replies compare equal."""
    if tok.endswith("'s"):
        tok = tok[:-2]
    if len(tok) > 4 and tok.endswith("ies"):
        return tok[:-3] + "y"
    if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
        return tok[:-1]
    return tok


def _en_tokens(text):
    return _EN_TOKEN.findall(_norm(text))


def _is_content(tok):
    return tok not in _EN_STOP and _stem(tok) not in _EN_STOP


def _en_content(text):
    return {_stem(tok) for tok in _en_tokens(text) if _is_content(tok)}


def _cjk_chars(text):
    return [ch for ch in _norm(text) if _CJK.match(ch)]


def _cjk_bigrams(text):
    chars = _cjk_chars(text)
    return {chars[i] + chars[i + 1] for i in range(len(chars) - 1)}


# --- negation ---------------------------------------------------------------

# Stemmed forms (tokens are stemmed before lookup).
# Propositional negations only. Action verbs (avoid / refuse / stop /
# against) are excluded on purpose: they negate an act, not the truth of
# the shared content word, and misfired on entity-style values (value
# "downtown and the highway" vs evidence "We'd like to avoid downtown
# and the highway"). Dislike-class predicates live in _EN_NEG_PREDICATES.
_EN_NEG_WORDS = {
    "not",
    "no",
    "non",
    "never",
    "none",
    "cannot",
    "can't",
    "won't",
    "don't",
    "doesn't",
    "didn't",
    "isn't",
    "aren't",
    "wasn't",
    "weren't",
    "wouldn't",
    "shouldn't",
    "couldn't",
    "without",
    "disagree",
    "oppose",
    "forbid",
}

# Predicates that carry their own negation: stemmed token -> positive bases.
_EN_NEG_PREDICATES = {
    "dislike": ("like",),
    "unlike": ("like",),
    "hate": ("like", "love"),
}

_CJK_NEG_CHARS = {"不", "没", "别", "无", "未", "非", "勿"}
_CJK_NEG_WORDS = ("拒绝", "讨厌", "反对", "避免", "不要", "没有", "不会", "不能")


def _en_negated_targets(text):
    """Content words negated in an English text (stemmed).

    A negation marker governs the nearest following content word
    (``not coffee`` negates coffee, not an earlier ``tea``). Self-negating
    predicates (``dislike``) govern their positive base form.
    """
    tokens = _en_tokens(text)
    negated = set()
    for i, tok in enumerate(tokens):
        stem = _stem(tok)
        if stem in _EN_NEG_PREDICATES:
            negated.update(_EN_NEG_PREDICATES[stem])
        if stem in _EN_NEG_WORDS or tok.endswith("n't"):
            for nxt in tokens[i + 1 :]:
                nstem = _stem(nxt)
                if nstem in _EN_NEG_PREDICATES:
                    negated.update(_EN_NEG_PREDICATES[nstem])
                    break
                if _is_content(nxt):
                    negated.add(nstem)
                    break
    return negated


def _cjk_negated_targets(text):
    """Content characters negated in a Chinese text.

    A negation character governs the first content character that follows
    it within two characters (不换城市 -> 换). Word-level negations
    (拒绝/讨厌/反对/避免) govern the character right after the word.
    """
    norm = _norm(text)
    negated = set()
    for i, ch in enumerate(norm):
        if ch in _CJK_NEG_CHARS:
            for nxt in norm[i + 1 : i + 3]:
                if _CJK.match(nxt):
                    negated.add(nxt)
                    break
    for word in _CJK_NEG_WORDS:
        start = 0
        while True:
            j = norm.find(word, start)
            if j < 0:
                break
            for nxt in norm[j + len(word) : j + len(word) + 2]:
                if _CJK.match(nxt):
                    negated.add(nxt)
                    break
            start = j + len(word)
    return negated


# --- numbers ----------------------------------------------------------------

_NUM = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?%?")
_CJK_DIGITS = {
    "零": 0,
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
}
_CJK_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}


# Measure words: a lone 一 right before one of these is the indefinite
# article ("a camera"), not a quantity assertion.
_CJK_MEASURE = set("个台枚件只条本张把位名种样辆部双群套间座棵颗粒根支盏杯碗瓶盒包袋盘")


def _cjk_numbers(text):
    """Parse simple Chinese numerals (三, 二十, 五百, 一万) into values."""
    out = set()
    run = []

    def flush(run, following=None):
        if not run:
            return
        # A lone numeral char is a quantity only when a measure word
        # follows (两杯茶); inside a word (一直, 下一步, 三明治, 零食)
        # it is not an assertion. Compound forms (二十, 一百, 一万)
        # carry a unit char and always count.
        if len(run) == 1 and following not in _CJK_MEASURE:
            return
        total, section, last = 0, 0, None
        for ch in run:
            if ch in _CJK_DIGITS:
                last = _CJK_DIGITS[ch]
            elif ch in _CJK_UNITS:
                unit = _CJK_UNITS[ch]
                if unit == 10000:
                    section = (section + (last or 0)) * unit
                    total += section
                    section, last = 0, None
                else:
                    section += (last if last is not None else 1) * unit
                    last = None
        out.add(float(total + section + (last or 0)))

    chars = list(text)
    for i, ch in enumerate(chars):
        if ch in _CJK_DIGITS or ch in _CJK_UNITS:
            if ch == "一" and not run and i + 1 < len(chars) and chars[i + 1] in _CJK_MEASURE:
                continue
            run.append(ch)
        else:
            flush(run, ch)
            run = []
    flush(run)
    return out


_EN_NUM_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}


# Compare single-digit Chinese durations only when both assertions actually
# name the same unit. A weekday, an unrelated measure word or a differently
# expressed duration is outside this narrow comparison.
_DURATION = re.compile(
    r"(?<![零一二两三四五六七八九十百千万点第0-9.])"
    r"([一二两三四五六七八九]|[1-9])(分钟|小时|个月|年|周|天)"
)


def _duration_values(text):
    by_unit = {}
    for number, unit in _DURATION.findall(_norm(text)):
        value = int(number) if number.isascii() else _CJK_DIGITS[number]
        by_unit.setdefault(unit, set()).add(value)
    return by_unit


def _numbers(text):
    norm = _norm(text)
    out = set()
    for tok in _en_tokens(norm):
        if tok in _EN_NUM_WORDS:
            out.add(float(_EN_NUM_WORDS[tok]))
    for match in _NUM.findall(norm):
        raw = match.replace(",", "")
        try:
            if raw.endswith("%"):
                value = float(raw[:-1])
                out.add(value)
                out.add(value / 100.0)
            else:
                out.add(float(raw))
        except ValueError:
            continue
    out |= _cjk_numbers(norm)
    return out


# --- the check --------------------------------------------------------------


def _fold(tokens):
    """Add positive bases of self-negating predicates (dislike -> like)."""
    out = set(tokens)
    for tok in tokens:
        out.update(_EN_NEG_PREDICATES.get(tok, ()))
    return out


def _shared_tokens(value, evidence):
    """Shared content tokens between value and evidence, per script."""
    shared = _fold(_en_content(value)) & _fold(_en_content(evidence))
    shared |= _cjk_bigrams(value) & _cjk_bigrams(evidence)
    return shared


def _polarity_flip(value, evidence):
    shared = _shared_tokens(value, evidence)
    if not shared:
        return False
    v_neg = _en_negated_targets(value)
    e_neg = _en_negated_targets(evidence)
    for word in shared:
        if (word in v_neg) != (word in e_neg):
            return True
    # Chinese: shared bigrams contribute their characters; a character
    # negated on exactly one side flips the claim's polarity.
    shared_chars = set()
    for gram in shared:
        shared_chars.update(gram)
    v_neg_c = _cjk_negated_targets(value)
    e_neg_c = _cjk_negated_targets(evidence)
    for ch in shared_chars:
        if (ch in v_neg_c) != (ch in e_neg_c):
            return True
    return False


def support_flags(value, evidence):
    """Return the list of non-support signals found (empty = no objection)."""
    value, evidence = str(value or ""), str(evidence or "")
    if not value or not evidence:
        return []
    flags = []
    if _polarity_flip(value, evidence):
        flags.append("polarity_flip")
    v_nums, e_nums = _numbers(value), _numbers(evidence)
    v_durations, e_durations = _duration_values(value), _duration_values(evidence)
    for unit in v_durations.keys() & e_durations.keys():
        if v_durations[unit].isdisjoint(e_durations[unit]):
            flags.append("numeric_mismatch")
            break
        e_nums |= e_durations[unit]
    if 1.0 in v_nums and 1.0 not in e_nums:
        # "(1 month)" over "it's been a month": the indefinite article
        # carries the quantity. Narrow rule: value-side 1 with a time
        # unit, evidence-side a/an + unit or 一 + unit.
        units_en = ("month", "week", "year", "day", "hour", "minute")
        units_zh = ("个月", "周", "年", "天", "小时", "分钟")
        v_unit = any(re.search(r"1\s*" + u + r"s?\b", value, flags=re.I) for u in units_en) or any(
            "1" + u in value for u in units_zh
        )
        e_article = any(
            re.search(r"\ban?\s+" + u + r"\b", evidence, flags=re.I) for u in units_en
        ) or any("一" + u in evidence for u in units_zh)
        if v_unit and e_article:
            v_nums = v_nums - {1.0}
    if "numeric_mismatch" not in flags and v_nums and e_nums and not (v_nums & e_nums):
        flags.append("numeric_mismatch")
    elif "numeric_mismatch" not in flags and v_nums and not e_nums:
        flags.append("numeric_unanchored")
    shared = _shared_tokens(value, evidence)
    # Lexical anchoring is only meaningful within one script family: the
    # production extractor routinely renders Chinese values over English
    # evidence (and vice versa), where token overlap is zero by
    # construction. Cross-script pairs skip this signal entirely.
    v_cjk, e_cjk = bool(_cjk_chars(value)), bool(_cjk_chars(evidence))
    if v_cjk == e_cjk:
        if v_cjk:
            long_enough = len(_cjk_bigrams(value)) >= 6
        else:
            long_enough = len(_en_content(value)) >= 4
        if not shared and long_enough:
            flags.append("unanchored_long_value")
    return flags
