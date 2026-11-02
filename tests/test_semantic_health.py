"""Step 5 tests: semantic health, cache, rerank, own evaluation.

Synthetic placeholder data only. No network, no real embedding service.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

import semantic_health as SH


class SemanticHealthTests(unittest.TestCase):
    def test_check_service_ok(self):
        status = SH.check_service(lambda: (True, 12.5))
        self.assertTrue(status["available"])
        self.assertEqual(status["recommendation"], "use_semantic")

    def test_check_service_failure_falls_back(self):
        status = SH.check_service(lambda: (_ for _ in ()).throw(RuntimeError("down")))
        self.assertFalse(status["available"])
        self.assertEqual(status["recommendation"], "fall_back_to_literal")

    def test_check_service_not_ok_falls_back(self):
        status = SH.check_service(lambda: (False, 3.0))
        self.assertFalse(status["available"])

    def test_cache_hit_miss_and_eviction(self):
        cache = SH.VectorCache(max_entries=2)
        self.assertIsNone(cache.get("占位查询A"))
        cache.put("占位查询A", [0.1, 0.2])
        cache.put("占位查询B", [0.3, 0.4])
        self.assertEqual(cache.get("占位查询A"), [0.1, 0.2])
        cache.put("占位查询C", [0.5, 0.6])
        # A was recently used, so B (least recently used) is evicted.
        self.assertIsNone(cache.get("占位查询B"))
        self.assertEqual(cache.stats()["entries"], 2)

    def test_cache_key_is_hash_not_text(self):
        self.assertNotIn("占位查询", SH.VectorCache.key_for("占位查询"))

    def test_cache_invalid_size_rejected(self):
        with self.assertRaises(ValueError):
            SH.VectorCache(max_entries=0)

    def test_rerank_prefers_semantic(self):
        ranked = SH.rerank([
            {"id": "a", "literal": 0.9, "semantic": 0.1},
            {"id": "b", "literal": 0.2, "semantic": 0.9},
        ])
        self.assertEqual(ranked[0]["id"], "b")
        self.assertEqual(ranked[0]["rerank_basis"], "literal_semantic")

    def test_rerank_missing_semantic_uses_literal(self):
        ranked = SH.rerank([{"id": "a", "literal": 0.7, "semantic": None}])
        self.assertEqual(ranked[0]["rerank_score"], 0.7)
        self.assertEqual(ranked[0]["rerank_basis"], "literal_only")

    def test_rerank_tie_break_deterministic(self):
        ranked = SH.rerank([
            {"id": "b", "literal": 0.5, "semantic": 0.5},
            {"id": "a", "literal": 0.5, "semantic": 0.5},
        ])
        self.assertEqual([c["id"] for c in ranked], ["a", "b"])

    def test_evaluate_recall(self):
        result = SH.evaluate([
            {"query": "占位查询1", "expected_id": "x", "candidates": [
                {"id": "x", "literal": 0.9, "semantic": 0.9},
                {"id": "y", "literal": 0.1, "semantic": 0.1},
            ]},
            {"query": "占位查询2", "expected_id": "z", "candidates": [
                {"id": "y", "literal": 0.9, "semantic": 0.9},
                {"id": "z", "literal": 0.1, "semantic": 0.1},
            ]},
        ])
        self.assertEqual(result["cases"], 2)
        self.assertEqual(result["recall_at_1"], 0.5)
        self.assertEqual(result["recall_at_5"], 1.0)

    def test_evaluate_empty(self):
        result = SH.evaluate([])
        self.assertEqual(result["cases"], 0)
        self.assertEqual(result["recall_at_1"], 0.0)


if __name__ == "__main__":
    unittest.main()
