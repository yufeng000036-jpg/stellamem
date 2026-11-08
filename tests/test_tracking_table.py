"""The per-question tracking table must reconcile with the rerun report.

The committed CSV is the stage-level evidence chain for the 2026-11-06
third LongMemEval round (main c46a6fa): 500 rows, one per question.
If the CSV and the report disagree, the table is not trustworthy.
"""

import csv
import importlib.util
import sys
import unittest
from collections import Counter
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1] / "benchmark/longmemeval"
sys.path.insert(0, str(BENCH))
spec = importlib.util.spec_from_file_location(
    "longmemeval_tracking_table", BENCH / "tracking_table.py"
)
tracking = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tracking)

CSV_PATH = BENCH / "tracking_table_20261106.csv"
KNOWN_CATEGORIES = {
    "abstention",
    "knowledge-update",
    "multi-session",
    "single-session-assistant",
    "single-session-preference",
    "single-session-user",
    "temporal-reasoning",
}
KNOWN_STAGES = {"pass", "extract", "retrieve", "project/cite", "answer/use", "answer"}


def load_rows():
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


class TrackingTableReconciliationTests(unittest.TestCase):
    def test_csv_reconciles_with_rerun_report(self):
        rows = load_rows()
        self.assertEqual(len(rows), 500)
        self.assertEqual(len({r["question_id"] for r in rows}), 500)
        self.assertTrue({r["category"] for r in rows} <= KNOWN_CATEGORIES)
        self.assertTrue({r["loss_stage"] for r in rows} <= KNOWN_STAGES)
        judge_pass = sum(1 for r in rows if r["judge_main"] == "True")
        self.assertEqual(judge_pass, 175)
        non_ab = [r for r in rows if r["category"] != "abstention"]
        r5 = [float(r["recall@5"]) for r in non_ab if r["recall@5"] not in ("", None)]
        self.assertEqual(len(non_ab), 470)
        self.assertAlmostEqual(sum(r5) / len(r5), 0.870, places=3)
        ab = [r for r in rows if r["category"] == "abstention"]
        self.assertEqual(len(ab), 30)
        self.assertEqual(sum(1 for r in ab if r["correct_abstention"] == "True"), 24)
        self.assertEqual(
            dict(Counter(r["loss_stage"] for r in rows)),
            {
                "project/cite": 211,
                "pass": 173,
                "answer/use": 61,
                "extract": 35,
                "retrieve": 14,
                "answer": 6,
            },
        )


def _main(**over):
    m = {
        "extracted": 5,
        "retrieval_recall_at_5": 1.0,
        "cited_sessions": ["s1"],
        "citation_valid": True,
        "judge": {"judge_label": False, "judge_response": "no", "hypothesis": ""},
        "error": None,
        "logical_calls": 5,
        "correct_abstention": None,
        "retrieved_sessions": ["s1"],
    }
    m.update(over)
    return m


class LossStageTests(unittest.TestCase):
    def test_judge_pass_is_pass_even_with_partial_recall(self):
        stage, _ = tracking.loss_stage(
            {"category": "multi-session"},
            _main(judge={"judge_label": True}, retrieval_recall_at_5=0.5),
            {},
        )
        self.assertEqual(stage, "pass")

    def test_error_is_extract(self):
        stage, _ = tracking.loss_stage(
            {"category": "multi-session"}, _main(error="ChunkFailure"), {}
        )
        self.assertEqual(stage, "extract")

    def test_zero_extracted_is_extract(self):
        stage, _ = tracking.loss_stage({"category": "multi-session"}, _main(extracted=0), {})
        self.assertEqual(stage, "extract")

    def test_zero_recall_is_retrieve(self):
        stage, _ = tracking.loss_stage(
            {"category": "multi-session"}, _main(retrieval_recall_at_5=0), {}
        )
        self.assertEqual(stage, "retrieve")

    def test_no_cited_sessions_is_project_cite(self):
        stage, _ = tracking.loss_stage({"category": "multi-session"}, _main(cited_sessions=[]), {})
        self.assertEqual(stage, "project/cite")

    def test_invalid_citation_is_project_cite(self):
        stage, _ = tracking.loss_stage(
            {"category": "multi-session"}, _main(citation_valid=False), {}
        )
        self.assertEqual(stage, "project/cite")

    def test_cited_but_judged_wrong_is_answer_use(self):
        stage, detail = tracking.loss_stage({"category": "multi-session"}, _main(), {})
        self.assertEqual(stage, "answer/use")
        self.assertIn("citation_valid=true but judge=false", detail)

    def test_abstention_correct_is_pass(self):
        stage, _ = tracking.loss_stage(
            {"category": "abstention"}, _main(correct_abstention=True), {}
        )
        self.assertEqual(stage, "pass")

    def test_abstention_answered_is_answer(self):
        stage, detail = tracking.loss_stage(
            {"category": "abstention"}, _main(correct_abstention=False), {}
        )
        self.assertEqual(stage, "answer")
        self.assertIn("retrieved 1 session(s)", detail)


class GoldEvidenceRoleTests(unittest.TestCase):
    def _q(self, roles):
        return {
            "answer_session_ids": ["s1"],
            "haystack_session_ids": ["s1", "s2"],
            "haystack_sessions": [
                [{"role": r, "has_answer": True} for r in roles],
                [{"role": "user", "has_answer": False}],
            ],
        }

    def test_user_only(self):
        self.assertEqual(tracking.gold_evidence_role(self._q(["user"])), "user")

    def test_assistant_only(self):
        self.assertEqual(tracking.gold_evidence_role(self._q(["assistant"])), "assistant")

    def test_mixed_roles(self):
        self.assertEqual(tracking.gold_evidence_role(self._q(["user", "assistant"])), "mixed")

    def test_no_answer_turn(self):
        q = self._q(["user"])
        q["haystack_sessions"][0][0]["has_answer"] = False
        self.assertEqual(tracking.gold_evidence_role(q), "")


if __name__ == "__main__":
    unittest.main()
