"""第二阶段：索引与检索回归。"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

import search as S
from indexer import Index
from storage import render_records

RECORDS = [
    ("c-1", "r-1", "project", "记忆系统第一阶段完成", "active"),
    ("c-2", "r-2", "preference", "偏好大白话讲技术", "active"),
    ("c-3", "r-3", "decision", "决定用 SQLite 做本地索引", "pending"),
    ("c-4", "r-4", "fact", "隔离中的敏感内容不该被搜到", "quarantined"),
]


def seed(root: Path):
    rows = []
    for cid, rid, kind, value, status in RECORDS:
        rows.append(
            {
                "schema_version": 2,
                "claim_id": cid,
                "revision_id": rid,
                "event_id": "e" + cid,
                "kind": kind,
                "scope": "global",
                "subject": "用户",
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
                "status": status,
                "relation": "NEW",
                "auto_write": True,
                "observed_at": "2026-11-01T00:00:00+00:00",
                "provenance": {"timestamp": "2026-11-01T09:00:00", "record_id": "rm-1"},
            }
        )
    target = root / "long-term" / "projects.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_records("# 项目\n", rows), encoding="utf-8")


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "memories"
        seed(self.root)
        self.idx = Index(base / "index.db")
        self.idx.build(self.root)

    def tearDown(self):
        self.idx.close()
        self.tmp.cleanup()

    def test_rebuild_is_idempotent(self):
        first = self.idx.stats()["records"]
        self.idx.build(self.root)
        self.assertEqual(self.idx.stats()["records"], first)

    def test_quarantined_never_returned_by_default(self):
        hits = S.search(self.idx.con, "敏感内容", limit=10)
        self.assertEqual(hits, [])
        explicit = S.search(self.idx.con, "敏感内容", limit=10, status=("quarantined",))
        self.assertEqual(len(explicit), 1)

    def test_active_and_pending_are_searchable(self):
        self.assertTrue(S.search(self.idx.con, "第一阶段完成", limit=5))
        self.assertTrue(S.search(self.idx.con, "大白话", limit=5))
        self.assertTrue(S.search(self.idx.con, "SQLite 本地索引", limit=5))

    def test_fragmented_chinese_query(self):
        hits = S.search(self.idx.con, "索引 本地 做", limit=5)
        self.assertTrue(hits)

    def test_kind_filter(self):
        hits = S.search(self.idx.con, "决定", kinds="decision", limit=5)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["kind"], "decision")

    def test_recall_respects_budget_and_dedupes(self):
        out = S.recall(self.idx.con, "我的记忆系统做到哪了", budget_chars=60, max_items=5)
        self.assertLessEqual(out["chars"], 60)
        self.assertEqual(len({i["claim_id"] for i in out["items"]}), len(out["items"]))

    def test_history_and_timeline(self):
        self.assertEqual(len(S.timeline(self.idx.con)), 4)
        self.assertGreaterEqual(len(S.history(self.idx.con, "c-1")), 1)


if __name__ == "__main__":
    unittest.main()
