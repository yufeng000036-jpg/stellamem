"""Evaluation mistakes must not turn failures into benchmark success."""

import importlib.util
import json
import sys
import unittest
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1] / "benchmark/longmemeval"
sys.path.insert(0, str(BENCH))
spec = importlib.util.spec_from_file_location("longmemeval_evaluate", BENCH / "evaluate.py")
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


class IndependentProtocolTests(unittest.TestCase):
    def fixture(self, **kwargs):
        case = dict(
            question_id="fictional",
            answer=42,
            answer_session_ids=["s1", "s2"],
            haystack_session_ids=["s1", "s2"],
            haystack_sessions=[[{"has_answer": True}], [{"has_answer": True}]],
        )
        result = dict(
            answer="42",
            error=None,
            abstain=False,
            retrieved_sessions=["s2", "unrelated", "s1"],
            cited_sessions=["unrelated"],
            extracted_turns=["s1:0"],
        )
        result.update(kwargs)
        return case, result

    def test_numeric_labels_and_session_recall_are_not_binary_hits(self):
        result = evaluation.score(*self.fixture())
        self.assertTrue(result["exact_match"])
        self.assertEqual(result["retrieval_recall_at_1"], 0.5)
        self.assertEqual(result["retrieval_recall_at_5"], 1)
        self.assertEqual(result["citation_gold_session_recall"], 0)
        self.assertEqual(result["extracted_evidence_turn_coverage"], 0.5)
        self.assertNotIn("answer", result)
        self.assertNotIn("s1:0", result["extracted_turns"])

    def test_failure_cannot_count_as_correct_abstention(self):
        case, result = self.fixture(error="ChunkFailure", abstain=True)
        case["question_id"] += "_abs"
        scored = evaluation.score(case, result)
        self.assertFalse(scored["correct_abstention"])
        self.assertFalse(scored["exact_match"])
        self.assertIsNone(scored["retrieval_recall_at_5"])

    def test_no_gold_answer_match_by_substring(self):
        result = evaluation.score(*self.fixture(answer="42 or possibly 43"))
        self.assertFalse(result["exact_match"])

    def test_manifest_has_independent_fixed_coverage(self):
        manifest = json.loads((BENCH / "manifest.json").read_text())
        self.assertEqual(len(manifest["cases"]), 140)
        self.assertEqual(len({c["question_id"] for c in manifest["cases"]}), 140)
        self.assertEqual(set(manifest["counts"].values()), {20})
        self.assertEqual(len(manifest["counts"]), 7)
        self.assertEqual(manifest["license"], "MIT")


if __name__ == "__main__":
    unittest.main()
