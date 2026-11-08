"""Independent counterexamples and both historical rounds, without model calls."""

import copy
import csv
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1] / "benchmark/longmemeval"
SPEC = importlib.util.spec_from_file_location("score_integrity", BENCH / "score_integrity.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def fixture(**changes):
    row = dict(
        question_id="synthetic",
        category="temporal-reasoning",
        judge_main=True,
        judge_hypothesis="42",
        error=None,
        abstain=False,
        correct_abstention=None,
        citation_valid=True,
        cited_n=1,
    )
    row.update(changes)
    return row


class ScoreIntegrityTests(unittest.TestCase):
    def test_normal_answer_is_unchanged_and_does_not_claim_entailment(self):
        row = fixture()
        before = copy.deepcopy(row)
        result = audit.audit_row(row)
        self.assertTrue(result["corrected_correct"])
        self.assertTrue(result["citation_qualified_correct"])
        self.assertIsNone(result["semantic_support"])
        self.assertEqual(row, before)

    def test_empty_answers_cannot_pass_even_with_positive_judge(self):
        for answer in ("", " ", "\n\t", "\u3000"):
            with self.subTest(answer=answer):
                result = audit.audit_row(fixture(judge_hypothesis=answer))
                self.assertFalse(result["corrected_correct"])
                self.assertIn("empty_answer", result["issues"])

    def test_execution_failures_never_count_as_answers_or_refusals(self):
        for error in ("ChunkFailure", "BudgetExceeded", "JSONDecodeError", "ValueError"):
            for absent in (False, True):
                with self.subTest(error=error, absent=absent):
                    row = fixture(error=error)
                    if absent:
                        row.update(category="abstention", abstain=True, correct_abstention=True)
                    result = audit.audit_row(row)
                    self.assertFalse(result["corrected_correct"])
                    self.assertIn("execution_error", result["issues"])

    def test_no_evidence_keeps_answer_accuracy_separate(self):
        result = audit.audit_row(fixture(citation_valid=False, cited_n=0))
        self.assertTrue(result["corrected_correct"])
        self.assertFalse(result["citation_qualified_correct"])
        self.assertIn("no_valid_cited_evidence", result["issues"])

    def test_correct_empty_refusal_does_not_require_citation_or_positive_judge(self):
        for answer in ("", audit.ABSTENTION_MARKER):
            result = audit.audit_row(
                fixture(
                    category="abstention",
                    abstain=True,
                    correct_abstention=True,
                    judge_main=False,
                    judge_hypothesis=answer,
                    citation_valid=False,
                    cited_n=0,
                )
            )
            self.assertTrue(result["correct_abstention"])
            self.assertTrue(result["corrected_correct"])

    def test_judge_cannot_override_failed_refusal(self):
        result = audit.audit_row(fixture(category="abstention", correct_abstention=False))
        self.assertFalse(result["correct_abstention"])
        self.assertTrue(result["false_positive_detected"])

    def test_refusing_everything_fails_answerable_controls(self):
        result = audit.audit_row(fixture(abstain=True, judge_hypothesis=audit.ABSTENTION_MARKER))
        self.assertFalse(result["corrected_correct"])

    def test_malformed_tracking_fields_fail_closed(self):
        for changes in (
            {"abstain": "yes"},
            {"judge_main": 1},
            {"judge_hypothesis": []},
            {"cited_n": -1},
            {"cited_n": 1.5},
            {"cited_n": True},
            {"error": False},
        ):
            with self.subTest(changes=changes):
                result = audit.audit_row(fixture(**changes))
                self.assertFalse(result["corrected_correct"])
                self.assertIn("malformed_tracking_row", result["issues"])

    def test_missing_and_contradictory_fields_cannot_pass(self):
        for changes in (
            {"abstain": None},
            {"cited_n": 0},
            {"category": "abstention", "correct_abstention": True, "abstain": False},
            {"judge_hypothesis": audit.ABSTENTION_MARKER},
        ):
            with self.subTest(changes=changes):
                self.assertFalse(audit.audit_row(fixture(**changes))["corrected_correct"])

    def test_full_historical_rounds_detect_known_false_positives_without_mutation(self):
        for date, original, corrected in (("20261106", 175, 172), ("20261108", 177, 174)):
            path = BENCH / f"tracking_table_{date}.csv"
            before = path.read_bytes()
            report = audit.audit_file(path)
            summary = report["summary"]
            self.assertEqual(summary["all"]["n"], 500)
            self.assertEqual(summary["all"]["legacy_judge_correct"], original)
            self.assertEqual(summary["all"]["corrected_correct"], corrected)
            self.assertEqual(summary["abstention"]["corrected_correct"], 24)
            self.assertEqual(summary["abstention"]["n"], 30)
            flagged = {r["question_id"] for r in report["rows"] if r["false_positive_detected"]}
            self.assertEqual(flagged, {"gpt4_a1b77f9c", "gpt4_372c3eed_abs", "6456829e_abs"})
            self.assertEqual(before, path.read_bytes())

    def test_cli_detects_anomalies_and_refuses_overwriting_input(self):
        source = BENCH / "tracking_table_20261108.csv"
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "audit.json"
            cmd = [
                sys.executable,
                str(BENCH / "score_integrity.py"),
                str(source),
                "--out",
                str(out),
            ]
            proc = subprocess.run(cmd, capture_output=True, encoding="utf-8")
            self.assertEqual(proc.returncode, 1, proc.stderr)
            self.assertEqual(
                json.loads(out.read_text(encoding="utf-8"))["summary"]["all"]["n"], 500
            )
            before = out.read_bytes()
            self.assertEqual(subprocess.run(cmd, capture_output=True).returncode, 2)
            self.assertEqual(before, out.read_bytes())
            cmd[-1] = str(source)
            before = source.read_bytes()
            self.assertEqual(subprocess.run(cmd, capture_output=True).returncode, 2)
            self.assertEqual(before, source.read_bytes())

    def test_duplicate_or_empty_datasets_are_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.csv"
            for rows in ([], [fixture(), fixture()]):
                with path.open("w", newline="", encoding="utf-8") as stream:
                    writer = csv.DictWriter(stream, fieldnames=sorted(audit.REQUIRED))
                    writer.writeheader()
                    writer.writerows(rows)
                with self.assertRaises(ValueError):
                    audit.audit_file(path)


if __name__ == "__main__":
    unittest.main()
