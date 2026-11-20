"""证据充分性信号测试（compiler/sufficiency.py + search.recall 集成）。

与 JS 侧 adapters/openclaw/index.js 的 questionAnchors /
evidenceSufficiency 保持口径一致；锚点用例与
tests/adapter-anchor-gate.test.mjs 的已测案例对齐（平价）。
只用隔离临时目录与虚构数据，不碰生产数据。
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "compiler"))

from indexer import Index  # noqa: E402
from search import recall  # noqa: E402
from storage import render_records  # noqa: E402
from sufficiency import evidence_sufficiency, question_anchors  # noqa: E402


class AnchorParityTest(unittest.TestCase):
    def test_anchor_cases_match_js(self):
        self.assertEqual(
            question_anchors(
                "How long have I been working before I started my current job at Google?"
            ),
            ["google"],
        )
        self.assertEqual(
            question_anchors("Where did I attend the Imagine Dragons concert in March?"),
            ["imagine dragons"],
        )
        self.assertEqual(question_anchors("我的绘本项目叫什么名字？"), [])
        self.assertEqual(
            question_anchors(
                "I’m a bit anxious about getting around Tokyo. Do you have any helpful tips?"
            ),
            ["tokyo"],
        )
        self.assertEqual(
            question_anchors("Tell me about my stay at the Park Hyatt Tokyo."),
            ["park hyatt tokyo"],
        )
        self.assertEqual(question_anchors("The concert was in May."), [])
        self.assertEqual(question_anchors("I visited the 'Google' campus last year"), ["google"])
        self.assertEqual(question_anchors("I flew to the U.S. last spring"), ["u.s"])


class SufficiencyGradeTest(unittest.TestCase):
    def test_partial_grade(self):
        suff = evidence_sufficiency(
            "Which project did I start first, the Ferrari model or the Porsche 991 Turbo S model?",
            ["user is building a Ferrari 288 GTO model"],
        )
        self.assertEqual(suff["status"], "partial")
        self.assertIn("ferrari", suff["covered"])
        self.assertIn("porsche", suff["uncovered"])

    def test_other_grades(self):
        self.assertEqual(
            evidence_sufficiency(
                "Where did I attend the Imagine Dragons concert?",
                ["I attended the Imagine Dragons concert"],
            )["status"],
            "sufficient",
        )
        self.assertEqual(
            evidence_sufficiency(
                "How long have I been working before I started my current job at Google?",
                ["worked at NovaTech for years"],
            )["status"],
            "insufficient",
        )
        self.assertEqual(
            evidence_sufficiency("how many plants did i plant?", ["5 tomato plants"])["status"],
            "unknown",
        )


def _record(claim_id, value, observed="2026-11-05T09:00:00"):
    return {
        "schema_version": 2,
        "claim_id": claim_id,
        "revision_id": f"r-{claim_id}",
        "event_id": f"e-{claim_id}",
        "kind": "fact",
        "scope": "global",
        "subject": "user",
        "predicate": "p",
        "value": value,
        "evidence": value,
        "source": "user",
        "confidence": 0.9,
        "importance": 0.5,
        "sensitivity": "PRIVATE",
        "write_policy": "allow",
        "injection_policy": "recall",
        "evidence_verified": True,
        "status": "active",
        "relation": "NEW",
        "auto_write": True,
        "observed_at": observed,
        "provenance": {"timestamp": observed, "record_id": "rm-x"},
    }


class RecallSufficiencyTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        base = Path(self._td.name)
        self.mem_root = base / "memories"
        rows = [
            _record("c-ferrari", "虚构：正在拼装 Ferrari 288 GTO 模型"),
            _record("c-baseball", "虚构：收藏签名棒球，三个月收集了15个"),
        ]
        target = self.mem_root / "semantic" / "facts.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_records("# 事实\n", rows), encoding="utf-8")
        self.db_path = base / "index.db"
        idx = Index(self.db_path)
        try:
            idx.build(self.mem_root)
        finally:
            idx.close()
        self.con = sqlite3.connect(str(self.db_path))
        self.con.row_factory = sqlite3.Row
        self._env = mock.patch.dict(os.environ, {"STELLAMEM_SEMANTIC": "0"})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self.con.close()
        self._td.cleanup()

    def test_recall_reports_partial_for_one_sided_evidence(self):
        result = recall(
            self.con,
            "Which project did I start first, the Ferrari model or the Porsche 991 Turbo S model?",
            subject="user",
        )
        suff = result["evidence_sufficiency"]
        self.assertEqual(suff["status"], "partial")
        self.assertIn("porsche", suff["uncovered"])

    def test_recall_reports_unknown_without_anchors(self):
        result = recall(self.con, "虚构问题：签名棒球收集了多少个？", subject="user")
        self.assertEqual(result["evidence_sufficiency"]["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
