"""Tests for the summary-only bridge rule (docs/adr/0004) helpers.

raw_source_hits() 是噪音过滤器：命中只提醒审批人核对，
不自动拒绝；没命中也不代表干净。真正的边界永远是人工看全文。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "compiler"))

from bridge import raw_source_hits


class RawSourceHitsTests(unittest.TestCase):
    def test_raw_gift_log_line_flagged(self):
        fields = {"x": "钟丽潭", "result": "[20:31:02] [INFO] 礼物: 钟丽潭(id=277***94) -> 小心心 x235 (235音浪)"}
        hits = raw_source_hits(fields)
        self.assertTrue(hits, "原始礼物日志行应当被标记")

    def test_raw_chat_jsonl_flagged(self):
        fields = {
            "x": "某观众",
            "result": '{"type":"chat","t":"2026-11-03T12:00:00Z","user":"某","userId":"123","content":"哈哈"}',
        }
        hits = raw_source_hits(fields)
        self.assertTrue(hits, "聊天原始 JSON 行应当被标记")

    def test_raw_timestamped_line_flagged(self):
        fields = {"x": "y", "result": "[2026-11-03 20:31:02] 某事件发生"}
        self.assertTrue(raw_source_hits(fields))

    def test_distilled_summary_not_flagged(self):
        # 提炼后的摘要：无原始行特征，不应误报
        fields = {
            "a": "钟丽潭",
            "b": "鲸鱼娘直播间",
            "c": "常客（2026-11-03 送小心心 x235，77 条互动事件）",
        }
        self.assertEqual(raw_source_hits(fields), [])

    def test_empty_fields_no_hits(self):
        self.assertEqual(raw_source_hits({}), [])
        self.assertEqual(raw_source_hits({"a": "", "b": "", "c": ""}), [])

    def test_hits_deduplicated(self):
        fields = {
            "a": '{"type":"chat","userId":"1"}',
            "b": '{"type":"chat","userId":"2"}',
        }
        hits = raw_source_hits(fields)
        self.assertEqual(len(hits), len(set(hits)))


if __name__ == "__main__":
    unittest.main()
