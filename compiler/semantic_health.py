"""Semantic retrieval health, cache, rerank, and own evaluation (Step 5).

This is the open-source-side improvement for semantic retrieval. It
does NOT fix any production port occupation and does NOT embed any
production configuration: the service URL is injected by the caller
(or defaults to the loopback default used by semantic.py), and all
tests inject fake probes/embedders so no network is required.

Components:
- check_service(probe): probe is a callable returning (ok, latency_ms)
  or raising; returns a status dict with available, latency, error,
  and a fail-closed recommendation (fall back to literal retrieval).
- VectorCache: bounded LRU in-memory cache for query -> vector, with
  hit/miss stats. No disk writes unless a path is explicitly given,
  in which case it stores only query hashes, not raw user text.
- rerank(candidates): each candidate has literal (0..1) and semantic
  (0..1, or None). Missing semantic falls back to literal alone.
  Score = 0.4*literal + 0.6*semantic when semantic exists, else
  literal. Weights are documented, not claimed as measured optima.
- evaluate(cases, rank_fn): own evaluation set. Each case has a
  query, an expected id, and a candidate list already scored by
  rank_fn; returns recall@1, recall@5, and per-case results. The set
  is synthetic in tests and must not be presented as a vendor score.
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from pathlib import Path


def check_service(probe, timeout_note: str = "") -> dict:
    """Probe the embedding service without embedding user data here.

    ``probe`` is a zero-arg callable returning (ok: bool, latency_ms: float)
    or raising an exception. The caller owns the probe (real embed call
    or a fake in tests).
    """
    try:
        ok, latency_ms = probe()
    except Exception as exc:  # noqa: BLE001 - any probe failure is fail-closed
        return {
            "available": False,
            "latency_ms": None,
            "error": str(exc),
            "recommendation": "fall_back_to_literal",
            "note": timeout_note,
        }
    if not ok:
        return {
            "available": False,
            "latency_ms": latency_ms,
            "error": "probe returned not-ok",
            "recommendation": "fall_back_to_literal",
            "note": timeout_note,
        }
    return {
        "available": True,
        "latency_ms": latency_ms,
        "error": None,
        "recommendation": "use_semantic",
        "note": timeout_note,
    }


class VectorCache:
    """Bounded LRU cache for query vectors (in-memory by default)."""

    def __init__(self, max_entries: int = 256, path=None):
        if max_entries <= 0:
            raise ValueError("max_entries must be > 0")
        self.max_entries = max_entries
        self.path = str(path) if path is not None else None
        self._store: OrderedDict[str, list] = OrderedDict()
        self.hits = 0
        self.misses = 0
        if self.path:
            try:
                data = json.loads(Path(self.path).read_text(encoding="utf-8"))
                for key, vec in data.items():
                    self._store[key] = vec
            except (OSError, ValueError):
                pass

    @staticmethod
    def key_for(query: str) -> str:
        return hashlib.sha256(str(query).encode("utf-8")).hexdigest()

    def get(self, query: str):
        key = self.key_for(query)
        if key in self._store:
            self.hits += 1
            self._store.move_to_end(key)
            return list(self._store[key])
        self.misses += 1
        return None

    def put(self, query: str, vector) -> None:
        key = self.key_for(query)
        self._store[key] = list(vector)
        self._store.move_to_end(key)
        while len(self._store) > self.max_entries:
            self._store.popitem(last=False)
        if self.path:
            try:
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
                Path(self.path).write_text(
                    json.dumps(self._store, ensure_ascii=False), encoding="utf-8"
                )
            except OSError:
                pass

    def stats(self) -> dict:
        total = self.hits + self.misses
        return {
            "entries": len(self._store),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": (self.hits / total) if total else 0.0,
        }


def rerank(candidates) -> list[dict]:
    """Rerank by documented weights; missing semantic = literal only."""
    scored = []
    for cand in candidates:
        cand = dict(cand)
        literal = float(cand.get("literal") or 0.0)
        semantic = cand.get("semantic")
        if semantic is None:
            score = literal
            basis = "literal_only"
        else:
            score = 0.4 * literal + 0.6 * float(semantic)
            basis = "literal_semantic"
        cand["rerank_score"] = score
        cand["rerank_basis"] = basis
        scored.append(cand)
    scored.sort(key=lambda c: (-c["rerank_score"], str(c.get("id") or "")))
    return scored


def evaluate(cases) -> dict:
    """Own synthetic evaluation: recall@1 and recall@5 over cases."""
    cases = list(cases or [])
    if not cases:
        return {"cases": 0, "recall_at_1": 0.0, "recall_at_5": 0.0, "results": []}
    hit1 = 0
    hit5 = 0
    results = []
    for case in cases:
        ranked = rerank(case.get("candidates") or [])
        expected = case.get("expected_id")
        rank = None
        for i, cand in enumerate(ranked, start=1):
            if cand.get("id") == expected:
                rank = i
                break
        if rank == 1:
            hit1 += 1
        if rank is not None and rank <= 5:
            hit5 += 1
        results.append({"query": case.get("query"), "expected_id": expected, "rank": rank})
    return {
        "cases": len(cases),
        "recall_at_1": hit1 / len(cases),
        "recall_at_5": hit5 / len(cases),
        "results": results,
    }
