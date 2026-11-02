"""Audit the 272 "extra" claims: true memory / detail / unsourced.

The 2026-09-20 report counted 272 extras as false positives, but extras were
never re-checked one by one. A prior hand audit (extra_6f_all.json) covers 205
of them. This script re-verifies the "unsourced" bucket against the sample text
with the deterministic matcher and writes a machine-readable audit.
"""

import json
import os
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import match  # noqa: E402

BENCH = Path(os.environ.get("STELLAMEM_BENCH_DIR", "<USER_HOME>/.dsh/memories/benchmark"))
SOURCE = BENCH / "results/extra_6f_all.json"
OUT = BENCH / "results/extra_audit_20261031.json"
COVERAGE = 0.72


def lines_of(sample):
    path = BENCH / "samples" / sample
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if len(line.strip()) >= 6]


def best_coverage(text, pool):
    best = 0.0
    for line in pool:
        best = max(best, match.coverage(text, line))
    return best


def main():
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    items = data["items"]
    cache = {}
    buckets = Counter()
    rows = []
    for item in items:
        sample = item.get("sample", "")
        pool = cache.setdefault(sample, lines_of(sample))
        text = str(item.get("value") or "")
        cov = best_coverage(text, pool)
        cat = item.get("cat", "未标")
        if cat == "无源" and cov >= COVERAGE:
            cat = "有源（原标无源）"
        buckets[cat] += 1
        rows.append({**item, "coverage": round(cov, 3), "bucket": cat})

    summary = {
        "itemized": len(rows),
        "reported_extras_6j": 272,
        "unaudited": 272 - len(rows),
        "buckets": dict(buckets),
        "source_file": str(SOURCE),
    }
    OUT.write_text(json.dumps({"summary": summary, "items": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    covered = sum(1 for r in rows if r["coverage"] >= COVERAGE)
    print(f"能在原文找到依据的: {covered}/{len(rows)} ({covered/len(rows):.1%})")
    print("写入", OUT)


if __name__ == "__main__":
    main()
