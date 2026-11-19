"""Offline tests of the experiment, never scored as model or blind evidence."""

import json
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from benchmark.jarvis_semantic import scale_v2 as exp
from benchmark.jarvis_semantic.scale_corpus import check_base, distractors


class ScaleTest(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((Path(exp.HERE) / "scale_public.json").read_text("utf-8"))

    def test_corpus(self):
        check_base(self.data, False)
        with self.assertRaises(ValueError):
            check_base(self.data, True)
        extra = distractors(self.data["notes"])
        for size, f, g, h in ((480, 320, 100, 60), (1980, 1460, 400, 120)):
            rows = extra[:size]
            for key, target in (("F", f), ("G", g), ("H", h)):
                self.assertEqual(sum(r["kind"] == key for r in rows), target)
            for transform in ("entity", "attribute", "old_other_entity"):
                self.assertEqual(sum(r.get("transform") == transform for r in rows), h // 3)

    def test_local_pair_and_boundary(self):
        def reply(port, path, body):
            if path.endswith("/embeddings"):
                return {
                    "data": [
                        dict(index=i, embedding=[1.0] + [0.0] * 1023)
                        for i in range(len(body["input"]))
                    ]
                }
            return {"choices": [{"message": {"content": "NONE"}}]}

        args = SimpleNamespace(
            blind=False, embed_model="fixture", reader_model="fixture", reader_port=1234
        )
        logs = []
        with patch.object(exp.frozen, "post", reply):
            exp.study(self.data, args, logs.append)
        self.assertEqual(logs[-1]["pairs"], 9)
        self.assertIsNone(logs[-1]["passed"])
        self.assertEqual([r["items"] for r in logs if r["type"] == "index"], [20, 500, 2000])
        rows = [r for r in logs if r["type"] == "question"]
        self.assertEqual(len(rows), 18)
        self.assertTrue(
            any(r["gold_delivered"] and r["reader_status"] == "model_none" for r in rows)
        )
        with self.assertRaises(exp.BoundaryFailure):
            exp.boundary(["foreign"], {"foreign"}, {"old"}, "raw_projection")
        with self.assertRaises(exp.BoundaryFailure):
            exp.boundary(["old"], {"foreign"}, {"old"}, "index")

    def test_paired_decision(self):
        def row(i, k, correct):
            return dict(
                id=f"{k}{i}",
                group=k,
                correct=correct,
                gold=None if k == "N" else str(i),
                recall5=correct,
                negative_answer_with_real_source=k == "N" and not correct,
                query_embedding_seconds=0,
                vector_search_seconds=0,
                projection_seconds=0,
                end_to_end_seconds=0,
                reader_status="selected",
                gold_delivered=k != "N",
            )

        pairs = [dict(A=row(i, "D", False), B=row(i, "D", True)) for i in range(5)]
        pairs.append(dict(A=row(0, "E", True), B=row(0, "E", False)))
        pairs.append(dict(A=row(0, "N", False), B=row(0, "N", False)))
        self.assertTrue(all(exp.summarize(pairs)["gates"].values()))
        pairs.append(dict(A=row(1, "N", True), B=row(1, "N", False)))
        self.assertFalse(exp.summarize(pairs)["gates"]["negative"])
