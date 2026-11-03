"""F3 测试：90 天时效衰减接入 recall。合成数据，不碰真实记忆。"""

import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

import rank
import search as S


def _make_db():
    tmp = Path(tempfile.mkdtemp(prefix="stellamem-f3-"))
    db = tmp / "index.db"
    con = sqlite3.connect(db)
    # 与 indexer.py 的真实表结构对齐
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


def _old_ts(days_ago: int) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


class DecayConfigTests(unittest.TestCase):
    def setUp(self):
        rank.configure_decay(halflife_days=None, strength=1.0)

    def tearDown(self):
        rank.configure_decay(halflife_days=None, strength=1.0)

    def test_configure_halflife_override(self):
        rank.configure_decay(halflife_days=90)
        rec = {"kind": "fact", "occurred_at": _old_ts(90)}
        # 90 天 + 半衰期 90 天 -> R ≈ 0.5
        self.assertAlmostEqual(rank.retention(rec), 0.5, places=2)

    def test_configure_invalid_halflife(self):
        with self.assertRaises(ValueError):
            rank.configure_decay(halflife_days=0)

    def test_identity_never_decays_even_with_override(self):
        rank.configure_decay(halflife_days=90)
        rec = {"kind": "identity", "occurred_at": _old_ts(3650)}
        self.assertEqual(rank.retention(rec), 1.0)

    def test_pinned_never_decays(self):
        rec = {"kind": "fact", "pinned": True, "occurred_at": _old_ts(365)}
        self.assertEqual(rank.retention(rec), 1.0)
        rec2 = {"kind": "fact", "pinned": 1, "occurred_at": _old_ts(365)}
        self.assertEqual(rank.retention(rec2), 1.0)

    def test_decay_only_lowers_never_deletes(self):
        # 衰减只影响排序：rank() 不删除条目
        now = int(time.time() * 1000)
        items = [
            ({"kind": "fact", "occurred_at": _old_ts(300)}, 0.9),
            ({"kind": "fact", "occurred_at": _old_ts(1)}, 0.5),
        ]
        out = rank.rank(items, now_ms=now)
        self.assertEqual(len(out), 2)  # 一条不少
        # 新记录（1天前，R≈1）应排在旧记录（300天前，R很小）前面
        self.assertTrue(out[0][0]["occurred_at"] > out[1][0]["occurred_at"])

    def test_strength_zero_disables(self):
        rank.configure_decay(strength=0)
        rec = {"kind": "fact", "occurred_at": _old_ts(365)}
        self.assertEqual(rank.retention(rec), 1.0)

    def test_explain_has_fields(self):
        rank.configure_decay(halflife_days=90)
        e = rank.explain({"kind": "fact", "occurred_at": _old_ts(45)})
        self.assertEqual(e["halflife_override_days"], 90)
        self.assertIn("retention", e)
        self.assertIn("age_days", e)
        self.assertFalse(e["pinned"])


class RecallDecayWiringTests(unittest.TestCase):
    def setUp(self):
        rank.configure_decay(halflife_days=None, strength=1.0)
        self.db = _make_db()
        self.con = sqlite3.connect(self.db)
        self.con.row_factory = sqlite3.Row

    def tearDown(self):
        self.con.close()
        rank.configure_decay(halflife_days=None, strength=1.0)

    def _insert(self, claim_id, value, kind="fact", occurred_at=None, status="active"):
        self.con.execute(
            "INSERT INTO records (id, claim_id, layer, kind, subject, value, evidence,"
            " status, occurred_at, grams)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                claim_id,
                claim_id,
                "memory",
                kind,
                "s",
                value,
                "",
                status,
                occurred_at or _old_ts(0),
                "",
            ),
        )
        self.con.commit()

    def test_decay_reorders_old_below_new(self):
        # 两条同样关键词：旧的高分 vs 新的低分；衰减后新的应排前
        self._insert("c-old", "测试关键词 alpha", occurred_at=_old_ts(200))
        self._insert("c-new", "测试关键词 beta", occurred_at=_old_ts(0))
        res = S.recall(self.con, "测试关键词", decay=True, min_score=0.0, max_items=10)
        ids = [r["claim_id"] for r in res["items"]]
        self.assertIn("c-old", ids)
        self.assertIn("c-new", ids)
        # 新记录应排在旧记录前面（旧记录被衰减）
        self.assertLess(ids.index("c-new"), ids.index("c-old"))

    def test_decay_false_preserves_old_behavior(self):
        self._insert("c-old", "测试关键词 alpha", occurred_at=_old_ts(200))
        self._insert("c-new", "测试关键词 beta", occurred_at=_old_ts(0))
        res = S.recall(self.con, "测试关键词", decay=False, min_score=0.0, max_items=10)
        ids = [r["claim_id"] for r in res["items"]]
        self.assertIn("c-old", ids)
        self.assertIn("c-new", ids)

    def test_explain_decay_attaches_info(self):
        self._insert("c-1", "测试关键词 gamma", occurred_at=_old_ts(10))
        res = S.recall(
            self.con, "测试关键词", decay=True, explain_decay=True, min_score=0.0, max_items=10
        )
        self.assertTrue(res["items"])
        self.assertIn("decay", res["items"][0])
        self.assertIn("retention", res["items"][0]["decay"])

    def test_no_explain_by_default(self):
        self._insert("c-1", "测试关键词 delta", occurred_at=_old_ts(10))
        res = S.recall(self.con, "测试关键词", min_score=0.0, max_items=10)
        if res["items"]:
            self.assertNotIn("decay", res["items"][0])


if __name__ == "__main__":
    unittest.main()
