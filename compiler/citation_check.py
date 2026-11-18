"""Deterministic citation verification for answers written under the
reading contract (compiler/reading.py).

verify_answer(answer, evidence) checks, with no model call:

1. Existence — every claim id cited in the answer must belong to the
   evidence set handed to the reader (forged or stale ids are flagged
   as unknown).
2. Support — for every sentence that cites a claim, the cited claim's
   own text must support the sentence's assertion under the existing
   deterministic grounding signals (compiler/grounding.support_flags:
   polarity flips, numeric mismatch / unanchored numbers, lexically
   unanchored long values). A flagged sentence means the citation does
   not deterministically support what it is attached to — e.g. citing
   the baseball claim for a number it never states.

The verifier reports; it never rewrites or blocks an answer. Support
is checked per assertion sentence: citation tokens are stripped from
the sentence before the check, and a sentence that is only a citation
(no assertion text) is counted as known-but-unchecked rather than
supported.

Also counted, informationally: factual-looking sentences with no
citation at all (uncited_assertions) — a digit or a capitalized name
beyond the first word and no claim id anywhere in the sentence.
"""

from __future__ import annotations

import re

try:  # 包内导入与扁平导入都要能用（同 search.py 的双轨写法）
    from . import grounding as grounding_mod
except ImportError:  # pragma: no cover
    import grounding as grounding_mod  # type: ignore

_ID_RE = re.compile(r"c-[0-9a-fA-F]{8,}")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。！？])\s+|\n+")
_CITATION_STRIP_RE = re.compile(r"\[?\s*c-[0-9a-fA-F]{8,}\s*\]?")
_CAPITALIZED_RE = re.compile(r"(?<=\s)[A-Z][a-z]+")


def _evidence_text(entry: dict) -> str:
    parts = [entry.get("text"), entry.get("value"), entry.get("evidence")]
    return " ".join(str(p) for p in parts if p)


def _normalize_evidence(evidence) -> dict:
    out: dict[str, str] = {}
    for entry in evidence or []:
        claim_id = entry.get("claim_id") or entry.get("id")
        if claim_id:
            out[str(claim_id)] = _evidence_text(entry)
    return out


def cited_ids(text: str) -> list[str]:
    """Claim ids cited anywhere in text, in order of first appearance."""
    seen: list[str] = []
    for m in _ID_RE.finditer(str(text or "")):
        cid = m.group(0).lower()
        if cid not in seen:
            seen.append(cid)
    return seen


def _sentences(answer: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(str(answer or "")) if s.strip()]


def verify_answer(answer: str, evidence) -> dict:
    """Verify an answer's citations against the evidence it was given.

    evidence: iterable of {"claim_id"/"id", "text"} entries (a "value"
    and/or "evidence" field is used when "text" is absent — the shape
    compiler/reading.build_evidence returns works as-is).
    """
    known_texts = _normalize_evidence(evidence)
    per_id: dict[str, dict] = {}
    uncited = 0
    for sentence in _sentences(answer):
        ids = cited_ids(sentence)
        if not ids:
            if any(ch.isdigit() for ch in sentence) or _CAPITALIZED_RE.search(sentence):
                uncited += 1
            continue
        assertion = _CITATION_STRIP_RE.sub(" ", sentence).strip(" []()\t")
        assertion = re.sub(r"\s+", " ", assertion).strip()
        for cid in ids:
            slot = per_id.setdefault(
                cid, {"claim_id": cid, "known": cid in known_texts, "flags": [], "checked": False}
            )
            if cid not in known_texts:
                continue
            if len(assertion) < 2:
                continue  # citation-only sentence: nothing to support
            slot["checked"] = True
            for flag in grounding_mod.support_flags(assertion, known_texts[cid]):
                if flag not in slot["flags"]:
                    slot["flags"].append(flag)
    citations = []
    for cid in cited_ids(answer):
        slot = per_id[cid]
        if not slot["known"]:
            supported = False
        elif slot["flags"]:
            supported = False
        elif slot["checked"]:
            supported = True
        else:
            supported = None  # known, but only ever cited bare
        citations.append(
            {
                "claim_id": cid,
                "known": slot["known"],
                "supported": supported,
                "flags": slot["flags"],
            }
        )
    cited_n = len(citations)
    known_n = sum(1 for c in citations if c["known"])
    supported_n = sum(1 for c in citations if c["supported"] is True)
    return {
        "citations": citations,
        "cited_n": cited_n,
        "known_n": known_n,
        "supported_n": supported_n,
        "verifiable_rate": (supported_n / cited_n) if cited_n else None,
        "unknown_ids": [c["claim_id"] for c in citations if not c["known"]],
        "unsupported": [
            {"claim_id": c["claim_id"], "flags": c["flags"]}
            for c in citations
            if c["known"] and c["flags"]
        ],
        "uncited_assertions": uncited,
    }
