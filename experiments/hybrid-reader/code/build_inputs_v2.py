#!/usr/bin/env python3
"""Apply prefilter_v2 to eval2 inputs -> v62_inputs.jsonl; audit gold
survival (must be 100%) and diff removals/contexts against the v1 filter
(hybrid_inputs.jsonl + filter_audit.json). Gold is read ONLY for the
survival audit; it never enters the pipeline inputs."""
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
HP = os.path.dirname(HERE)
RT = os.path.expanduser("~/workspace/reader-train")
sys.path.insert(0, HERE)
sys.path.insert(0, HP)

from prefilter_v2 import prefilter_v2
from records import build_context, parse_context, parse_question_date

inputs = [json.loads(l) for l in open(os.path.join(RT, "eval2", "eval2_inputs.jsonl"), encoding="utf-8")]
gold = {json.loads(l)["qid"]: json.loads(l) for l in open(os.path.join(RT, "eval2", "eval2.jsonl"), encoding="utf-8")}
old_inputs = {json.loads(l)["qid"]: json.loads(l) for l in open(os.path.join(HP, "hybrid_inputs.jsonl"), encoding="utf-8")}
old_audit = json.load(open(os.path.join(HP, "filter_audit.json"), encoding="utf-8"))
assert len(inputs) == 420

out_rows = []
audit = {"n": 0, "gold_total": 0, "gold_survived": 0, "survival_failures": [],
         "removals_by_rule": Counter(), "removals_by_klass": Counter(),
         "questions_with_removals": 0, "per_question": {}}
changed_contexts = []
for row in inputs:
    qid = row["qid"]
    header, pairs = parse_context(row["context"])
    records = [rec for _, rec in pairs]
    raw_by_id = {rec["id"]: raw for raw, rec in pairs}
    qdate = parse_question_date(row["question_date"])
    kept, removals = prefilter_v2(records, row["question"], qdate)
    kept_ids = {r["id"] for r in kept}
    kept_pairs = [(raw_by_id[r["id"]], r) for r in kept]
    new_row = dict(row)
    new_row["context"] = build_context(header, kept_pairs)
    out_rows.append(new_row)
    if new_row["context"] != old_inputs[qid]["context"]:
        changed_contexts.append(qid)

    g = gold[qid]
    audit["n"] += 1
    audit["gold_total"] += len(g["gold_citations"])
    audit["gold_survived"] += sum(1 for c in g["gold_citations"] if c in kept_ids)
    lost = [c for c in g["gold_citations"] if c not in kept_ids]
    if lost:
        audit["survival_failures"].append({"qid": qid, "klass": g["klass"],
                                           "lost_gold_ids": lost})
    if removals:
        audit["questions_with_removals"] += 1
    for rm in removals:
        audit["removals_by_rule"][rm["rule"]] += 1
        audit["removals_by_klass"][g["klass"]] += 1
    old_removed = set(old_audit["per_question"][qid]["removed_ids"])
    new_removed = {r["id"] for r in removals}
    audit["per_question"][qid] = {
        "klass": g["klass"], "sub": g.get("sub"),
        "removed_ids": sorted(new_removed), "removals": removals,
        "n_before": len(records), "n_after": len(kept),
        "removed_vs_v1": {"added": sorted(new_removed - old_removed),
                          "dropped": sorted(old_removed - new_removed)}}

with open(os.path.join(HERE, "v62_inputs.jsonl"), "w", encoding="utf-8") as fh:
    for r in out_rows:
        fh.write(json.dumps(r, ensure_ascii=False) + "\n")

audit["removals_by_rule"] = dict(audit["removals_by_rule"])
audit["removals_by_klass"] = dict(audit["removals_by_klass"])
audit["gold_survival_rate"] = audit["gold_survived"] / audit["gold_total"]
audit["contexts_changed_vs_v1"] = changed_contexts
with open(os.path.join(HERE, "filter_audit_v2.json"), "w", encoding="utf-8") as fh:
    json.dump(audit, fh, ensure_ascii=False, indent=1)

print(f"questions={audit['n']} gold {audit['gold_survived']}/{audit['gold_total']} "
      f"survival={audit['gold_survival_rate']}")
print("removals_by_rule:", audit["removals_by_rule"])
print("removals_by_klass:", audit["removals_by_klass"])
print("questions_with_removals:", audit["questions_with_removals"])
print("survival_failures:", audit["survival_failures"][:10])
print("contexts changed vs v1:", len(changed_contexts), changed_contexts)
diffs = {q: d["removed_vs_v1"] for q, d in audit["per_question"].items()
         if d["removed_vs_v1"]["added"] or d["removed_vs_v1"]["dropped"]}
print("removal diffs vs v1:", json.dumps(diffs, ensure_ascii=False))
