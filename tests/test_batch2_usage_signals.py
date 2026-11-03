"""Tests for batch2 item 3: UsageTracker（使用频率回流，只增信号）。

Synthetic data only; nothing touches real user memory.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

from usage_signals import UsageTracker


class UsageTrackerTests(unittest.TestCase):
    def test_record_and_get(self):
        t = UsageTracker()
        snap = t.record_hit("pref:钟丽潭", kind="preference_summary")
        self.assertEqual(snap["hits"], 1)
        self.assertEqual(snap["kind"], "preference_summary")
        self.assertIn("first_hit", snap)
        self.assertIn("last_hit", snap)

    def test_hits_accumulate(self):
        t = UsageTracker()
        t.record_hit("pref:A")
        t.record_hit("pref:A")
        t.record_hit("pref:B")
        self.assertEqual(t.get("pref:A")["hits"], 2)
        self.assertEqual(t.get("pref:B")["hits"], 1)

    def test_report_sorted_desc(self):
        t = UsageTracker()
        t.record_hit("x")
        t.record_hit("y")
        t.record_hit("y")
        t.record_hit("y")
        rows = t.report()
        self.assertEqual(rows[0]["item_id"], "y")
        self.assertEqual(rows[0]["hits"], 3)
        self.assertEqual(rows[1]["item_id"], "x")

    def test_report_limit(self):
        t = UsageTracker()
        for i in range(10):
            t.record_hit("item-%d" % i)
        self.assertEqual(len(t.report(limit=3)), 3)

    def test_get_unknown_returns_none(self):
        self.assertIsNone(UsageTracker().get("nope"))

    def test_persistence_roundtrip(self):
        tmp = Path(tempfile.mkdtemp(prefix="stellamem-usage-"))
        p = tmp / "usage.json"
        t = UsageTracker(path=p)
        t.record_hit("pref:A", kind="preference_summary")
        t2 = UsageTracker(path=p)
        self.assertEqual(t2.get("pref:A")["hits"], 1)

    def test_corrupt_file_starts_empty(self):
        tmp = Path(tempfile.mkdtemp(prefix="stellamem-usage-"))
        p = tmp / "usage.json"
        p.write_text("not json{{{", encoding="utf-8")
        t = UsageTracker(path=p)
        self.assertEqual(t.report(), [])

    def test_no_delete_api(self):
        # 只增信号：模块不提供任何删除 / 清理方法
        t = UsageTracker()
        t.record_hit("pref:A")
        self.assertFalse(hasattr(t, "delete"))
        self.assertFalse(hasattr(t, "clear"))
        self.assertFalse(hasattr(t, "reset"))


if __name__ == "__main__":
    unittest.main()
