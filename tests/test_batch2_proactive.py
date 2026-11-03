"""Tests for batch2 item 4: SenseNova 三件套（本地、可审计、可关闭）。

Synthetic data only; nothing touches real user memory.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

from proactive import ProactiveCenter, ProactiveDisabled


def _claim(cid, subject, predicate, value, status="active", valid_to=None):
    c = {
        "claim_id": cid,
        "subject": subject,
        "predicate": predicate,
        "value": value,
        "status": status,
        "evidence": "synthetic evidence for " + cid,
    }
    if valid_to:
        c["valid_to"] = valid_to
    return c


class ReminderTests(unittest.TestCase):
    def test_contradiction_proposes_pending(self):
        pc = ProactiveCenter()
        out = pc.propose_reminders(
            [_claim("n1", " s ", "喜欢", "白米饭")], [_claim("o1", "s", "喜欢", "面条")]
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["status"], "pending")
        self.assertEqual(out[0]["type"], "contradiction")

    def test_no_contradiction_no_reminder(self):
        pc = ProactiveCenter()
        out = pc.propose_reminders(
            [_claim("n1", "s", "喜欢", "白米饭")], [_claim("o1", "s", "喜欢", "白米饭")]
        )
        self.assertEqual(out, [])

    def test_confirm_requires_named_human(self):
        pc = ProactiveCenter()
        out = pc.propose_reminders(
            [_claim("n1", "s", "喜欢", "白米饭")], [_claim("o1", "s", "喜欢", "面条")]
        )
        with self.assertRaises(ValueError):
            pc.confirm_reminder(out[0]["id"], "")
        rec = pc.confirm_reminder(out[0]["id"], "宇枫")
        self.assertEqual(rec["status"], "confirmed")
        self.assertEqual(rec["confirmed_by"], "宇枫")

    def test_dismiss(self):
        pc = ProactiveCenter()
        out = pc.propose_reminders(
            [_claim("n1", "s", "喜欢", "白米饭")], [_claim("o1", "s", "喜欢", "面条")]
        )
        rec = pc.dismiss_reminder(out[0]["id"], "宇枫", "误报")
        self.assertEqual(rec["status"], "dismissed")

    def test_expiry_check(self):
        pc = ProactiveCenter()
        out = pc.check_expiry(
            [_claim("o1", "s", "订阅", "会员", valid_to="2020-01-01T00:00:00+00:00")],
            now_iso="2026-11-03T00:00:00+00:00",
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["type"], "expired")

    def test_no_auto_push_path(self):
        # 不自动打扰：模块没有任何发送 / 推送方法
        pc = ProactiveCenter()
        for name in ("push", "send", "notify", "auto_apply", "dispatch"):
            self.assertFalse(hasattr(pc, name), name)


class BriefingTests(unittest.TestCase):
    def test_briefing_text(self):
        pc = ProactiveCenter()
        b = pc.session_briefing(
            [
                {
                    "id": "t1",
                    "title": "写第9期流程单",
                    "status": "open",
                    "updated_at": "2026-11-03T10:00:00+00:00",
                }
            ],
            {"session_id": "ep8", "done": ["开播"], "next": ["复盘"]},
        )
        self.assertIn("写第9期流程单", b["text"])
        self.assertIn("开播", b["text"])
        self.assertEqual(b["open_count"], 1)

    def test_briefing_no_side_effects(self):
        pc = ProactiveCenter()
        b1 = pc.session_briefing([], None)
        b2 = pc.session_briefing([], None)
        self.assertEqual(b1["text"], b2["text"])  # 纯函数，确定性


class ConflictTests(unittest.TestCase):
    def test_detect_conflict_both_sides(self):
        pc = ProactiveCenter()
        out = pc.detect_conflicts(
            _claim("n1", "s", "偏好", "新说法"), [_claim("o1", "s", "偏好", "旧说法")]
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["new"]["value"], "新说法")
        self.assertEqual(out[0]["old"]["value"], "旧说法")
        self.assertEqual(out[0]["status"], "pending")

    def test_adjudicate_records_not_applies(self):
        pc = ProactiveCenter()
        out = pc.detect_conflicts(
            _claim("n1", "s", "偏好", "新说法"), [_claim("o1", "s", "偏好", "旧说法")]
        )
        rec = pc.adjudicate(out[0]["id"], "keep_new", "宇枫")
        self.assertEqual(rec["status"], "adjudicated")
        self.assertEqual(rec["decision"], "keep_new")
        self.assertIn("suggested_action", rec)
        # 只记录不执行：旧记忆对象本身未被改动（调用方在批准流程里做）
        self.assertEqual(out[0]["status"], "pending")

    def test_adjudicate_requires_named_human(self):
        pc = ProactiveCenter()
        out = pc.detect_conflicts(
            _claim("n1", "s", "偏好", "新说法"), [_claim("o1", "s", "偏好", "旧说法")]
        )
        with self.assertRaises(ValueError):
            pc.adjudicate(out[0]["id"], "keep_new", "")
        with self.assertRaises(ValueError):
            pc.adjudicate(out[0]["id"], "whatever", "宇枫")


class DisabledTests(unittest.TestCase):
    def test_all_off(self):
        pc = ProactiveCenter(enabled=False)
        with self.assertRaises(ProactiveDisabled):
            pc.propose_reminders([], [])
        with self.assertRaises(ProactiveDisabled):
            pc.session_briefing([], None)
        with self.assertRaises(ProactiveDisabled):
            pc.detect_conflicts({}, [])

    def test_single_switch_off(self):
        pc = ProactiveCenter(reminders_on=False)
        with self.assertRaises(ProactiveDisabled):
            pc.propose_reminders([], [])
        # 别的开关不受影响
        b = pc.session_briefing([], None)
        self.assertIn("没有待办", b["text"])

    def test_persistence(self):
        tmp = Path(tempfile.mkdtemp(prefix="stellamem-proactive-"))
        pc = ProactiveCenter(store_dir=tmp)
        out = pc.propose_reminders(
            [_claim("n1", "s", "喜欢", "白米饭")], [_claim("o1", "s", "喜欢", "面条")]
        )
        pc2 = ProactiveCenter(store_dir=tmp)
        self.assertEqual(len(pc2.list_reminders()), 1)
        self.assertEqual(pc2.list_reminders()[0]["id"], out[0]["id"])


if __name__ == "__main__":
    unittest.main()
