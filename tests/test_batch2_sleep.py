"""Tests for batch2 item 5: 睡眠巩固（文本版）。

Synthetic data only; nothing touches real user memory.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

import sleep_consolidation as SC
from sleep_consolidation import PatternStore, mine_patterns, pattern_candidates


def _ep(eid, claims):
    return {"episode_id": eid, "date": "2026-11-0%d" % int(eid[-1]), "claims": claims}


def _c(subject, predicate, value):
    return {"subject": subject, "predicate": predicate, "value": value}


class MineTests(unittest.TestCase):
    def test_recurring_becomes_pattern(self):
        eps = [
            _ep("ep5", [_c("钟丽潭", "送礼", "起哄时跟风刷小心心")]),
            _ep("ep6", [_c("goku", "发言", "只发表情")]),
            _ep("ep7", [_c("钟丽潭", "送礼", "起哄时跟风刷小心心")]),
        ]
        out = mine_patterns(eps, min_episodes=2)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["subject"], "钟丽潭")
        self.assertEqual(sorted(out[0]["provenance"]), ["ep5", "ep7"])
        self.assertEqual(out[0]["support"], 2)
        self.assertEqual(out[0]["status"], "pending")

    def test_single_episode_not_pattern(self):
        eps = [_ep("ep5", [_c("钟丽潭", "送礼", "刷了一个小心心")])]
        self.assertEqual(mine_patterns(eps, min_episodes=2), [])

    def test_different_values_not_merged(self):
        eps = [
            _ep("ep5", [_c("钟丽潭", "送礼", "刷小心心")]),
            _ep("ep7", [_c("钟丽潭", "送礼", "刷大啤酒")]),
        ]
        self.assertEqual(mine_patterns(eps, min_episodes=2), [])

    def test_duplicate_within_episode_counts_once(self):
        eps = [
            _ep("ep5", [_c("钟丽潭", "送礼", "刷小心心"), _c("钟丽潭", "送礼", "刷小心心")]),
            _ep("ep7", [_c("钟丽潭", "送礼", "刷小心心")]),
        ]
        out = mine_patterns(eps, min_episodes=2)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["support"], 2)


class StoreTests(unittest.TestCase):
    def test_approve_withdraw_flow(self):
        store = PatternStore()
        eps = [
            _ep("ep5", [_c("钟丽潭", "送礼", "刷小心心")]),
            _ep("ep7", [_c("钟丽潭", "送礼", "刷小心心")]),
        ]
        added = store.add_mined(mine_patterns(eps))
        pid = added[0]["id"]
        with self.assertRaises(ValueError):
            store.approve(pid, "")
        rec = store.approve(pid, "宇枫")
        self.assertEqual(rec["status"], "active")
        rec2 = store.withdraw(pid, "宇枫", "过时了")
        self.assertEqual(rec2["status"], "withdrawn")
        # provenance 保留，可回查
        self.assertEqual(sorted(rec2["provenance"]), ["ep5", "ep7"])

    def test_persistence(self):
        tmp = Path(tempfile.mkdtemp(prefix="stellamem-pattern-"))
        s1 = PatternStore(path=tmp / "patterns.json")
        eps = [
            _ep("ep5", [_c("a", "p", "v")]),
            _ep("ep7", [_c("a", "p", "v")]),
        ]
        added = s1.add_mined(mine_patterns(eps))
        s2 = PatternStore(path=tmp / "patterns.json")
        self.assertIsNotNone(s2.get(added[0]["id"]))


class CandidateTests(unittest.TestCase):
    def _active(self):
        return [
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

    def test_weight_below_one(self):
        hits = pattern_candidates(self._active(), "钟丽潭起哄送礼")
        self.assertEqual(len(hits), 1)
        self.assertLessEqual(hits[0]["score"], SC.PATTERN_WEIGHT)
        self.assertLess(SC.PATTERN_WEIGHT, 1.0)

    def test_pending_not_candidate(self):
        pats = self._active()
        pats[0]["status"] = "pending"
        self.assertEqual(pattern_candidates(pats, "钟丽潭起哄送礼"), [])

    def test_no_match_empty(self):
        self.assertEqual(pattern_candidates(self._active(), "今天天气怎么样"), [])


if __name__ == "__main__":
    unittest.main()
