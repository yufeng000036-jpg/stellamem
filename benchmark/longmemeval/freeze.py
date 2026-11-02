"""Freeze public labels by deterministic ID sampling, before running inference."""

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

REVISION = "98d7416c24c778c2fee6e6f3006e7a073259d48f"
DATA_SHA = "821a2034d219ab45846873dd14c14f12cfe7776e73527a483f9dac095d38620c"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def category(case):
    return "abstention" if case["question_id"].endswith("_abs") else case["question_type"]


def freeze(path):
    raw = path.read_bytes()
    if digest(raw) != DATA_SHA:
        raise ValueError("dataset hash mismatch")
    groups = defaultdict(list)
    for case in json.loads(raw):
        groups[category(case)].append(case)
    selected = [
        case
        for name in sorted(groups)
        for case in sorted(
            groups[name], key=lambda c: digest(("stellamem-pr15-v1:" + c["question_id"]).encode())
        )[:20]
    ]
    return {
        "dataset": "xiaowu0162/longmemeval-cleaned",
        "revision": REVISION,
        "file": "longmemeval_oracle.json",
        "sha256": DATA_SHA,
        "license": "MIT",
        "selection": "20 per category; SHA256(stellamem-pr15-v1:question_id) ascending",
        "scope": "oracle evidence sessions, NOT LongMemEval_S/M or independent claim labels",
        "counts": dict(Counter(category(c) for c in selected)),
        "cases": [
            {
                "question_id": c["question_id"],
                "category": category(c),
                "record_sha256": digest(json.dumps(c, ensure_ascii=False, sort_keys=True).encode()),
            }
            for c in selected
        ],
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("dataset", type=Path)
    p.add_argument("output", type=Path)
    a = p.parse_args()
    result = json.dumps(freeze(a.dataset), ensure_ascii=False, indent=2) + "\n"
    with a.output.open("x", encoding="utf-8") as out:
        out.write(result)
