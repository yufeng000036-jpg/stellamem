"""F4 测试：selector-audit 诊断。合成数据，不碰真实记忆。"""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

import selector_audit as SA


def _make_db():
    tmp = Path(tempfile.mkdtemp(prefix="stellamem-f4-"))
    db = tmp / "index.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE records (id TEXT PRIMARY KEY, claim_id TEXT, revision_id TEXT,"
        " layer TEXT NOT NULL, kind TEXT, subject TEXT, predicate TEXT, value TEXT,"
        " evidence TEXT, status TEXT, source TEXT, occurred_at TEXT, observed_at TEXT,"
        " record_id TEXT, session_id TEXT, file TEXT, content_hash TEXT,"
        " redacted INTEGER DEFAULT 0, grams TEXT, valid_to TEXT, expired_at TEXT)"
    )
    con.commit()
    con.close()
    return db


def _insert(db, claim_id, value, status="active", kind="fact", subject="user"):
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO records (id, claim_id, layer, kind, subject, value, status)"
        " VALUES (?,?,?,?,?,?,?)",
        (claim_id, claim_id, "memory", kind, subject, value, status),
    )
    con.commit()
    con.close()


class SelectorAuditTests(unittest.TestCase):
    def test_not_in_index(self):
        db = _make_db()
        r = SA.audit(db, "no-such-id", "随便什么查询")
        self.assertEqual(r["verdict"], "not_in_index")
        self.assertFalse(r["stages"][0]["pass"])

    def test_blocked_by_status(self):
        db = _make_db()
        _insert(db, "c-pending", "测试诊断关键词", status="pending")
        r = SA.audit(db, "c-pending", "测试诊断关键词")
        self.assertEqual(r["verdict"], "blocked_by_status")

    def test_not_recalled_by_search(self):
        db = _make_db()
        _insert(db, "c-far", "完全不相关的火星土豆种植指南")
        r = SA.audit(db, "c-far", "量子计算机散热方案")
        self.assertEqual(r["verdict"], "not_recalled_by_search")

    def test_injected_happy_path(self):
        db = _make_db()
        _insert(db, "c-hit", "测试诊断关键词 unique 太阳能板之详情")
        r = SA.audit(db, "c-hit", "测试诊断关键词 unique 太阳能板",
                     min_score=0.0)
        self.assertEqual(r["verdict"], "injected")
        stage_names = [s["stage"] for s in r["stages"]]
        for expected in ("exists", "namespace", "status", "search", "decay",
                         "dedup_topk", "budget"):
            self.assertIn(expected, stage_names)

    def test_filtered_by_min_score(self):
        db = _make_db()
        _insert(db, "c-low", "测试诊断关键词")
        r = SA.audit(db, "c-low", "测试诊断关键词", min_score=2.0)
        self.assertEqual(r["verdict"], "filtered_by_min_score")

    def test_cut_by_top_k(self):
        db = _make_db()
        for i in range(5):
            _insert(db, f"c-{i}", f"测试诊断关键词公共部分 filler{i} 区分")
        # max_items=2：5 条都进候选池，前 2 条被选中，c-4 被 top-k 截断
        r = SA.audit(db, "c-4", "测试诊断关键词公共部分", min_score=0.0,
                     max_items=2)
        self.assertEqual(r["verdict"], "cut_by_top_k")
        pos = [s for s in r["stages"] if s["stage"] == "dedup_topk"][0]
        self.assertIsNone(pos["position"])

    def test_folded_by_dedup(self):
        db = _make_db()
        # 前 40 字符相同 -> recall 去重折叠；但 48 字符内不同 -> search 不折叠
        _insert(db, "c-a", "去重目标关键词" + "x" * 33 + "甲乙丙丁")
        _insert(db, "c-b", "去重目标关键词" + "x" * 33 + "戊己庚辛")
        r = SA.audit(db, "c-b", "去重目标关键词", min_score=0.0,
                     max_items=10)
        self.assertEqual(r["verdict"], "folded_by_dedup")

    def test_readonly(self):
        db = _make_db()
        _insert(db, "c-ro", "测试诊断关键词只读")
        before = db.stat().st_mtime_ns
        SA.audit(db, "c-ro", "测试诊断关键词只读", min_score=0.0)
        self.assertEqual(before, db.stat().st_mtime_ns)


if __name__ == "__main__":
    unittest.main()
