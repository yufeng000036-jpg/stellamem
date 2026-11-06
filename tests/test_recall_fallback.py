"""召回 P2 空池兜底测试（compiler/search.py）。

只用隔离临时目录与虚构数据（全部以“虚构”开头），不碰生产数据。
语义通道在测试里强制离线（STELLAMEM_SEMANTIC=0），与生产现状一致。
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
from storage import render_records  # noqa: E402
from search import recall, search  # noqa: E402


def make_record(claim_id, kind, subject, value, observed="2026-11-05T09:00:00"):
    return {
        "schema_version": 2,
        "claim_id": claim_id,
        "revision_id": f"r-{claim_id}",
        "event_id": f"e-{claim_id}",
        "kind": kind,
        "scope": "global",
        "subject": subject,
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


def seed(mem_root: Path) -> None:
    rows = [
        make_record("c-a2-proj", "project", "user", "虚构：深海灯塔电台"),
        make_record("c-a2-fact", "fact", "user", "虚构：住在海宁"),
        # 旧记录（2026-09-01，超过 30 天）：时间问句兜底不得把它拉回来。
        make_record(
            "c-a2-old", "fact", "user", "虚构：十年前去过泰山", observed="2026-09-01T09:00:00"
        ),
        make_record("c-a2-persona", "project", "persona", "虚构：鲸鱼娘的巡演计划"),
    ]
    target = mem_root / "long-term" / "projects.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_records("# 项目\n", rows), encoding="utf-8")


class RecallFallbackTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        base = Path(self._td.name)
        self.mem_root = base / "memories"
        seed(self.mem_root)
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

    def test_intent_question_with_zero_bigram_overlap_falls_back(self):
        # 意图问题（>14 字，不走泛问），与记录零共享二元组：修前返回 []。
        q = "请问那个播客项目现在做到哪一步了"
        hits = search(self.con, q, person="user")
        self.assertEqual(len(hits), 1)
        rec = hits[0]
        self.assertIn("深海灯塔电台", rec["value"])
        self.assertTrue(rec.get("fallback"))
        self.assertEqual(rec.get("match"), "fallback")
        self.assertEqual(rec.get("overlap"), 0.0)
        # recall() 放过 min_score（兜底分 0.10 < 0.18），并标 weak。
        result = recall(self.con, q, subject="user")
        self.assertEqual(len(result["items"]), 1)
        item = result["items"][0]
        self.assertTrue(item.get("fallback"))
        self.assertTrue(item.get("weak"))
        self.assertLess(item["score"], 0.18)

    def test_recent_question_falls_back_to_last_30_days_only(self):
        q = "这几天发生了什么"
        hits = search(self.con, q, person="user")
        values = [r["value"] for r in hits]
        self.assertTrue(any("深海灯塔电台" in v for v in values))
        self.assertTrue(any("住在海宁" in v for v in values))
        self.assertFalse(any("泰山" in v for v in values), "旧记录不得被兜底拉回")
        self.assertTrue(all(r.get("fallback") for r in hits))

    def test_no_signal_question_still_empty(self):
        # 无意图、无时间信号、零词面命中：不兜底，仍返回空。
        q = "今天天气怎么样"
        self.assertEqual(search(self.con, q, person="user"), [])
        self.assertEqual(recall(self.con, q, subject="user")["items"], [])

    def test_fallback_respects_subject_isolation(self):
        # 问 persona 的项目，subject=user 时不得漏出 persona 记录；
        # 意图兜底只取 user 主体的同类记录。
        q = "请问那个巡演项目现在做到哪一步了"
        hits = search(self.con, q, person="user")
        self.assertTrue(all("巡演" not in r["value"] for r in hits))
        self.assertTrue(any("深海灯塔电台" in r["value"] for r in hits))
        # 纯 persona 话题、无意图信号：直接为空。
        self.assertEqual(search(self.con, "鲸鱼娘的巡演计划怎么样", person="user"), [])

    def test_nonempty_pool_has_no_fallback(self):
        # 有词面命中的正常问题走原打分路径，不带 fallback 标记。
        hits = search(self.con, "深海灯塔电台怎么样", person="user")
        self.assertGreater(len(hits), 0)
        self.assertTrue(all(not r.get("fallback") for r in hits))
        self.assertTrue(all(r.get("match") != "fallback" for r in hits))


if __name__ == "__main__":
    unittest.main()
