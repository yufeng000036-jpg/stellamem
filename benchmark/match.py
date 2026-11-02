"""Deterministic claim matcher.

Why this exists: the 0.705 / 0.505 numbers came from a single LLM judgement per
pair, with an unreleased script. That is not reproducible and drifts with the
model. This matcher is stable: same input, same score, no sampling.

Two layers:
  1. lexical similarity (CJK bigrams + latin/number tokens), always available;
  2. optional embeddings via STELLAMEM_EMBED_URL (llama.cpp /v1/embeddings).
     When the endpoint is down it silently falls back to layer 1, so CI and
     offline runs still work.

Usage:
    python benchmark/match.py "文本A" "文本B"
    python benchmark/match.py --file pairs.json
"""

import argparse
import json
import math
import os
import re
import sys
import unicodedata
import urllib.request
from collections import Counter

MATCH_THRESHOLD = 0.52
CJK = re.compile(r"[\u4e00-\u9fff]+")
LATIN = re.compile(r"[a-z0-9_]{2,}")
NUM = re.compile(r"\d+(?:\.\d+)?")
PATH = re.compile(r"[a-z]:[\\/][^\s]+")

# Small, explicit synonym set. Only pairs that mean the same thing in this
# corpus; anything broader starts merging distinct memories.
SYNONYMS = {
    "偏好": "喜欢",
    "喜爱": "喜欢",
    "本意": "想",
    "需": "需要",
    "已完成": "完成",
    "採用": "采用",
}


def normalize(text):
    text = unicodedata.normalize("NFKC", str(text or "")).lower()
    text = text.replace("\\", "/")
    for source, target in SYNONYMS.items():
        text = text.replace(source, target)
    return re.sub(r"\s+", " ", text).strip()


def tokens(text):
    text = normalize(text)
    out = []
    for run in CJK.findall(text):
        if len(run) == 1:
            out.append(run)
        else:
            out.extend(run[i : i + 2] for i in range(len(run) - 1))
    out.extend(LATIN.findall(text))
    return out


def lexical(a, b):
    va, vb = Counter(tokens(a)), Counter(tokens(b))
    if not va or not vb:
        return 0.0
    dot = sum(va[k] * vb[k] for k in va.keys() & vb.keys())
    na = math.sqrt(sum(v * v for v in va.values()))
    nb = math.sqrt(sum(v * v for v in vb.values()))
    cosine = dot / (na * nb) if na and nb else 0.0
    inter = sum(min(va[k], vb[k]) for k in va.keys() & vb.keys())
    containment = inter / min(sum(va.values()), sum(vb.values()))
    return cosine * (0.5 + 0.5 * containment)


def anchors(a, b):
    """Exact numbers and paths are strong evidence; mismatch is not fatal."""
    na, nb = set(NUM.findall(normalize(a))), set(NUM.findall(normalize(b)))
    pa, pb = set(PATH.findall(normalize(a))), set(PATH.findall(normalize(b)))
    bonus = 0.0
    if na and nb and na & nb:
        bonus += 0.12
    if pa and pb and pa & pb:
        bonus += 0.18
    return bonus


def embed(texts):
    url = os.environ.get("STELLAMEM_EMBED_URL")
    if not url:
        return None
    payload = json.dumps({"input": texts, "model": "bge-m3"}).encode("utf-8")
    req = urllib.request.Request(
        url.rstrip("/") + "/v1/embeddings",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
        return [item["embedding"] for item in data["data"]]
    except Exception:
        return None


def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def coverage(needle, haystack):
    """Fraction of the needle's tokens that appear in the haystack.

    Asymmetric on purpose: for evidence lookup we ask "is this claim supported
    by that sentence", not "are these two texts equally similar", so a short
    claim inside a long line should still score high.
    """
    va, vb = Counter(tokens(needle)), Counter(tokens(haystack))
    total = sum(va.values())
    if not total or not vb:
        return 0.0
    inter = sum(min(va[k], vb[k]) for k in va.keys() & vb.keys())
    return inter / total


def score(a, b, vectors=None):
    a, b = normalize(a), normalize(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    base = _cosine(vectors[0], vectors[1]) if vectors else lexical(a, b)
    if a in b or b in a:
        base = max(base, 0.82 if min(len(a), len(b)) >= 8 else 0.55)
    return min(1.0, base + anchors(a, b))


def match(predicted, expected, use_embeddings=True):
    """Greedy one-to-one matching; returns (pairs, missed, extra)."""
    predictions = list(predicted)
    gold = list(expected)
    vectors = None
    if use_embeddings:
        flat = [p["text"] for p in predictions] + [g["text"] for g in gold]
        emb = embed(flat)
        if emb and len(emb) == len(flat):
            vectors = emb
    candidates = []
    for i, p in enumerate(predictions):
        for j, g in enumerate(gold):
            v = None if vectors is None else [vectors[i], vectors[len(predictions) + j]]
            candidates.append((score(p["text"], g["text"], v), i, j))
    candidates.sort(reverse=True)
    used_p, used_g, pairs = set(), set(), []
    for s, i, j in candidates:
        if s < MATCH_THRESHOLD or i in used_p or j in used_g:
            continue
        used_p.add(i)
        used_g.add(j)
        pairs.append({"predicted": i, "expected": j, "score": round(s, 3)})
    missed = [j for j in range(len(gold)) if j not in used_g]
    extra = [i for i in range(len(predictions)) if i not in used_p]
    return pairs, missed, extra


def metrics(predicted, expected):
    pairs, missed, extra = match(predicted, expected)
    tp = len(pairs)
    recall = tp / len(expected) if expected else 0.0
    precision = tp / len(predicted) if predicted else 0.0
    return {
        "expected": len(expected),
        "predicted": len(predicted),
        "matched": tp,
        "missed": len(missed),
        "extra": len(extra),
        "recall": round(recall, 4),
        "precision": round(precision, 4),
        "pairs": pairs,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("a", nargs="?")
    parser.add_argument("b", nargs="?")
    parser.add_argument("--file", help="JSON list of [a, b] pairs")
    parser.add_argument("--no-embed", action="store_true")
    args = parser.parse_args()
    if args.file:
        pairs = json.load(open(args.file, encoding="utf-8"))
        for a, b in pairs:
            print(f"{score(a, b):.3f}\t{a}  ||  {b}")
        return 0
    if not args.a or not args.b:
        parser.error("provide two strings or --file")
    vector = None
    if not args.no_embed:
        emb = embed([args.a, args.b])
        vector = emb
    print(f"{score(args.a, args.b, vector):.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
