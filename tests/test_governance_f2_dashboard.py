"""F2 测试：Token 看板（只读）。合成数据，不碰真实记忆。"""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

import token_dashboard as TD


def _make_db(rows):
    tmp = Path(tempfile.mkdtemp(prefix="stellamem-f2-"))
    db = tmp / "index.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE records (id TEXT, claim_id TEXT, kind TEXT, subject TEXT,"
        " value TEXT, status TEXT, namespace TEXT)"
    )
    for i, r in enumerate(rows):
        con.execute(
            "INSERT INTO records VALUES (?,?,?,?,?,?,?)",
            (f"id-{i}", r.get("claim_id", f"c-{i}"), r.get("kind", "fact"),
             r.get("subject", "s"), r.get("value", "v"),
             r.get("status", "active"), r.get("namespace", "user")),
        )
    con.commit()
    con.close()
    return db


def _rec(**kw):
    return kw


class DashboardTests(unittest.TestCase):
    def test_counts_by_namespace_status_kind(self):
        db = _make_db([
            _rec(kind="fact", namespace="user", status="active", value="a" * 100),
            _rec(kind="fact", namespace="user", status="pending", value="b" * 100),
            _rec(kind="preference", namespace="persona", status="active", value="c" * 100),
        ])
        d = TD.dashboard(db)
        self.assertEqual(d["records"], 3)
        self.assertEqual(d["by_namespace"], {"user": 2, "persona": 1})
        self.assertEqual(d["by_status"], {"active": 2, "pending": 1})
        self.assertEqual(d["by_kind"], {"fact": 2, "preference": 1})

    def test_alert_levels(self):
        db = _make_db([_rec(value="x" * 10)])
        green = TD.dashboard(db, budget_tokens=10**9)
        self.assertEqual(green["tokens"]["alert"], "green")
        red = TD.dashboard(db, budget_tokens=1)
        self.assertEqual(red["tokens"]["alert"], "red")
        self.assertTrue(red["review_suggestions"])  # 超预算给出复核建议
        self.assertFalse(green["review_suggestions"])

    def test_estimate_flag_present(self):
        # 无 tiktoken 时必须标注"估算"
        db = _make_db([_rec(value="测试中文内容")])
        d = TD.dashboard(db)
        self.assertIn("estimated", d["tokens"])
        self.assertIsInstance(d["tokens"]["estimated"], bool)

    def test_readonly_does_not_modify_db(self):
        db = _make_db([_rec(value="v")])
        before = db.stat().st_mtime_ns
        TD.dashboard(db)
        after = db.stat().st_mtime_ns
        self.assertEqual(before, after)

    def test_cjk_heuristic_marks_estimated(self):
        toks, is_est = TD.estimate_tokens("你好世界 hello")
        # 无 tiktoken 环境下应为估算；有 tiktoken 则为精确值
        self.assertGreater(toks, 0)
        self.assertIsInstance(is_est, bool)

    def test_top_consumers_sorted(self):
        db = _make_db([
            _rec(value="s", claim_id="small"),
            _rec(value="L" * 5000, claim_id="large"),
        ])
        d = TD.dashboard(db)
        self.assertEqual(d["top_consumers"][0]["id"], "large")


if __name__ == "__main__":
    unittest.main()
