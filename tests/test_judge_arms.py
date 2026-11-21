"""Resident regression for the judge must-fail arms.

The three arms are the audited false positives of the LongMemEval
stage-2 abstention scoring (2026-11-19): rows the LLM judge marked
correct that the exact audit rejected. Frozen as a test OF THE JUDGE
(处置 agreed on EigenFlux with 虾米, 2026-11-21): any judge
implementation that passes one of these rows is itself red, and a
judge's readings only enter a comparable set when its id and version
are pinned alongside them.
"""

import importlib.util
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BENCH = REPO / "benchmark" / "longmemeval"
sys.path.insert(0, str(BENCH))


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


judge_arms = load_module("judge_arms", BENCH / "judge_arms.py")

EXECUTION_ERROR_IDS = {"gpt4_70e84552_abs", "gpt4_372c3eed_abs"}
ANSWERED_ID = "6456829e_abs"


class TestArmSet(unittest.TestCase):
    def test_three_arms_with_recorded_ids(self):
        ids = {arm["question_id"] for arm in judge_arms.ARMS}
        self.assertEqual(ids, EXECUTION_ERROR_IDS | {ANSWERED_ID})

    def test_expected_verdicts_follow_the_behavior_axis(self):
        expected = {arm["question_id"]: arm["expected_verdict"] for arm in judge_arms.ARMS}
        for qid in EXECUTION_ERROR_IDS:
            self.assertEqual(expected[qid], "indeterminate")
        self.assertEqual(expected[ANSWERED_ID], "fail")

    def test_arms_digest_is_stable(self):
        self.assertEqual(judge_arms.arms_digest(), judge_arms.ARMS_DIGEST)


class TestCheckJudge(unittest.TestCase):
    def test_recorded_stage2_judge_is_red_on_all_three(self):
        verdicts = {qid: "pass" for qid in EXECUTION_ERROR_IDS | {ANSWERED_ID}}
        report = judge_arms.check_judge(verdicts, judge_id="stage2-judge", judge_version="as-run")
        self.assertFalse(report["ok"])
        for arm in report["arms"]:
            self.assertEqual(arm["status"], "passed_must_fail")

    def test_correct_judge_is_green(self):
        verdicts = {qid: "indeterminate" for qid in EXECUTION_ERROR_IDS}
        verdicts[ANSWERED_ID] = "fail"
        report = judge_arms.check_judge(verdicts, judge_id="judge-x", judge_version="1.0")
        self.assertTrue(report["ok"])
        self.assertTrue(report["comparable"])

    def test_execution_error_judged_fail_is_wrong_axis_not_a_pass(self):
        verdicts = {qid: "fail" for qid in EXECUTION_ERROR_IDS}
        verdicts[ANSWERED_ID] = "fail"
        report = judge_arms.check_judge(verdicts, judge_id="judge-x", judge_version="1.0")
        self.assertFalse(report["ok"])
        for arm in report["arms"]:
            if arm["question_id"] in EXECUTION_ERROR_IDS:
                self.assertEqual(arm["status"], "wrong_axis")
            else:
                self.assertEqual(arm["status"], "ok")

    def test_missing_verdict_is_not_ok(self):
        report = judge_arms.check_judge({}, judge_id="judge-x", judge_version="1.0")
        self.assertFalse(report["ok"])
        for arm in report["arms"]:
            self.assertEqual(arm["status"], "missing")

    def test_unpinned_judge_is_not_comparable(self):
        verdicts = {qid: "indeterminate" for qid in EXECUTION_ERROR_IDS}
        verdicts[ANSWERED_ID] = "fail"
        report = judge_arms.check_judge(verdicts, judge_id="judge-x")
        self.assertFalse(report["comparable"])


class TestSelfTest(unittest.TestCase):
    def test_module_self_test_passes(self):
        self.assertTrue(judge_arms.self_test())


if __name__ == "__main__":
    unittest.main()
