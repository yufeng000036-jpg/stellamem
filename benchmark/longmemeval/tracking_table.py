#!/usr/bin/env python3
"""Build the per-question tracking table for Stellamem LongMemEval route B.

Usage:
    python tracking_table.py --results <this-round results.json> \
        --prev-results <prev-round results.json> \
        --oracle <longmemeval_oracle.json> --out <table.csv>

Inputs are the evaluation evidence files kept with each run report (not
committed to this repo): ``results.json`` for this round and the previous
round, plus the oracle (public HF dataset ``xiaowu0162/longmemeval-cleaned``,
SHA256 ``821a2034d219ab45846873dd14c14f12cfe7776e73527a483f9dac095d38620c``).
No private user data is involved: the table holds public benchmark question
IDs, categories and per-stage metrics only.

Chain stages, in order (main arm only):
  source -> extract -> retrieve -> project/cite -> answer/use
Loss stage = first stage that breaks, computed ONLY for questions the
main arm got wrong (judge False, or correct_abstention False). Passed
questions are "pass" regardless of partial recall.
  - extract: error is not None, or extracted == 0
  - retrieve: retrieval_recall_at_5 == 0 (gold evidence never retrieved)
  - project/cite: no cited sessions, or citation_valid is False
  - answer/use: evidence retrieved AND cited, but judge still False
Partial recall (0 < recall@5 < 1) on a failed question is recorded as a
contributing factor in loss_detail, not as the first break.
Abstention questions have no gold retrieval target (recall fields are
null); their chain is: abstain? -> correct? A wrong abstention answer
is loss_stage "answer"; whether any session was retrieved is recorded
separately (retrieved_n) so the "irrelevant evidence -> hard answer"
hypothesis can be checked, not assumed.

Outputs: CSV (one row per question) + summary printed to stdout.
"""

import argparse
import csv
import json
from collections import Counter, defaultdict

from score_integrity import audit_row, summarize


def judge_label(arm):
    """judge is a dict {'judge_response': 'yes'/'no', 'judge_label': bool,
    'hypothesis': <answer text the judge saw>}; flatten to the bool."""
    j = arm.get("judge")
    if isinstance(j, dict):
        return j.get("judge_label")
    return j


def judge_hypothesis(arm):
    j = arm.get("judge")
    if isinstance(j, dict):
        return j.get("hypothesis") or ""
    return ""


def gold_evidence_role(q):
    """Role ('user'/'assistant'/'mixed'/None) of the has_answer turn(s)
    inside the gold answer sessions. Used for the user/assistant source split."""
    roles = set()
    gold = set(q.get("answer_session_ids") or [])
    for sid, turns in zip(q.get("haystack_session_ids") or [], q.get("haystack_sessions") or []):
        if sid in gold:
            for t in turns:
                if t.get("has_answer"):
                    roles.add(t.get("role"))
    if not roles:
        return ""
    if len(roles) == 1:
        return roles.pop()
    return "mixed"


def loss_stage(case, main, q):
    cat = case["category"]
    if cat == "abstention":
        if main.get("correct_abstention"):
            return "pass", ""
        detail = "answered instead of abstaining"
        if main.get("retrieved_sessions"):
            detail += "; retrieved %d session(s) before answering" % len(main["retrieved_sessions"])
        return "answer", detail
    if judge_label(main) is True:
        return "pass", ""
    if main.get("error") is not None or (main.get("extracted") or 0) == 0:
        return "extract", "error=%s extracted=%s logical_calls=%s" % (
            main.get("error"),
            main.get("extracted"),
            main.get("logical_calls"),
        )
    r5 = main.get("retrieval_recall_at_5")
    if r5 is None or r5 == 0:
        return "retrieve", "recall@5=%s" % (r5,)
    if not main.get("cited_sessions") or main.get("citation_valid") is not True:
        return "project/cite", "recall@5=%.3f cited=%s citation_valid=%s" % (
            r5,
            len(main.get("cited_sessions") or []),
            main.get("citation_valid"),
        )
    detail = "recall@5=%.3f citation_valid=true but judge=false" % r5
    if r5 < 1.0:
        detail += " (partial recall: contributing factor)"
    return "answer/use", detail


def build(results_path, prev_results_path, oracle_path, out_path):
    rerun2 = json.load(open(results_path))
    rerun1 = json.load(open(prev_results_path))
    oracle = {q["question_id"]: q for q in json.load(open(oracle_path))}
    prev = {c["question_id"]: c["arms"]["main"] for c in rerun1["cases"]}

    assert rerun2["n_questions"] == 500 and len(oracle) == 500
    print("round repo_sha=%s prev repo_sha=%s" % (rerun2.get("repo_sha"), rerun1.get("repo_sha")))

    rows = []
    for case in rerun2["cases"]:
        qid = case["question_id"]
        q = oracle[qid]
        main = case["arms"]["main"]
        direct = case["arms"]["direct"]
        none = case["arms"]["none"]
        pm = prev[qid]
        stage, detail = loss_stage(case, main, q)
        rows.append(
            {
                "question_id": qid,
                "category": case["category"],
                "in_manifest": case["in_manifest"],
                "gold_sessions_n": len(q.get("answer_session_ids") or []),
                "gold_evidence_role": gold_evidence_role(q),
                "extracted_n": main.get("extracted"),
                "extracted_coverage": main.get("extracted_evidence_turn_coverage"),
                "recall@1": main.get("retrieval_recall_at_1"),
                "recall@5": main.get("retrieval_recall_at_5"),
                "retrieved_n": len(main.get("retrieved_sessions") or []),
                "cited_n": len(main.get("cited_sessions") or []),
                "citation_valid": main.get("citation_valid"),
                "citation_gold_recall": main.get("citation_gold_session_recall"),
                "judge_main": judge_label(main),
                "judge_hypothesis": judge_hypothesis(main),
                "exact_main": main.get("exact_match"),
                "abstain": main.get("abstain"),
                "correct_abstention": main.get("correct_abstention"),
                "error": main.get("error"),
                "logical_calls": main.get("logical_calls"),
                "loss_stage": stage,
                "loss_detail": detail,
                "judge_prev_round": judge_label(pm),
                "judge_direct": judge_label(direct),
                "judge_none": judge_label(none),
            }
        )

    # Historical judge/loss fields remain verbatim. New consumers get a
    # separately named corrected verdict; failures cannot masquerade as passes.
    audited = [audit_row(row) for row in rows]
    for row, checked in zip(rows, audited):
        row["integrity_correct"] = checked["corrected_correct"]
        row["integrity_citation_qualified"] = checked["citation_qualified_correct"]
        row["integrity_issues"] = ";".join(checked["issues"])
    print("score-integrity-v1:", json.dumps(summarize(audited), sort_keys=True))

    fields = list(rows[0].keys())
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    # --- validation + summary (must reproduce the rerun report totals) ---
    n = len(rows)
    judge_pass = sum(1 for r in rows if r["judge_main"] is True)
    non_ab = [r for r in rows if r["category"] != "abstention"]
    r5 = [r["recall@5"] for r in non_ab if r["recall@5"] is not None]
    ab = [r for r in rows if r["category"] == "abstention"]
    ab_ok = sum(1 for r in ab if r["correct_abstention"])
    print("rows:", n)
    print("legacy judge pass main: %d/500 = %.3f" % (judge_pass, judge_pass / 500))
    print("recall@5 mean non-abstention: %.3f" % (sum(r5) / len(r5)))
    print("abstention correct: %d/30 = %.3f" % (ab_ok, ab_ok / 30))
    print("loss_stage counts:", dict(Counter(r["loss_stage"] for r in rows)))
    print("loss by category:")
    by_cat = defaultdict(Counter)
    for r in rows:
        by_cat[r["category"]][r["loss_stage"]] += 1
    for cat in sorted(by_cat):
        print(" ", cat, dict(by_cat[cat]))
    print(
        "judge delta vs prev round: improved %d, regressed %d, unchanged %d"
        % (
            sum(1 for r in rows if r["judge_main"] is True and r["judge_prev_round"] is not True),
            sum(1 for r in rows if r["judge_main"] is not True and r["judge_prev_round"] is True),
            sum(1 for r in rows if (r["judge_main"] is True) == (r["judge_prev_round"] is True)),
        )
    )
    print(
        "abstention wrong answers with retrieved evidence: %d/%d"
        % (
            sum(1 for r in ab if not r["correct_abstention"] and r["retrieved_n"] > 0),
            sum(1 for r in ab if not r["correct_abstention"]),
        )
    )
    print("loss by gold_evidence_role (non-abstention, judge failed):")
    by_role = defaultdict(Counter)
    for r in non_ab:
        by_role[r["gold_evidence_role"] or "unknown"][r["loss_stage"]] += 1
    for role in sorted(by_role):
        print(" ", role, dict(by_role[role]))
    print("CSV:", out_path)
    return rows


def main():
    p = argparse.ArgumentParser(description="Build the LongMemEval per-question tracking table.")
    p.add_argument("--results", required=True, help="this round's results.json")
    p.add_argument("--prev-results", required=True, help="previous round's results.json")
    p.add_argument("--oracle", required=True, help="longmemeval_oracle.json")
    p.add_argument("--out", required=True, help="output CSV path")
    args = p.parse_args()
    build(args.results, args.prev_results, args.oracle, args.out)


if __name__ == "__main__":
    main()
