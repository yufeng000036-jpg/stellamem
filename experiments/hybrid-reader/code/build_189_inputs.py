#!/usr/bin/env python3
"""Build the V6.2 189-question full-chain inputs: B2 rows with the
prefilter_v2 evidence view applied (same rebuild mechanics as the eval2
v62 inputs). Output: b2_inputs_v62.jsonl + filter_audit_189.json.
No gold is read here: the 189 set is an observational regression and the
B2 rows carry no gold citations to audit against."""

import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
HP = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, HP)

from prefilter_v2 import prefilter_v2 as filter_context  # noqa: E402
from records import build_context, parse_context, parse_question_date  # noqa: E402

B2 = os.path.expanduser("~/workspace/stellamem-takeover/v4-reader-exp-20261121/b2_rows.jsonl")

rows = [json.loads(line) for line in open(B2, encoding="utf-8")]
assert len(rows) == 189, len(rows)

out_rows = []
audit = {
    "n": 0,
    "removals_by_rule": Counter(),
    "questions_with_removals": 0,
    "per_question": {},
}
for row in rows:
    header, pairs = parse_context(row["context"])
    records = [rec for _, rec in pairs]
    raw_by_id = {rec["id"]: raw for raw, rec in pairs}
    qdate = parse_question_date(row["question_date"])
    kept, removals = filter_context(records, row["question"], qdate)
    kept_pairs = [(raw_by_id[r["id"]], r) for r in kept]
    new_row = {
        "qid": row["qid"],
        "set": "b2",
        "question": row["question"],
        "question_date": row["question_date"],
        "context": build_context(header, kept_pairs),
    }
    out_rows.append(new_row)
    audit["n"] += 1
    if removals:
        audit["questions_with_removals"] += 1
    for rm in removals:
        audit["removals_by_rule"][rm["rule"]] += 1
    audit["per_question"][row["qid"]] = {
        "removed_ids": [r["id"] for r in removals],
        "n_before": len(records),
        "n_after": len(kept),
    }

with open(os.path.join(HERE, "b2_inputs_v62.jsonl"), "w", encoding="utf-8") as fh:
    for r in out_rows:
        fh.write(json.dumps(r, ensure_ascii=False) + "\n")
audit["removals_by_rule"] = dict(audit["removals_by_rule"])
with open(os.path.join(HERE, "filter_audit_189.json"), "w", encoding="utf-8") as fh:
    json.dump(audit, fh, ensure_ascii=False, indent=1)
print(
    "wrote b2_inputs_v62.jsonl n=",
    len(out_rows),
    "removals:",
    audit["removals_by_rule"],
    "questions_with_removals:",
    audit["questions_with_removals"],
)
