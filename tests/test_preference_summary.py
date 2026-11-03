"""Tests for compiler/preference_summary.py.

铁律：每条陈述必须有证据，缺证据 = 拒绝写入 + 记审计。
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "compiler"))

from preference_summary import (
    MAX_TEXT_CHARS,
    PREFERENCE_SUMMARY_KIND,
    PreferenceCardStore,
    PreferenceEvidenceError,
    validate_card,
)

GOOD_EVIDENCE = [
    {"statement": "送小心心 x235", "evidence": "2026-11-03 直播日志礼物行"},
    {"statement": "常来直播间", "evidence": "2026-11-03 77 条事件记录"},
]


def make_store():
    tmp = tempfile.mkdtemp()
    return PreferenceCardStore(audit_path=os.path.join(tmp, "audit.jsonl")), tmp


class ValidateCardTests(unittest.TestCase):
    def test_valid_card(self):
        card = validate_card({
            "kind": PREFERENCE_SUMMARY_KIND,
            "subject": "钟丽潭",
            "text": "常客，送礼积极",
            "evidence": GOOD_EVIDENCE,
        })
        self.assertEqual(card["subject"], "钟丽潭")
        self.assertEqual(card["version"], 1)
        self.assertEqual(card["status"], "active")
        self.assertIn("updated_at", card)

    def test_wrong_kind_rejected(self):
        with self.assertRaises(PreferenceEvidenceError):
            validate_card({
                "kind": "fact", "subject": "x", "text": "y",
                "evidence": GOOD_EVIDENCE,
            })

    def test_empty_evidence_list_rejected(self):
        with self.assertRaises(PreferenceEvidenceError):
            validate_card({
                "kind": PREFERENCE_SUMMARY_KIND, "subject": "x",
                "text": "y", "evidence": [],
            })

    def test_statement_without_evidence_rejected(self):
        with self.assertRaises(PreferenceEvidenceError):
            validate_card({
                "kind": PREFERENCE_SUMMARY_KIND, "subject": "x", "text": "y",
                "evidence": [{"statement": "喜欢小心心特效", "evidence": ""}],
            })

    def test_statement_missing_evidence_key_rejected(self):
        with self.assertRaises(PreferenceEvidenceError):
            validate_card({
                "kind": PREFERENCE_SUMMARY_KIND, "subject": "x", "text": "y",
                "evidence": [{"statement": "喜欢小心心特效"}],
            })

    def test_empty_statement_rejected(self):
        with self.assertRaises(PreferenceEvidenceError):
            validate_card({
                "kind": PREFERENCE_SUMMARY_KIND, "subject": "x", "text": "y",
                "evidence": [{"statement": "  ", "evidence": "某日志"}],
            })

    def test_text_too_long_rejected(self):
        with self.assertRaises(PreferenceEvidenceError):
            validate_card({
                "kind": PREFERENCE_SUMMARY_KIND, "subject": "x",
                "text": "长" * (MAX_TEXT_CHARS + 1),
                "evidence": GOOD_EVIDENCE,
            })

    def test_empty_subject_rejected(self):
        with self.assertRaises(PreferenceEvidenceError):
            validate_card({
                "kind": PREFERENCE_SUMMARY_KIND, "subject": "  ",
                "text": "y", "evidence": GOOD_EVIDENCE,
            })

    def test_evidence_is_quote_not_raw_dump(self):
        # evidence 是引用，不是原文搬运：超长引用被拒绝
        with self.assertRaises(PreferenceEvidenceError):
            validate_card({
                "kind": PREFERENCE_SUMMARY_KIND, "subject": "x", "text": "y",
                "evidence": [{"statement": "s", "evidence": "原" * 301}],
            })


class StoreTests(unittest.TestCase):
    def test_create_and_get(self):
        store, _ = make_store()
        card = store.create("钟丽潭", "常客", GOOD_EVIDENCE)
        self.assertEqual(card["version"], 1)
        got = store.get("钟丽潭")
        self.assertEqual(got["text"], "常客")

    def test_create_rejected_logs_audit(self):
        store, tmp = make_store()
        with self.assertRaises(PreferenceEvidenceError):
            store.create("x", "y", [])
        audit = open(os.path.join(tmp, "audit.jsonl"), encoding="utf-8").read()
        entry = json.loads(audit.strip().split("\n")[-1])
        self.assertEqual(entry["action"], "rejected")
        self.assertEqual(entry["subject"], "x")

    def test_update_bumps_version(self):
        store, _ = make_store()
        store.create("钟丽潭", "常客", GOOD_EVIDENCE)
        card = store.update("钟丽潭", text="铁杆常客", evidence=GOOD_EVIDENCE)
        self.assertEqual(card["version"], 2)
        self.assertEqual(card["text"], "铁杆常客")

    def test_update_without_evidence_rejected(self):
        store, _ = make_store()
        store.create("钟丽潭", "常客", GOOD_EVIDENCE)
        with self.assertRaises(PreferenceEvidenceError):
            store.update("钟丽潭", evidence=[{"statement": "s", "evidence": ""}])
        # 旧卡不受影响
        self.assertEqual(store.get("钟丽潭")["version"], 1)

    def test_withdraw(self):
        store, _ = make_store()
        store.create("钟丽潭", "常客", GOOD_EVIDENCE)
        card = store.withdraw("钟丽潭", reason="测试撤回")
        self.assertEqual(card["status"], "withdrawn")
        self.assertEqual(store.list_active(), [])
        # 撤回后不能更新
        with self.assertRaises(PreferenceEvidenceError):
            store.update("钟丽潭", text="z")

    def test_update_missing_card(self):
        store, _ = make_store()
        with self.assertRaises(KeyError):
            store.update("不存在", text="z")


if __name__ == "__main__":
    unittest.main()
