"""Merge the 4 full-run shards into one 500-question result.

Reads work/full/shardN/results.json (N=0..3) and work/full/shardN/usage.jsonl,
verifies 500 unique question_ids, aggregates with the FROZEN evaluate.py
aggregate(), adds judge_accuracy per arm and a by-category breakdown, totals
the cost from the usage logs (flash off-peak official prices, as in the
pilot), and writes work/full/results.json.

Cost model (DeepSeek official, flash, off-peak, checked 2026-11-06):
  input cache-miss $0.15 / 1M tokens, input cache-hit $0.003 / 1M,
  output $0.6 / 1M. CNY figures are approximate (platform settlement lags);
  USD is the reference, as in the pilot report.

Provenance: this is the run script used for all four LongMemEval rounds
(2026-11-06/08), copied into the repo with ONLY the path handling changed.
The four archived copies (~/workspace/longmemeval-{full,rerun,rerun2}-20261106/
and longmemeval-rerun3-20261108/merge.py) are byte-identical to each other
except for one hardcoded line:
``sys.path.insert(0, "/tmp/stellamem-lme[-rerun2|-rerun3]/benchmark/longmemeval")``
-- a /tmp clone path that no longer exists after a VM restart, so every
reproduction had to rebuild that path first (see
docs/verification/REPRODUCIBILITY-20261109.md). This copy resolves the
frozen evaluate.py relative to its own location instead (--repo, default:
the directory holding this file, which in the repo is
benchmark/longmemeval/ next to the frozen evaluate.py) and takes the run
directory as --run-dir (default: the directory holding this file, i.e.
the old behaviour when the script is dropped into a run root).
Scoring, cost model, breach signature and output schema are unchanged:
re-running this file on the archived round-4 shards reproduces
work/full/results.json byte-for-byte, as do rounds 1-3 (verified
2026-11-09, zero model calls).

Usage:
    python merge.py --run-dir <run root> [--repo <dir holding evaluate.py>]
"""

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARMS = ("main", "candidate", "none", "direct")

MISS = 0.15 / 1_000_000
HIT = 0.003 / 1_000_000
OUT = 0.6 / 1_000_000


def cost_of(call):
    miss = call.get("prompt_cache_miss_tokens") or 0
    hit = call.get("prompt_cache_hit_tokens") or 0
    out = call.get("completion_tokens") or 0
    # Fallback: if the cache split is missing, count all prompt tokens as miss.
    if miss == 0 and hit == 0 and call.get("prompt_tokens"):
        miss = call["prompt_tokens"]
    return miss * MISS + hit * HIT + out * OUT


def resolve_paths(argv=None):
    """Resolve (run_dir, repo_dir); defaults preserve the old behaviour."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=HERE,
        help="run root holding work/full/shardN (default: this file's directory)",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=HERE,
        help="directory holding the frozen evaluate.py (default: this file's directory)",
    )
    args = parser.parse_args(argv)
    return args.run_dir, args.repo


def main(argv=None):
    run_dir, repo_dir = resolve_paths(argv)
    sys.path.insert(0, str(repo_dir))
    from evaluate import aggregate  # noqa: E402

    full = run_dir / "work" / "full"
    rows = []
    usage_all = []
    preflights = []
    for i in range(4):
        shard = full / f"shard{i}"
        result = json.loads((shard / "results.json").read_text())
        rows.extend(result["cases"])
        preflights.append(result["preflight_usage"])
        for line in (shard / "usage.jsonl").read_text().splitlines():
            if line.strip():
                entry = json.loads(line)
                entry["shard"] = i
                usage_all.append(entry)
    qids = [r["question_id"] for r in rows]
    assert len(rows) == 500, f"expected 500 rows, got {len(rows)}"
    assert len(set(qids)) == 500, "duplicate question_ids"

    # Re-sequence usage entries in file order per shard, then globally.
    seq = 0
    for entry in usage_all:
        entry["global_seq"] = seq
        seq += 1

    summary = aggregate(rows)
    for arm in ARMS:
        rs = [r["arms"][arm] for r in rows]
        summary[arm]["judge_accuracy"] = (
            sum(1 for r in rs if r["judge"]["judge_label"]) / len(rs) if rs else None
        )
    # Deltas in a second pass: judge_accuracy must exist on every arm first
    # (computing main's delta before none's accuracy exists was a KeyError).
    for arm in ARMS:
        summary[arm]["judge_delta_vs_none"] = (
            summary[arm]["judge_accuracy"] - summary["none"]["judge_accuracy"]
            if summary[arm]["judge_accuracy"] is not None
            and summary["none"]["judge_accuracy"] is not None
            else None
        )
        summary[arm]["exact_delta_vs_none"] = (summary[arm]["exact_match"] or 0) - (
            summary["none"]["exact_match"] or 0
        )
    by_category = {
        k: aggregate([r for r in rows if r["category"] == k])
        for k in sorted({r["category"] for r in rows})
    }
    for cat, summ in by_category.items():
        for arm in ARMS:
            rs = [r["arms"][arm] for r in rows if r["category"] == cat]
            summ[arm]["judge_accuracy"] = (
                sum(1 for r in rs if r["judge"]["judge_label"]) / len(rs) if rs else None
            )

    # Cost totals by call kind (extract / answer / judge / preflight).
    by_kind = {}
    for entry in usage_all:
        kind = entry.get("kind") or "unknown"
        d = by_kind.setdefault(
            kind, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}
        )
        d["calls"] += 1
        d["prompt_tokens"] += entry.get("prompt_tokens") or 0
        d["completion_tokens"] += entry.get("completion_tokens") or 0
        d["cost_usd"] += cost_of(entry)
    total_usd = sum(d["cost_usd"] for d in by_kind.values())
    total_calls = sum(d["calls"] for d in by_kind.values())
    for d in by_kind.values():
        d["cost_usd"] = round(d["cost_usd"], 4)

    # 32-call budget breach signature (verified against the pilot's
    # 81507db6 raw output): error == "ChunkFailure" (pipeline wraps the
    # budget RuntimeError), extraction_failure_code == "RuntimeError",
    # logical_calls > 32 (the 33rd extraction call raises), extracted == 0.
    # The raw worker JSON under shardN/raw/ preserves the full evidence.
    budget_breach = [
        r["question_id"]
        for r in rows
        if r["arms"]["main"].get("error") == "ChunkFailure"
        and r["arms"]["main"].get("extraction_failure_code") == "RuntimeError"
        and (r["arms"]["main"].get("logical_calls") or 0) > 32
        and r["arms"]["main"].get("extracted") == 0
    ]
    extract_failed_main = [
        r["question_id"] for r in rows if r["arms"]["main"].get("error") is not None
    ]

    result = {
        "protocol": "v2-full-route-B",
        "repo_sha": json.loads((full / "shard0" / "results.json").read_text())["repo_sha"],
        "dataset_sha256": json.loads((full / "shard0" / "results.json").read_text())[
            "dataset_sha256"
        ],
        "n_questions": 500,
        "n_manifest_verified": sum(1 for r in rows if r.get("in_manifest")),
        "preflights": preflights,
        "elapsed_seconds_total": sum(
            json.loads((full / f"shard{i}" / "results.json").read_text())["elapsed_seconds"]
            for i in range(4)
        ),
        "summary": summary,
        "by_category": by_category,
        "cost": {
            "by_kind": by_kind,
            "total_calls": total_calls,
            "total_usd": round(total_usd, 4),
            "per_question_usd": round(total_usd / 500, 5),
            "pricing": "flash off-peak: input miss $0.15/M, hit $0.003/M, output $0.6/M",
        },
        "budget_breach_question_ids": budget_breach,
        "extract_failed_main_question_ids": extract_failed_main,
        "cases": sorted(rows, key=lambda r: r["case_id_sha256"]),
    }
    (full / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "n": len(rows),
                "summary": summary,
                "by_category_keys": sorted(by_category),
                "cost": result["cost"],
                "budget_breach": len(budget_breach),
                "extract_failed_main": len(extract_failed_main),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
