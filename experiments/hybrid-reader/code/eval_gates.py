#!/usr/bin/env python3
"""Evaluate an assemble_v2 ab_result against the pre-registered gates
(unchanged from the V6 hybrid preregistration) and print full metrics.
Usage: python3 eval_gates.py ab_result_<tag>.json"""
import csv
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
RT = os.path.expanduser("~/workspace/reader-train")

path = sys.argv[1]
res = json.load(open(path))
tag = os.path.basename(path)[len("ab_result_"):-len(".json")]
flips = {r["qid"]: r for r in
         csv.DictReader(open(os.path.join(HERE, f"flips_{tag}.csv")))}
gold = {json.loads(l)["qid"]: json.loads(l)
        for l in open(os.path.join(RT, "eval2", "eval2.jsonl"))}
h = res["hybrid"]
by = h["by_klass"]
ab = res["hybrid_abstain_by_missing_type"]

date_c = by.get("date", {}).get("correct", 0)
multi_c = by.get("multi", {}).get("correct", 0)
oe = ab.get("only_expired", {})
oe_ca = oe.get("correct_abstain", 0)
combined_wrong = sum(by.get(k, {}).get("wrong_answer", 0)
                     for k in ("expired", "update", "conflict"))
mm = ab.get("multi_missing_one", {})
mm_ca = mm.get("correct_abstain", 0)

gates = [
    ("date correct >= 30/43", date_c, 30, date_c >= 30),
    ("multi correct >= 50/54", multi_c, 50, multi_c >= 50),
    ("only_expired correct_abstain >= 10/12", oe_ca, 10, oe_ca >= 10),
    ("expired+update+conflict wrong <= 2", combined_wrong, 2,
     combined_wrong <= 2),
    ("total over_abstain <= 17", h["over_abstain"], 17,
     h["over_abstain"] <= 17),
    ("gold survival (filter audit, separate file) == 100%", None, None, None),
]
print(f"== gates for {tag} ==")
for name, val, _thr, ok in gates:
    print(f"  {'PASS' if ok else ('FAIL' if ok is not None else ' n/a')}  "
          f"{name}  value={val}")
print(f"totals: correct={h['correct']} wrong={h['wrong_answer']} "
      f"over={h['over_abstain']} ca={h['correct_abstain']} "
      f"format_fail={h['format_fail']}")
print(f"citations valid among correct: {h['citation_valid_among_correct']}")
print(f"multi_missing_one correct_abstain: {mm_ca}/12 "
      f"(v2 assembly was 3, C1 baseline 6)")
print("by_klass:", json.dumps(by, ensure_ascii=False))
print("abstain_by_missing_type:", json.dumps(ab, ensure_ascii=False))
pos = sum(1 for f in flips.values() if f["direction"] in ("+", "1"))
neg = sum(1 for f in flips.values() if f["direction"] in ("-", "-1"))
print(f"flips vs C1 baseline: +{pos} / -{neg} (net {pos - neg:+d})")
simple_weak = {k: by.get(k) for k in ("simple", "weak", "similar")}
print("simple/weak/similar:", json.dumps(simple_weak, ensure_ascii=False))
