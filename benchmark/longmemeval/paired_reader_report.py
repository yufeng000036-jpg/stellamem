"""Aggregate paired local-reader trials without dropping changed or unexecuted cases."""

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean

from freeze import DATA_SHA
from reader_pair import category, selected
from session_balance_pair import sample
import hashlib


def summarize(expected, records):
    expected = {row["question_id"]: category(row) for row in expected}
    arms = {"baseline": {}, "candidate": {}}
    for r in records:
        q, arm = r["question_id"], r["arm"]
        if q not in expected or r["category"] != expected[q] or arm not in arms or q in arms[arm]:
            raise ValueError("unexpected or duplicate pair member")
        arms[arm][q] = r

    def stats(ids):
        results = {}
        for arm, by_id in arms.items():
            rows = [by_id[q] for q in ids if q in by_id]
            paid = [r for r in rows if not r.get("identical_reuse")]
            errors = sum("error" in r for r in rows)
            answerable = [r for r in rows if r["category"] != "abstention"]
            absent = [r for r in rows if r["category"] == "abstention"]

            def avg(values):
                return round(mean(values), 4) if values else None

            results[arm] = {
                "n": len(ids),
                "correct_exact": sum(r["exact"] is True for r in rows),
                "execution_errors": errors,
                "not_executed": len(ids) - len(rows),
                "correct_refusals": sum(r["exact"] is True for r in absent),
                "unanswerable_n": sum(expected[q] == "abstention" for q in ids),
                "false_refusals": sum(r.get("abstain") is True for r in answerable),
                "answerable_n": sum(expected[q] != "abstention" for q in ids),
                "valid_ref_answerable": sum(r.get("citation_valid") is True for r in answerable),
                "gold_turn_cited_answerable": sum(
                    r.get("gold_turn_cited") is True for r in answerable
                ),
                "full_gold_turns": avg(
                    [r["full_gold_turns"] for r in answerable if "full_gold_turns" in r]
                ),
                "turn_precision": avg(
                    [r["turn_precision"] for r in answerable if "turn_precision" in r]
                ),
                "turn_recall_at_5": avg([r["recall5"] for r in answerable if "recall5" in r]),
                "mean_chars": avg([r["chars"] for r in rows]),
                "mean_prompt_tokens": avg(
                    [r["usage"]["prompt_tokens"] for r in rows if "usage" in r]
                ),
                "mean_completion_tokens": avg(
                    [r["usage"]["completion_tokens"] for r in rows if "usage" in r]
                ),
                "fresh_calls_with_usage": sum("usage" in r for r in paid),
                "exact_request_reuses": len(rows) - len(paid),
                "mean_fresh_seconds": avg([r["seconds"] for r in paid if "usage" in r]),
            }
        changes = defaultdict(list)
        for q in ids:
            a, b = arms["baseline"].get(q), arms["candidate"].get(q)
            if not a or not b:
                changes["unpaired"].append(q)
                continue
            if "error" in a or "error" in b:
                changes["response_error"].append(q)
            if a["exact"] != b["exact"]:
                changes["wrong_to_right" if b["exact"] else "right_to_wrong"].append(q)
        return {"arms": results, "transitions": dict(changes)}

    return {
        "overall": stats(list(expected)),
        "by_category": {
            group: stats([q for q in expected if expected[q] == group])
            for group in sorted(set(expected.values()))
        },
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("dataset", type=Path)
    p.add_argument("audit", type=Path)
    p.add_argument("trial", choices=["prompt", "retrieval"])
    args = p.parse_args()
    raw = args.dataset.read_bytes()
    if hashlib.sha256(raw).hexdigest() != DATA_SHA:
        raise ValueError("dataset digest mismatch")
    cases = json.loads(raw)
    planned = selected(cases, 2) if args.trial == "prompt" else sample(cases)
    rows = [json.loads(line) for line in args.audit.read_text().splitlines()]
    print(json.dumps(summarize(planned, rows), indent=2))
