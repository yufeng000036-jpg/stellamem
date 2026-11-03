"""Preference cards must enter human review and keep append-only versions.

Synthetic data only. Reproduces the review finding that create/update
publish status=active, overwrite the only in-memory copy, and audit
op+version without the card content.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "compiler"))

from preference_summary import PreferenceCardStore

GOOD_EVIDENCE = [
    {"statement": "送小心心 x235", "evidence": "2026-11-03 直播日志礼物行"},
    {"statement": "常来直播间", "evidence": "2026-11-03 77 条事件记录"},
]


def make_store():
    import tempfile

    tmp = tempfile.mkdtemp()
    return PreferenceCardStore(audit_path=os.path.join(tmp, "audit.jsonl")), tmp


def _audit_entries(tmp):
    path = os.path.join(tmp, "audit.jsonl")
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


class PreferenceReviewTests(unittest.TestCase):
    def test_create_and_update_stay_pending_until_named_approval(self):
        store, _tmp = make_store()
        created = store.create("钟丽潭", "常客", GOOD_EVIDENCE)
        self.assertEqual(created["status"], "pending")
        self.assertEqual(store.list_active(), [])
        with self.assertRaises(ValueError):
            store.approve("钟丽潭", by="  ")
        approved = store.approve("钟丽潭", by="宇")
        self.assertEqual(approved["status"], "active")
        self.assertEqual(approved["approved_by"], "宇")
        self.assertEqual([c["subject"] for c in store.list_active()], ["钟丽潭"])
        updated = store.update("钟丽潭", text="铁杆常客")
        self.assertEqual(updated["status"], "pending")
        self.assertEqual(store.list_active(), [])

    def test_history_is_append_only_and_audit_keeps_content(self):
        store, tmp = make_store()
        store.create("钟丽潭", "常客", GOOD_EVIDENCE)
        store.update("钟丽潭", text="铁杆常客")
        if not hasattr(store, "history"):
            self.fail("update overwrote the only in-memory version; no append-only history")
        hist = store.history("钟丽潭")
        self.assertGreaterEqual(len(hist), 2)
        self.assertEqual(hist[0]["text"], "常客")
        self.assertEqual(hist[0]["evidence"], GOOD_EVIDENCE)
        self.assertEqual(hist[1]["text"], "铁杆常客")
        # mutating the returned history must not rewrite the log
        hist[0]["text"] = "tampered"
        self.assertEqual(store.history("钟丽潭")[0]["text"], "常客")
        created_audit = _audit_entries(tmp)[0]
        blob = json.dumps(created_audit, ensure_ascii=False)
        self.assertIn("常客", blob)
        self.assertIn("送小心心 x235", blob)
        self.assertIn("content", created_audit)

    def test_rollback_restores_prior_version_without_erasing_history(self):
        store, _tmp = make_store()
        store.create("钟丽潭", "常客", GOOD_EVIDENCE)
        store.approve("钟丽潭", by="宇")
        store.update("钟丽潭", text="铁杆常客", evidence=GOOD_EVIDENCE)
        if not hasattr(store, "rollback"):
            self.fail("no rollback; the previous card content was overwritten")
        rolled = store.rollback("钟丽潭", version=1, by="宇")
        self.assertEqual(rolled["text"], "常客")
        self.assertEqual(rolled["evidence"], GOOD_EVIDENCE)
        self.assertEqual(rolled["status"], "pending")
        self.assertEqual(store.list_active(), [])
        hist = store.history("钟丽潭")
        self.assertEqual(hist[0]["text"], "常客")
        self.assertIn("铁杆常客", [item["text"] for item in hist])
        self.assertGreater(len(hist), 2)
        again = store.approve("钟丽潭", by="宇")
        self.assertEqual(again["status"], "active")
        self.assertEqual(again["text"], "常客")


if __name__ == "__main__":
    unittest.main()
