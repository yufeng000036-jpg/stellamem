"""Deterministic evidence-sufficiency signal for the recall path.

Python mirror of the anchor machinery in adapters/openclaw/index.js
(questionAnchors / anchorCovered / evidenceSufficiency), so the HTTP
recall path (/v1/recall via search.recall) reports the same graded
signal the projection layer computes:

    "insufficient" - named anchors exist, none covered by the recalled
                     items' own text;
    "partial"      - a proper subset of the named anchors is absent
                     from every recalled item (one-sided evidence: the
                     shape behind the stage-2 Ferrari/Porsche
                     abstention failure, where the reader answered a
                     two-sided comparison from one side's lines);
    "sufficient"   - every named anchor covered;
    "unknown"      - the prompt carries no named anchors (inert).

Deterministic, no model call, fail-closed: the signal only describes
coverage; it never removes items, never changes scores, and never
loosens any existing gate. A common-noun coverage variant was measured
on the stage-2 cache (482 projections) and false-fired on 57-147 of
186 correctly answered questions, so coverage is deliberately checked
over named (capitalized) anchors only - the same conservative basis
the projection anchor gate has used since PR #127.

Texts are lowercased inside evidence_sufficiency (the JS version
receives pre-lowercased claim texts from its caller; the results are
identical).
"""

from __future__ import annotations

import re

ANCHOR_STOP = {
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
    "i",
}

ANCHOR_EDGE_PUNCT = set(".,;:!?'\"()[]{}<>。、，；：！？·…—「」『』〈〉《》【】（）“”‘’")

_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9&'+\-.]*")
_SENTENCE_END_RE = re.compile(r"""[.!?]["']?\s*$""")


def _normalize_anchor_token(tok: str) -> str:
    start, end = 0, len(tok)
    while start < end and tok[start] in ANCHOR_EDGE_PUNCT:
        start += 1
    while end > start and tok[end - 1] in ANCHOR_EDGE_PUNCT:
        end -= 1
    return tok[start:end]


def question_anchors(prompt: str) -> list[str]:
    """Named anchors of a prompt: capitalized phrases that are not
    sentence-initial, with edge punctuation stripped (port of the JS
    questionAnchors; parity cases live in tests/test_sufficiency.py)."""
    prompt = str(prompt or "")
    anchors: list[str] = []
    current: list[str] = []
    for i, m in enumerate(_TOKEN_RE.finditer(prompt)):
        tok = _normalize_anchor_token(m.group(0))
        sentence_initial = i == 0 or bool(_SENTENCE_END_RE.search(prompt[: m.start()]))
        capitalized = (bool(re.match(r"[A-Z]", tok)) and bool(re.search(r"[a-z]", tok))) or (
            len(tok) >= 2 and tok == tok.upper() and bool(re.search(r"[A-Z]", tok))
        )
        if capitalized and not sentence_initial and tok.lower() not in ANCHOR_STOP:
            current.append(tok)
        elif current:
            anchors.append(" ".join(current).lower())
            current = []
    if current:
        anchors.append(" ".join(current).lower())
    return anchors


def _anchor_covered(text: str, anchor: str) -> bool:
    if " " in anchor:
        return anchor in text
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(anchor) + r"(?![a-z0-9])", text))


def evidence_sufficiency(prompt: str, texts) -> dict:
    """Grade the coverage of the prompt's named anchors over texts.

    texts: iterable of item texts (any case; lowercased here). Returns
    {"status", "anchors", "covered", "uncovered"} - see module docstring.
    """
    anchors = question_anchors(prompt)
    if not anchors:
        return {"status": "unknown", "anchors": [], "covered": [], "uncovered": []}
    lowered = [str(t or "").lower() for t in texts]
    uncovered = [a for a in anchors if not any(_anchor_covered(t, a) for t in lowered)]
    covered = [a for a in anchors if a not in uncovered]
    if len(uncovered) == len(anchors):
        status = "insufficient"
    elif uncovered:
        status = "partial"
    else:
        status = "sufficient"
    return {"status": status, "anchors": anchors, "covered": covered, "uncovered": uncovered}
