"""Protocol and governance controls only. No actual encoder or reader is claimed."""

import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1] / "benchmark/jarvis_semantic"
spec = importlib.util.spec_from_file_location("semantic_study", HERE / "run.py")
exp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exp)


class SemanticStudyTest(unittest.TestCase):
    def test_stale_ranks_after_forget_and_rollback(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d).resolve()
            exp.jarvis.initialize(home)
            root = home / "memory"
            rid = exp.mem.operate(root, "记下", "银色票根收在南侧矮柜。").split()[-1]
            approved = exp.mem.operate(root, "批准", rid).split()
            key = approved[1]
            ranks = [exp.verified_active(root)[key]["revision_id"]]
            q = "入场的凭据收哪儿了"
            self.assertNotIn(key, exp.project(home, q, None)[0])
            self.assertIn(key, exp.project(home, q, ranks)[0])
            exp.mem.operate(root, "撤销批准", approved[-1])
            self.assertNotIn(key, exp.project(home, q, ranks)[0])
            exp.mem.operate(root, "批准", rid)
            exp.mem.operate(root, "遗忘", key)
            self.assertNotIn(key, exp.project(home, q, ranks)[0])

    def test_paired_protocol(self):
        def mock_post(port, path, body):
            if path == "/v1/embeddings":
                return {
                    "data": [
                        dict(index=i, embedding=[1.0] + [0.0] * 1023)
                        for i in reversed(range(len(body["input"])))
                    ]
                }
            self.assertEqual(body["temperature"], 0)
            return {"choices": [{"message": {"content": "NONE"}}]}

        manifest = HERE / "public.json"
        out = io.StringIO()
        args = SimpleNamespace(
            manifest=manifest,
            sealed_original=None,
            sealed_hash=None,
            embed_model="fixture",
            reader_model="fixture",
            reader_port=1234,
        )
        with patch.object(exp, "post", side_effect=mock_post):
            exp.study(args, out, json.loads(manifest.read_text("utf-8")))
        logs = list(map(json.loads, out.getvalue().splitlines()))
        self.assertEqual(logs[0]["items"], 2)
        self.assertEqual(logs[-1], dict(type="complete", pairs=3))
        self.assertEqual(sum(r["type"] == "question" for r in logs), 6)
