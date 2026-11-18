"""Join the historical route-B audit and replay; never infer semantic support from a turn id."""

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from freeze import DATA_SHA
from score_integrity import audit_file

HERE = Path(__file__).resolve().parent


def classify(row, audit, turn_rows, gold):
    projected = {r["message_id"] for r in turn_rows if r["stage"] == "projected"}
    cited = {r["message_id"] for r in turn_rows if r["cited"] == "true"}
    absent = row["category"] == "abstention"
    tags = set(audit["issues"])
    missing = gold - projected
    reasons = set()
    for r in turn_rows:
        if r["message_id"] not in missing:
            continue
        stage, reason = r["stage"], r["drop_reason"]
        if stage in {"review-refused", "not-extracted"}:
            reasons.add(stage)
        elif stage == "dropped":
            reasons.add(reason or "unexplained-drop")
    tags.update(reasons)
    if audit["corrected_correct"]:
        primary = (
            "pass" if audit["citation_qualified_correct"] else "correct-without-valid-reference"
        )
    elif row["error"] or audit["legacy_judge_correct"] is None:
        primary = "execution-or-verdict-unavailable"
    elif absent:
        primary = "answered-unanswerable" if row["abstain"] == "False" else "inspect-verdict"
    elif not gold:
        primary = "gold-turn-annotation-unavailable"
    elif not missing:
        primary = "all-gold-turns-represented-answer-wrong"
    elif projected & gold:
        primary = "some-gold-turns-represented"
    elif len(reasons) == 1:
        primary = next(iter(reasons))
    else:
        primary = "mixed-losses" if reasons else "missing-replay-trace"
    if audit["legacy_judge_correct"] and not audit["corrected_correct"]:
        tags.add("legacy-verdict-overruled")
    return {
        "question_id": row["question_id"],
        "category": row["category"],
        "primary": primary,
        "tags": "|".join(sorted(tags)),
        "gold_turns": len(gold),
        "represented": len(gold & projected),
        "gold_turns_cited": len(gold & cited),
        "corrected_correct": audit["corrected_correct"],
        "citation_qualified_correct": audit["citation_qualified_correct"],
    }


def report(dataset):
    raw = dataset.read_bytes()
    if hashlib.sha256(raw).hexdigest() != DATA_SHA:
        raise ValueError("unexpected oracle corpus")
    oracle = {r["question_id"]: r for r in json.loads(raw)}
    tracking = HERE / "tracking_table_20261108.csv"
    with tracking.open(newline="") as stream:
        questions = list(csv.DictReader(stream))
    audited = {r["question_id"]: r for r in audit_file(tracking)["rows"]}
    if len(oracle) != 500 or oracle.keys() != audited.keys():
        raise ValueError("incomplete or mismatched question ids")
    claims = defaultdict(list)
    with (HERE / "claim_tracking_20261108.csv").open(newline="") as stream:
        for row in csv.DictReader(stream):
            if row["question_id"] not in oracle:
                raise ValueError("unexpected claim id in replay")
            claims[row["question_id"]].append(row)
    results = []
    for row in questions:
        qid = row["question_id"]
        case = oracle[qid]
        gold = {
            f"{sid}:{i}"
            for sid, turns in zip(case["haystack_session_ids"], case["haystack_sessions"])
            for i, turn in enumerate(turns)
            if turn.get("has_answer")
        }
        results.append(classify(row, audited[qid], claims[qid], gold))
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    rows = report(args.dataset)
    with args.out.open("x", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys(), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps(Counter(r["primary"] for r in rows), sort_keys=True))
