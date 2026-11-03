"""Tests for batch2 item 2: preference_summary / pattern 接入 recall（带配额）。

Synthetic data only; nothing touches real user memory.
"""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

import search as S
from usage_signals import UsageTracker


def _empty_db():
    tmp = Path(tempfile.mkdtemp(prefix="stellamem-batch2-"))
    con = sqlite3.connect(tmp / "index.db")
    con.execute(
        "CREATE TABLE records (id TEXT PRIMARY KEY, claim_id TEXT, revision_id TEXT,"
        " layer TEXT NOT NULL, kind TEXT, subject TEXT, predicate TEXT, value TEXT,"
        " evidence TEXT, status TEXT, source TEXT, occurred_at TEXT, observed_at TEXT,"
        " record_id TEXT, session_id TEXT, file TEXT, content_hash TEXT,"
        " redacted INTEGER DEFAULT 0, grams TEXT, valid_to TEXT, expired_at TEXT)"
    )
    con.commit()
    return con


def _card(subject, text, status="active"):
    return {
        "kind": "preference_summary",
        "subject": subject,
        "text": text,
        "evidence": [{"statement": "s1", "evidence": "e1"}],
        "version": 1,
        "status": status,
    }


class PrefRecallTests(unittest.TestCase):
    def test_matching_card_injected_with_evidence(self):
        con = _empty_db()
        cards = [_card("钟丽潭", "送小心心是省钱的支持方式，不是喜欢特效")]
        r = S.recall(con, "钟丽潭送了什么", preference_cards=cards)
        kinds = [i["kind"] for i in r["items"]]
        self.assertIn("preference_summary", kinds)
        item = [i for i in r["items"] if i["kind"] == "preference_summary"][0]
        self.assertEqual(len(item["evidence"]), 1)
        self.assertIn("[偏好]", r["text"])
        con.close()

    def test_non_matching_card_not_injected(self):
        con = _empty_db()
        cards = [_card("张三", "喜欢喝咖啡")]
        r = S.recall(con, "钟丽潭送了什么", preference_cards=cards)
        self.assertEqual(r["items"], [])
        con.close()

    def test_quota_respected(self):
        con = _empty_db()
        cards = [_card("钟丽潭%d" % i, "钟丽潭送小心心支持主播") for i in range(5)]
        r = S.recall(con, "钟丽潭", preference_cards=cards, pref_quota=2)
        n = len([i for i in r["items"] if i["kind"] == "preference_summary"])
        self.assertEqual(n, 2)
        con.close()

    def test_withdrawn_card_never_injected(self):
        con = _empty_db()
        cards = [_card("钟丽潭", "钟丽潭送小心心", status="withdrawn")]
        r = S.recall(con, "钟丽潭", preference_cards=cards)
        self.assertEqual(r["items"], [])
        con.close()

    def test_normal_path_unchanged_without_cards(self):
        con = _empty_db()
        r = S.recall(con, "随便问点什么")
        self.assertEqual(r["items"], [])
        self.assertIn("prompt", r)
        con.close()

    def test_usage_tracker_records_hit(self):
        con = _empty_db()
        tracker = UsageTracker()
        cards = [_card("钟丽潭", "送小心心是省钱的支持方式")]
        S.recall(con, "钟丽潭送了什么", preference_cards=cards, usage_tracker=tracker)
        hit = tracker.get("pref:钟丽潭")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["hits"], 1)
        self.assertEqual(hit["kind"], "preference_summary")
        con.close()

    def test_pattern_injected_with_lower_weight(self):
        con = _empty_db()
        patterns = [
            {
                "id": "pat-000001",
                "kind": "pattern",
                "status": "active",
                "subject": "钟丽潭",
                "predicate": "送礼",
                "value": "起哄时跟风刷小心心",
                "provenance": ["ep5", "ep7"],
                "support": 2,
            }
        ]
        r = S.recall(con, "钟丽潭起哄会送礼吗", patterns=patterns)
        kinds = [i["kind"] for i in r["items"]]
        self.assertIn("pattern", kinds)
        item = [i for i in r["items"] if i["kind"] == "pattern"][0]
        self.assertLessEqual(item["score"], 0.8)  # 权重 <1，低于原始记录
        self.assertIn("[规律]", r["text"])
        con.close()

    def test_pattern_quota_respected(self):
        con = _empty_db()
        patterns = [
            {
                "id": "pat-%06d" % i,
                "kind": "pattern",
                "status": "active",
                "subject": "钟丽潭",
                "predicate": "送礼",
                "value": "钟丽潭送小心心",
                "provenance": ["ep5", "ep7"],
                "support": 2,
            }
            for i in range(4)
        ]
        r = S.recall(con, "钟丽潭送礼", patterns=patterns, pattern_quota=1)
        n = len([i for i in r["items"] if i["kind"] == "pattern"])
        self.assertEqual(n, 1)
        con.close()

    def test_pending_pattern_not_injected(self):
        con = _empty_db()
        patterns = [
            {
                "id": "pat-000001",
                "kind": "pattern",
                "status": "pending",
                "subject": "钟丽潭",
                "predicate": "送礼",
                "value": "钟丽潭送小心心",
                "provenance": ["ep5"],
                "support": 1,
            }
        ]
        r = S.recall(con, "钟丽潭送礼", patterns=patterns)
        self.assertEqual(r["items"], [])
        con.close()


if __name__ == "__main__":
    unittest.main()
