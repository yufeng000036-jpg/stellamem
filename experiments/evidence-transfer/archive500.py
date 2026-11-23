"""Post-hoc diagnostic on an earlier public project record. No reader inputs/outputs are generated."""

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from audit import digest, paired, read_json, verify_package


def main():
    p = argparse.ArgumentParser()
    p.add_argument("archive", type=Path)
    p.add_argument("package", type=Path)
    p.add_argument("out", type=Path)
    args = p.parse_args()
    verify_package(args.package)
    scores = args.archive / "results-20261108-summary.json"
    stages = args.archive / "tracking_table_20261108.csv"
    data = read_json(scores)
    with stages.open(encoding="utf-8", newline="") as f:
        table = list(csv.DictReader(f))
    cases = data["cases"]
    ids = {r["question_id"] for r in cases}
    if (
        len(cases) != 500
        or len(ids) != 500
        or {r["question_id"] for r in table} != ids
        or len(table) != 500
    ):
        raise ValueError("invalid archive cohort")
    flips = read_json(args.package / "results/chain189_result.json")["full189"]["flips"]
    eligible = [
        r for r in cases if r["arms"]["main"]["recall_at_5"] == 1 and r["category"] != "abstention"
    ]
    by_arm = {}
    for arm in ("main", "direct", "none"):
        v = [r["arms"][arm] for r in cases]
        by_arm[arm] = {
            "judge_true": sum(x["judge_label"] is True for x in v),
            "judge_and_valid_reference_nonrefusal": sum(
                x["judge_label"] is True
                and x["citation_valid"] is True
                and x["abstain"] is False
                and not x["error"]
                for x in v
            ),
        }
    gains = sum(
        r["arms"]["main"]["judge_label"] and not r["arms"]["direct"]["judge_label"] for r in cases
    )
    losses = sum(
        r["arms"]["direct"]["judge_label"] and not r["arms"]["main"]["judge_label"] for r in cases
    )
    report = {
        "scope": "Post hoc, historical oracle-session input; session recall is coarser than turn or entailment.",
        "sha256": {scores.name: digest(scores), stages.name: digest(stages)},
        "source": data["source"],
        "stages": dict(Counter(r["loss_stage"] for r in table)),
        "arms": by_arm,
        "complete_session_recall": {
            "n": len(eligible),
            "judge_false": sum(not r["arms"]["main"]["judge_label"] for r in eligible),
        },
        "main_vs_direct_judge": paired(500, gains, losses),
        "known_189_flip_ids_also_in_500": sum(r["qid"] in ids for r in flips),
        "known_189_flip_ids": len(flips),
    }
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
