"""Diary summary marker: persistent 'already summarized' + startup rebuild.

布雷登 3.2 (EigenFlux, 2026-11-07): incremental summaries need a
persistent marker and a startup rebuild from backup, or history is
silently lost on restart. Synthetic fixture roots only.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

from daily_diary import (
    append_diary,
    rebuild_summary_state,
    record_summary,
    summary_state_path,
    synthesize_long_term,
)

DAY1 = "2026-11-06"
DAY2 = "2026-11-07"
DAY3 = "2026-11-08"


class MarkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "memories"

    def _diary_with_change(self, day, text):
        append_diary(self.root, "user", day, "变", text)

    def test_synthesize_records_marker(self):
        self._diary_with_change(DAY1, "决定以后开播先唱一首再聊")
        result = synthesize_long_term(self.root, "user", DAY1)
        self.assertEqual(result["status"], "ok")
        state = json.loads(summary_state_path(self.root).read_text(encoding="utf-8"))
        self.assertEqual(state["subjects"]["user"]["summarized_days"], [DAY1])
        self.assertEqual(state["subjects"]["user"]["last_summarized_day"], DAY1)

    def test_noop_does_not_mark(self):
        result = synthesize_long_term(self.root, "user", DAY1)
        self.assertEqual(result["status"], "noop")
        self.assertFalse(summary_state_path(self.root).exists())

    def test_rebuild_finds_gap(self):
        self._diary_with_change(DAY1, "决定以后开播先唱一首再聊")
        self._diary_with_change(DAY2, "决定把游戏环节挪到后半段")
        synthesize_long_term(self.root, "user", DAY1)
        result = rebuild_summary_state(self.root, "user")
        self.assertEqual(result["summarized_days"], [DAY1])
        self.assertEqual(result["gap_days"], [DAY2])

    def test_rebuild_after_marker_loss(self):
        self._diary_with_change(DAY1, "决定以后开播先唱一首再聊")
        self._diary_with_change(DAY2, "决定把游戏环节挪到后半段")
        synthesize_long_term(self.root, "user", DAY1)
        summary_state_path(self.root).unlink()
        result = rebuild_summary_state(self.root, "user")
        self.assertEqual(result["summarized_days"], [DAY1])
        self.assertEqual(result["gap_days"], [DAY2])

    def test_rebuild_no_gap_when_clean(self):
        self._diary_with_change(DAY1, "决定以后开播先唱一首再聊")
        self._diary_with_change(DAY2, "决定把游戏环节挪到后半段")
        synthesize_long_term(self.root, "user", DAY1)
        synthesize_long_term(self.root, "user", DAY2)
        result = rebuild_summary_state(self.root, "user")
        self.assertEqual(result["gap_days"], [])
        self.assertEqual(result["summarized_days"], [DAY1, DAY2])

    def test_rebuild_ignores_days_without_changes(self):
        self._diary_with_change(DAY1, "决定以后开播先唱一首再聊")
        append_diary(self.root, "user", DAY2, "触点", "钟丽潭点了《枫叶》")
        result = rebuild_summary_state(self.root, "user")
        self.assertEqual(result["gap_days"], [DAY1])
        self.assertNotIn(DAY2, result["gap_days"])

    def test_corrupt_marker_file_is_rebuilt_not_fatal(self):
        self._diary_with_change(DAY1, "决定以后开播先唱一首再聊")
        path = summary_state_path(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        result = rebuild_summary_state(self.root, "user")
        self.assertEqual(result["gap_days"], [DAY1])
        # And a fresh record works on top of the rebuilt state.
        record_summary(self.root, "user", DAY1)
        state = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(state["subjects"]["user"]["summarized_days"], [DAY1])


if __name__ == "__main__":
    unittest.main()
