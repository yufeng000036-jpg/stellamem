"""Improved round P1 tests: subjects, bridge, persona adapter, promises.
Synthetic placeholder data only (测试用户A / 测试人格B / 测试观众A)."""

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

import bridge as B
import persona_adapter as PA
import promises as PR
import subjects as SU


def _rec(subject, value, kind="fact"):
    return {"subject": subject, "kind": kind, "value": value, "status": "active"}


class SubjectTests(unittest.TestCase):
    def test_user_cannot_retrieve_persona(self):
        recs = [_rec("user", "测试用户A的私人偏好"), _rec("persona", "测试人格B的直播偏好")]
        out = SU.recall_for_subject(recs, "user")
        self.assertEqual([r["value"] for r in out], ["测试用户A的私人偏好"])

    def test_persona_cannot_retrieve_user(self):
        recs = [_rec("user", "测试用户A的私人偏好"), _rec("persona", "测试人格B的直播偏好")]
        out = SU.recall_for_subject(recs, "persona")
        self.assertEqual([r["value"] for r in out], ["测试人格B的直播偏好"])

    def test_viewer_isolated(self):
        recs = [_rec("viewer", "测试观众A常来"), _rec("persona", "测试人格B的直播偏好")]
        out = SU.recall_for_subject(recs, "viewer")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["value"], "测试观众A常来")

    def test_unclassified_is_denied_everywhere(self):
        recs = [_rec("某个自由文本主题", "未打标签的记录")]
        for subj in ("user", "persona", "viewer"):
            self.assertEqual(SU.recall_for_subject(recs, subj), [])

    def test_bridge_copy_is_visible_to_target_only(self):
        copy = {"subject": "persona", "kind": "fact", "value": "测试桥接内容",
                "bridge_id": "bridge-0001", "origin_id": "orig-1", "status": "active"}
        original = _rec("user", "测试用户A的私人偏好")
        recs = [original, copy]
        self.assertIn(copy, SU.recall_for_subject(recs, "persona"))
        self.assertNotIn(original, SU.recall_for_subject(recs, "persona"))
        self.assertNotIn(copy, SU.recall_for_subject(recs, "user"))

    def test_unknown_subject_rejected(self):
        with self.assertRaises(ValueError):
            SU.validate_subject("not-a-subject")


class BridgeTests(unittest.TestCase):
    def test_before_approval_no_copy(self):
        store = B.BridgeStore()
        req = store.create("user", "persona", "result",
                           {"x": "测试项目A", "result": "测试结果A"}, origin_id="orig-1")
        self.assertEqual(req["status"], "pending")
        self.assertIsNone(store.approved_copy(req["id"]))

    def test_after_approval_copy_carries_provenance(self):
        store = B.BridgeStore()
        req = store.create("user", "persona", "relationship",
                           {"a": "测试用户A", "b": "测试人格B", "c": "测试关系C"},
                           origin_id="orig-2")
        copy = store.approve(req["id"], approver="测试用户A", confirmed_full_text=True)
        self.assertEqual(copy["subject"], "persona")
        self.assertEqual(copy["origin_id"], "orig-2")
        self.assertEqual(copy["approver"], "测试用户A")
        self.assertEqual(copy["value"], "测试用户A 是 测试人格B 的 测试关系C")
        self.assertIsNotNone(copy["approved_at"])

    def test_approval_requires_full_text_confirmation(self):
        store = B.BridgeStore()
        req = store.create("user", "persona", "method",
                           {"task": "测试任务", "method": "测试方法"})
        with self.assertRaises(ValueError):
            store.approve(req["id"], approver="测试用户A", confirmed_full_text=False)
        self.assertEqual(store.get(req["id"])["status"], "pending")

    def test_blacklist_bypass_still_requires_approval(self):
        # Rephrased content evades the keyword list; the store must still
        # hold it pending and invisible until a human approves full text.
        store = B.BridgeStore()
        req = store.create("user", "persona", "result",
                           {"x": "测试项目B", "result": "改写后的测试敏感描述"})
        self.assertFalse(req["blacklist_flagged"])
        self.assertIsNone(store.approved_copy(req["id"]))
        copy = store.approve(req["id"], approver="测试用户A", confirmed_full_text=True)
        self.assertIsNotNone(copy)

    def test_blacklist_flagged_requires_acknowledgement(self):
        store = B.BridgeStore()
        req = store.create("user", "persona", "result",
                           {"x": "测试项目C", "result": "内含密码字样"})
        self.assertTrue(req["blacklist_flagged"])
        with self.assertRaises(ValueError):
            store.approve(req["id"], approver="测试用户A", confirmed_full_text=True)
        copy = store.approve(req["id"], approver="测试用户A",
                             confirmed_full_text=True, acknowledge_flag=True)
        self.assertIsNotNone(copy)

    def test_unknown_template_and_same_subject_rejected(self):
        store = B.BridgeStore()
        with self.assertRaises(ValueError):
            store.create("user", "persona", "free_text", {"text": "任意内容"})
        with self.assertRaises(ValueError):
            store.create("user", "user", "result", {"x": "a", "result": "b"})

    def test_batch_failure_aborts_whole_batch(self):
        store = B.BridgeStore()
        r1 = store.create("user", "persona", "result", {"x": "测试项目D", "result": "结果D"})
        r2 = store.create("user", "persona", "result", {"x": "测试项目E", "result": "结果E"})
        store.withdraw(r2["id"], by="测试用户A")
        with self.assertRaises(ValueError):
            store.approve_batch([r1["id"], r2["id"]], approver="测试用户A",
                                confirmed_full_text=True)
        self.assertEqual(store.get(r1["id"])["status"], "pending")

    def test_batch_success(self):
        store = B.BridgeStore()
        r1 = store.create("user", "persona", "result", {"x": "测试项目F", "result": "结果F"})
        r2 = store.create("user", "persona", "result", {"x": "测试项目G", "result": "结果G"})
        copies = store.approve_batch([r1["id"], r2["id"]], approver="测试用户A",
                                     confirmed_full_text=True)
        self.assertEqual(len(copies), 2)

    def test_withdrawal(self):
        store = B.BridgeStore()
        req = store.create("user", "persona", "result", {"x": "测试项目H", "result": "结果H"})
        out = store.withdraw(req["id"], by="测试用户A")
        self.assertEqual(out["status"], "withdrawn")
        with self.assertRaises(ValueError):
            store.approve(req["id"], approver="测试用户A", confirmed_full_text=True)

    def test_expiry_is_fail_closed(self):
        now = datetime.now(timezone.utc)
        store = B.BridgeStore(now_fn=lambda: now)
        old = now - timedelta(hours=73)  # 72h TTL, created 73h ago
        req = store.create("user", "persona", "result",
                           {"x": "测试项目I", "result": "结果I"}, created_at=old)
        self.assertEqual(store.get(req["id"])["status"], "expired")
        with self.assertRaises(ValueError):
            store.approve(req["id"], approver="测试用户A", confirmed_full_text=True)
        self.assertIsNone(store.approved_copy(req["id"]))


class PersonaAdapterTests(unittest.TestCase):
    def test_pending_not_retrievable_until_spoken(self):
        ad = PA.PersonaAdapter()
        ad.set_card("测试观众A", "常客测试卡")
        ev = ad.record_event("测试观众A", "测试弹幕内容")
        self.assertEqual(ev["status"], "pending_confirmation")
        pack = ad.retrieve_pack(speaker="测试观众A", budget_chars=800)
        self.assertNotIn("测试弹幕内容", pack["text"])
        ad.confirm_spoken(ev["id"], confirmed=True)
        pack = ad.retrieve_pack(speaker="测试观众A", budget_chars=800)
        self.assertIn("测试弹幕内容", pack["text"])

    def test_confirm_requires_explicit_flag(self):
        ad = PA.PersonaAdapter()
        ev = ad.record_event("测试观众A", "测试弹幕内容")
        ad.confirm_spoken(ev["id"], confirmed=False)
        self.assertEqual(ad.list_pending()[0]["status"], "pending_confirmation")

    def test_char_budget_and_speaker_card_first(self):
        ad = PA.PersonaAdapter()
        ad.set_card("测试观众A", "常客测试卡")
        for i in range(5):
            ev = ad.record_event("测试观众A", f"测试已说内容{i}" + "测试" * 20)
            ad.confirm_spoken(ev["id"], confirmed=True)
        pack = ad.retrieve_pack(speaker="测试观众A", budget_chars=120)
        self.assertLessEqual(len(pack["text"]), 120)
        self.assertTrue(pack["text"].startswith("[测试观众A]"))
        self.assertEqual(pack["subject"], "persona")

    def test_no_user_writes_from_adapter(self):
        ad = PA.PersonaAdapter()
        with self.assertRaises(ValueError):
            ad.record_event("测试观众A", "测试内容", subject="user")


class PromiseTests(unittest.TestCase):
    def setUp(self):
        self.store = PR.PromiseStore()

    def tearDown(self):
        self.store.close()

    def test_fulfil_with_evidence(self):
        p = self.store.create("测试承诺A", subject="persona")
        out = self.store.fulfil(p["id"], evidence="测试已播出证据")
        self.assertEqual(out["status"], "fulfilled")
        self.assertEqual(out["evidence"], "测试已播出证据")
        self.assertEqual(self.store.list_open(), [])

    def test_fulfil_requires_evidence(self):
        p = self.store.create("测试承诺B")
        with self.assertRaises(ValueError):
            self.store.fulfil(p["id"], evidence="")

    def test_expire_and_fulfil_after_expiry_refused_but_auditable(self):
        past = "2000-01-01T00:00:00+00:00"
        p = self.store.create("测试承诺C", expires_at=past)
        with self.assertRaises(ValueError):
            self.store.fulfil(p["id"], evidence="迟到的证据")
        rec = self.store.get(p["id"])
        self.assertEqual(rec["status"], "expired")
        actions = [a["action"] for a in self.store.audit(p["id"])]
        self.assertIn("fulfil_refused_expired", actions)
        self.assertEqual(self.store.list_open(), [])

    def test_expire_due(self):
        self.store.create("测试承诺D", expires_at="2000-01-01T00:00:00+00:00")
        self.store.create("测试承诺E", expires_at="2999-01-01T00:00:00+00:00")
        expired = self.store.expire_due()
        self.assertEqual(len(expired), 1)
        self.assertEqual(len(self.store.list_open()), 1)


if __name__ == "__main__":
    unittest.main()
