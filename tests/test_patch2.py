"""PATCH2 tests: namespace tags, persona persistence, bridge transaction.

Synthetic placeholder data only (测试用户A / 测试人格B / 测试观众A).
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

import bridge as B
import legacy_tags as LT
import persona_adapter as PA
import persona_store as PS
import subjects as SU


def _rec(subject, value, kind="fact", **extra):
    row = {
        "claim_id": f"c-{value}",
        "subject": subject,
        "kind": kind,
        "value": value,
        "status": "active",
    }
    row.update(extra)
    return row


class NamespaceFieldTests(unittest.TestCase):
    def test_namespace_wins_over_free_text_subject(self):
        rec = _rec("测试用户A", "测试偏好", namespace="user")
        self.assertEqual(SU.subject_of(rec), "user")
        self.assertEqual(SU.recall_for_subject([rec], "user"), [rec])

    def test_legacy_subject_still_works_without_namespace(self):
        self.assertEqual(SU.subject_of({"subject": "用户"}), "user")
        self.assertIsNone(SU.subject_of({"subject": "测试用户A"}))

    def test_invalid_namespace_is_fail_closed(self):
        # D13 fix: a present-but-invalid namespace must not fall back to a
        # valid subject (that would silently change visibility).
        rec = {"subject": "user", "namespace": "某个名字", "value": "x"}
        self.assertIsNone(SU.subject_of(rec))
        self.assertEqual(SU.recall_for_subject([rec], "user"), [])

    def test_bridge_copy_without_namespace_still_visible_to_target(self):
        copy = {
            "subject": "persona",
            "value": "测试桥接副本",
            "bridge_id": "bridge-0001",
            "origin_id": "orig-1",
            "status": "active",
        }
        self.assertEqual(SU.recall_for_subject([copy], "persona"), [copy])


class LegacyTagPlanTests(unittest.TestCase):
    def test_classify_rules(self):
        self.assertEqual(LT.classify_record({"subject": "user"}), ("user", "keep"))
        self.assertEqual(LT.classify_record({"subject": "用户"}), ("user", "set"))
        self.assertEqual(LT.classify_record({"subject": "测试用户A"}), ("user", "set"))
        self.assertEqual(LT.classify_record({"subject": "人格"}), ("persona", "manual_hold"))
        self.assertEqual(LT.classify_record({"subject": "观众"}), ("viewer", "manual_hold"))
        self.assertEqual(LT.classify_record({"subject": "persona"}), ("persona", "keep"))
        self.assertEqual(
            LT.classify_record({"namespace": "viewer", "subject": "x"}), ("viewer", "keep")
        )

    def test_plan_counts_and_pure(self):
        recs = [
            _rec("测试用户A", "a"),
            _rec("用户", "b"),
            _rec("user", "c"),
            _rec("人格", "d"),
            _rec("观众", "e"),
            _rec("观众甲", "f"),
        ]
        before = [dict(r) for r in recs]
        plan = LT.plan_legacy_tags(recs)
        self.assertEqual(plan["total_records"], 6)
        self.assertEqual(plan["by_action"], {"keep": 1, "set": 3, "manual_hold": 2})
        self.assertEqual(plan["by_namespace"], {"user": 4, "persona": 1, "viewer": 1})
        self.assertEqual(plan["manual_hold_count"], 2)
        self.assertEqual(plan["plan_size"], 3)
        self.assertFalse(plan["changed_anything"])
        self.assertFalse(plan["wrote_anything"])
        self.assertEqual(recs, before)  # input untouched

    def test_plan_hash_is_deterministic(self):
        recs = [_rec("测试用户A", "a"), _rec("测试用户B", "b")]
        first = LT.plan_legacy_tags(recs)["plan_sha256"]
        recs.reverse()
        second = LT.plan_legacy_tags(recs)["plan_sha256"]
        self.assertEqual(first, second)

    def test_tagged_records_memory_only(self):
        recs = [_rec("测试用户A", "a"), _rec("人格", "b")]
        tagged = LT.tagged_records(recs)
        self.assertEqual(tagged[0]["namespace"], "user")
        self.assertNotIn("namespace", tagged[1])  # manual hold is not auto-tagged
        self.assertNotIn("namespace", recs[0])  # input untouched
        self.assertEqual(tagged[0]["subject"], "测试用户A")  # legacy field unchanged


class NamespaceRetrievalParityTests(unittest.TestCase):
    def setUp(self):
        # Legacy production-style data: free-text subjects only.
        self.recs = [_rec("测试用户A", f"自由文本记录{i}") for i in range(20)]

    def test_user_sees_all_legacy_records_after_dry_run_tagging(self):
        tagged = LT.tagged_records(self.recs)
        user_view = SU.recall_for_subject(tagged, "user")
        # 20 free-text rows become namespace=user.
        self.assertEqual(len(user_view), 20)
        self.assertEqual({r["value"] for r in user_view}, {f"自由文本记录{i}" for i in range(20)})

    def test_persona_is_empty_without_bridge(self):
        tagged = LT.tagged_records(self.recs)
        self.assertEqual(SU.recall_for_subject(tagged, "persona"), [])

    def test_bridge_copy_visible_to_target_only(self):
        tagged = LT.tagged_records(self.recs)
        copy = {
            "claim_id": "c-bridge",
            "subject": "persona",
            "namespace": "persona",
            "value": "测试桥接副本",
            "bridge_id": "bridge-0009",
            "origin_id": "c-自由文本记录0",
            "status": "active",
        }
        tagged.append(copy)
        persona_view = SU.recall_for_subject(tagged, "persona")
        user_view = SU.recall_for_subject(tagged, "user")
        self.assertEqual(persona_view, [copy])
        self.assertNotIn(copy, user_view)
        self.assertEqual(len(user_view), 20)


class PersonaStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "persona.db"
        self.store = PS.PersonaStore(self.path)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_record_fields_and_pending_invisible(self):
        ev = self.store.record("测试人格B说过的话", source="测试观众A")
        self.assertEqual(ev["status"], "pending_confirmation")
        for key in ("event_id", "subject", "text", "created_at", "status", "spoken_at", "source"):
            self.assertIn(key, ev)
        self.assertIsNone(ev["spoken_at"])
        self.assertNotIn("测试人格B说过的话", self.store.pack()["text"])
        self.assertFalse(self.store.pack()["pending_visible"])

    def test_duplicate_event_id_rejected(self):
        self.store.record("第一句", source="测试观众A", event_id="e-1")
        with self.assertRaises(ValueError):
            self.store.record("第二句", source="测试观众A", event_id="e-1")
        self.assertEqual(len(self.store.list_pending()), 1)
        self.assertEqual(self.store.get("e-1")["text"], "第一句")

    def test_confirm_then_review_gate(self):
        ev = self.store.record("测试人格B确认过的话", source="测试观众A")
        with self.assertRaises(ValueError):
            self.store.review(ev["event_id"], reviewer="测试用户A")  # not confirmed yet
        confirmed = self.store.confirm_spoken(ev["event_id"])
        self.assertEqual(confirmed["status"], "confirmed")
        self.assertIsNotNone(confirmed["spoken_at"])
        self.assertIn("测试人格B确认过的话", self.store.pack()["text"])
        with self.assertRaises(ValueError):
            self.store.review(ev["event_id"], reviewer="")  # reviewer required
        reviewed = self.store.review(ev["event_id"], reviewer="测试用户A")
        self.assertEqual(reviewed["status"], "reviewed")
        self.assertEqual([r["event_id"] for r in self.store.export_pack()], [ev["event_id"]])

    def test_confirm_requires_pending(self):
        ev = self.store.record("一句话", source="s")
        self.store.confirm_spoken(ev["event_id"])
        self.store.review(ev["event_id"], reviewer="r")
        with self.assertRaises(ValueError):
            self.store.confirm_spoken(ev["event_id"])  # reviewed is terminal

    def test_restart_persistence(self):
        ev = self.store.record("重启前的话", source="测试观众A")
        self.store.confirm_spoken(ev["event_id"])
        self.store.close()
        reopened = PS.PersonaStore(self.path)
        try:
            got = reopened.get(ev["event_id"])
            self.assertEqual(got["status"], "confirmed")
            self.assertEqual(got["text"], "重启前的话")
        finally:
            reopened.close()
        self.store = PS.PersonaStore(":memory:")  # tearDown closes this

    def test_subject_user_refused_and_source_required(self):
        with self.assertRaises(ValueError):
            self.store.record("x", source="s", subject="user")
        with self.assertRaises(ValueError):
            self.store.record("x", source="")

    def test_append_only_log(self):
        ev = self.store.record("日志测试", source="s")
        self.store.confirm_spoken(ev["event_id"])
        self.store.review(ev["event_id"], reviewer="r")
        actions = [row["action"] for row in self.store.log(ev["event_id"])]
        self.assertEqual(actions, ["recorded", "confirmed_spoken", "reviewed"])


class PersonaAdapterPersistentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "persona.db"
        self.store = PS.PersonaStore(self.path)
        self.adapter = PA.PersonaAdapter(store=self.store)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_adapter_events_persist_across_restart(self):
        ev = self.adapter.record_event("测试观众A", "适配器持久化测试")
        self.adapter.confirm_spoken(ev["id"], confirmed=True)
        self.store.close()
        reopened = PS.PersonaStore(self.path)
        try:
            adapter2 = PA.PersonaAdapter(store=reopened)
            self.assertIn("适配器持久化测试", adapter2.retrieve_pack()["text"])
            self.assertEqual([e["id"] for e in adapter2.list_confirmed()], [ev["id"]])
        finally:
            reopened.close()
        self.store = PS.PersonaStore(":memory:")  # tearDown closes this

    def test_review_then_export(self):
        ev = self.adapter.record_event("测试观众A", "待复核事件")
        self.adapter.confirm_spoken(ev["id"], confirmed=True)
        self.assertEqual(self.adapter.export_reviewed(), [])
        self.adapter.review_spoken(ev["id"], reviewer="测试用户A")
        exported = self.adapter.export_reviewed()
        self.assertEqual([e["event_id"] for e in exported], [ev["id"]])

    def test_in_memory_adapter_has_no_review_step(self):
        adapter = PA.PersonaAdapter()
        ev = adapter.record_event("测试观众A", "内存态")
        with self.assertRaises(RuntimeError):
            adapter.review_spoken(ev["id"], reviewer="r")


class BridgeTransactionTests(unittest.TestCase):
    def _reqs(self, store, n=3):
        return [
            store.create(
                "user",
                "persona",
                "result",
                {"x": f"事务{i}", "result": f"结果{i}"},
                origin_id=f"orig-t{i}",
            )
            for i in range(n)
        ]

    def test_copy_failure_rolls_back_all(self):
        store = B.BridgeStore()
        reqs = self._reqs(store, 2)
        original = store._copy
        calls = {"n": 0}

        def flaky_copy(req):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("injected copy failure")
            return original(req)

        store._copy = flaky_copy
        with self.assertRaises(RuntimeError):
            store.approve_batch(
                [r["id"] for r in reqs], approver="测试用户A", confirmed_full_text=True
            )
        statuses = [store.get(r["id"])["status"] for r in reqs]
        self.assertEqual(statuses, ["pending", "pending"])
        self.assertEqual([store.get(r["id"])["approver"] for r in reqs], [None, None])

    def test_audit_failure_rolls_back_all(self):
        store = B.BridgeStore()
        reqs = self._reqs(store, 2)
        calls = {"n": 0}

        def flaky_audit(action, req_id, approver=None, note=""):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("injected audit failure")

        store._audit = flaky_audit
        with self.assertRaises(RuntimeError):
            store.approve_batch(
                [r["id"] for r in reqs], approver="测试用户A", confirmed_full_text=True
            )
        self.assertEqual([store.get(r["id"])["status"] for r in reqs], ["pending", "pending"])
        self.assertEqual(store.approved_copy(reqs[0]["id"]), None)

    def test_normal_batch_still_approves_all(self):
        store = B.BridgeStore()
        reqs = self._reqs(store, 3)
        copies = store.approve_batch(
            [r["id"] for r in reqs], approver="测试用户A", confirmed_full_text=True
        )
        self.assertEqual(len(copies), 3)
        self.assertEqual([store.get(r["id"])["status"] for r in reqs], ["approved"] * 3)


if __name__ == "__main__":
    unittest.main()
