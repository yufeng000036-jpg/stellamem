"""Attach source evidence to the golden set, or mark the claim weak.

The 2026-09-20 gold set has no evidence for 255 of 393 claims (65%), which is
why it could not be re-checked. This walks each claim against its own sample and
attaches the best matching sentence when the deterministic matcher is confident.
Claims that cannot be anchored are marked `weak` and counted separately instead
of silently passing as truth.

Usage:
    python benchmark/enrich_gold.py --dry-run
    python benchmark/enrich_gold.py --apply
"""

import argparse
import json
import os
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import match  # noqa: E402

# Code ships in the repo; the annotated corpus lives with the user's memory root.
BENCH = Path(os.environ.get("STELLAMEM_BENCH_DIR", "<USER_HOME>/.dsh/memories/benchmark"))
SAMPLES = BENCH / "samples"
GOLDEN = BENCH / "golden"
BACKUP = BENCH / "golden_pre_enrich_20261031"
ANCHOR_THRESHOLD = 0.62
COVERAGE_THRESHOLD = 0.72


def sentences(text):
    parts = re.split(r"(?<=[。！？；\n])", text)
    out = []
    for part in parts:
        for line in part.split("\n"):
            line = line.strip(" \t-*#>")
            if len(line) >= 6:
                out.append(line[:200])
    return out


def best_anchor(claim, pool):
    needles = [str(claim.get("value") or "")]
    subject = str(claim.get("subject") or "").strip()
    if subject and subject.lower() not in {"user", "assistant", "unknown"}:
        needles.append(f"{subject} {claim.get('value') or ''}")
    best, best_text, best_cov = 0.0, "", 0.0
    for needle in needles:
        for line in pool:
            score = match.score(needle, line, None)
            cov = match.coverage(needle, line)
            combined = max(score, cov * 0.95)
            if combined > best:
                best, best_text, best_cov = combined, line, cov
    return best, best_text, best_cov


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    files = sorted(GOLDEN.glob("*_confirmed.json"))
    stats = Counter()
    per_kind = Counter()
    weak_examples = []
    total = 0

    for path in files:
        name = path.name.replace("_confirmed.json", "")
        sample_path = SAMPLES / f"{name}.md"
        if not sample_path.exists():
            continue
        pool = sentences(sample_path.read_text(encoding="utf-8"))
        data = json.loads(path.read_text(encoding="utf-8"))
        for claim in data.get("expected_claims", []):
            total += 1
            score, text, cov = best_anchor(claim, pool)
            if score >= ANCHOR_THRESHOLD or cov >= COVERAGE_THRESHOLD:
                claim["evidence"] = text
                claim["evidence_status"] = "anchored"
                claim["evidence_score"] = round(score, 3)
                claim["evidence_coverage"] = round(cov, 3)
                stats["anchored"] += 1
                per_kind[("anchored", claim.get("kind"))] += 1
            else:
                claim["evidence"] = ""
                claim["evidence_status"] = "weak"
                claim["evidence_score"] = round(score, 3)
                claim["evidence_coverage"] = round(cov, 3)
                stats["weak"] += 1
                per_kind[("weak", claim.get("kind"))] += 1
                if len(weak_examples) < 8:
                    weak_examples.append(f"{claim.get('kind')}: {str(claim.get('value'))[:40]}")
        if args.apply:
            BACKUP.mkdir(exist_ok=True)
            backup_path = BACKUP / path.name
            if not backup_path.exists():
                shutil.copy2(path, backup_path)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"gold 文件 {len(files)} 个，claims {total} 条")
    print(f"anchored {stats['anchored']} | weak {stats['weak']}")
    print("按类型 anchored/weak：")
    kinds = sorted({k for _, k in per_kind})
    for kind in kinds:
        print(f"  {kind:12s} {per_kind[('anchored', kind)]:3d} / {per_kind[('weak', kind)]:3d}")
    print("weak 示例:", weak_examples)
    print("APPLIED" if args.apply else "DRY-RUN（未写入）")


if __name__ == "__main__":
    main()
