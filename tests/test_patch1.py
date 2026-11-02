"""PATCH1 tests: gate partial-signal coverage + bridge batch atomicity.

Synthetic placeholder data only. These tests target the two boundaries the
independent audit flagged as "declaration stronger than code" (D2, D6).
"""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

import bridge as B
import gate as G


class GatePartialSignalTests(unittest.TestCase):
    def test_overlap_only_zero_is_low_signal(self):
        # D2: one field present, the other missing, used to bypass the rule.
        self.assertTrue(G.is_low_signal({"text": "x", "overlap": 0}))

    def test_semantic_only_low_is_low_signal(self):
        self.assertTrue(G.is_low_signal({"text": "x", "semantic": 0.2}))

    def test_semantic_only_high_is_strong(self):
        self.assertFalse(G.is_low_signal({"text": "x", "semantic": 0.9}))

    def test_overlap_only_high_is_strong(self):
        self.assertFalse(G.is_low_signal({"text": "x", "overlap": 0.9}))

    def test_numeric_strings_are_read(self):
        self.assertTrue(G.is_low_signal({"text": "x", "overlap": "0", "semantic": "0.2"}))
        self.assertFalse(G.is_low_signal({"text": "x", "overlap": "0.9", "semantic": "0.8"}))
        self.assertTrue(G.is_low_signal({"text": "x", "score": "0.05"}))

    def test_booleans_and_garbage_are_not_numbers(self):
        # bool is not a signal; a garbage string is not a signal either.
        self.assertFalse(G.is_low_signal({"text": "x", "score": True}))
        self.assertFalse(G.is_low_signal({"text": "x", "overlap": "n/a", "semantic": "n/a"}))
        # ...but a score present as garbage must not crash the gate.
        self.assertEqual(G.InjectionGate().decide({"items": [{"text": "x", "score": "n/a"}]})["injected"], 1)

    def test_fieldless_item_still_not_judged(self):
        # Boundary kept: text-only items are not invented into low signal.
        self.assertFalse(G.is_low_signal({"text": "only text"}))

    def test_weak_flag_still_trusted(self):
        self.assertTrue(G.is_low_signal({"text": "x", "weak": True}))

    def test_decide_blocks_partial_signal(self):
        g = G.InjectionGate()
        out = g.decide({"items": [
            {"text": "测试强信号", "overlap": 0.9, "semantic": 0.8},
            {"text": "测试部分字段", "overlap": 0},
        ]}, query="测试查询")
        self.assertEqual(out["decision"], "inject_partial")
        self.assertEqual([i["text"] for i in out["inject"]], ["测试强信号"])
        self.assertEqual(out["blocked_low_score"], 1)


class BridgeBatchAtomicityTests(unittest.TestCase):
    def _req(self, store, tag="a"):
        return store.create("user", "persona", "result",
                            {"x": f"测试{tag}", "result": f"结果{tag}"},
                            origin_id=f"orig-{tag}")

    def test_duplicate_ids_abort_before_any_change(self):
        store = B.BridgeStore()
        req = self._req(store)
        with self.assertRaises(ValueError):
            store.approve_batch([req["id"], req["id"]],
                                approver="测试用户A", confirmed_full_text=True)
        self.assertEqual(store.get(req["id"])["status"], "pending")
        self.assertIsNone(store.approved_copy(req["id"]))

    def test_duplicate_in_mixed_batch_changes_nothing(self):
        store = B.BridgeStore()
        a, b = self._req(store, "a"), self._req(store, "b")
        with self.assertRaises(ValueError):
            store.approve_batch([a["id"], b["id"], a["id"]],
                                approver="测试用户A", confirmed_full_text=True)
        self.assertEqual([store.get(r["id"])["status"] for r in (a, b)], ["pending", "pending"])

    def test_clock_jump_inside_phase1_aborts_all(self):
        t0 = datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc)
        created = t0 - timedelta(hours=71)
        calls = {"n": 0}

        def now_fn():
            calls["n"] += 1
            # Calls 1-2 are the create() audit timestamps; call 3 is the
            # first validation and already jumped past the 72h TTL.
            return t0 if calls["n"] <= 2 else t0 + timedelta(days=3)

        store = B.BridgeStore(now_fn=now_fn)
        reqs = [store.create("user", "persona", "result",
                             {"x": f"时钟{i}", "result": f"结果{i}"},
                             origin_id=f"orig-c{i}", created_at=created) for i in range(2)]
        with self.assertRaises(ValueError):
            store.approve_batch([r["id"] for r in reqs],
                                approver="测试用户A", confirmed_full_text=True)
        statuses = [store.get(r["id"])["status"] for r in reqs]
        self.assertNotIn("approved", statuses, statuses)

    def test_expired_item_aborts_whole_batch(self):
        store = B.BridgeStore()
        good = self._req(store, "good")
        past = datetime.now(timezone.utc) - timedelta(hours=73)
        stale = store.create("user", "persona", "result",
                             {"x": "过期项", "result": "占位"},
                             origin_id="orig-stale", created_at=past)
        with self.assertRaises(ValueError):
            store.approve_batch([good["id"], stale["id"]],
                                approver="测试用户A", confirmed_full_text=True)
        self.assertEqual(store.get(good["id"])["status"], "pending")
        self.assertEqual(store.get(stale["id"])["status"], "expired")
        self.assertIsNone(store.approved_copy(good["id"]))

    def test_batch_uses_one_clock_read(self):
        store = B.BridgeStore()
        reqs = [self._req(store, str(i)) for i in range(2)]
        copies = store.approve_batch([r["id"] for r in reqs],
                                     approver="测试用户A", confirmed_full_text=True)
        self.assertEqual(len(copies), 2)
        self.assertEqual(len({store.get(r["id"])["approved_at"] for r in reqs}), 1)

    def test_successful_batch_returns_copies(self):
        store = B.BridgeStore()
        reqs = [self._req(store, str(i)) for i in range(2)]
        copies = store.approve_batch([r["id"] for r in reqs],
                                     approver="测试用户A", confirmed_full_text=True)
        self.assertEqual([c["subject"] for c in copies], ["persona", "persona"])
        self.assertEqual(len({c["approver"] for c in copies}), 1)

    def test_single_approve_unaffected(self):
        store = B.BridgeStore()
        req = self._req(store)
        copy = store.approve(req["id"], approver="测试用户A", confirmed_full_text=True)
        self.assertEqual(copy["subject"], "persona")
        self.assertEqual(store.get(req["id"])["status"], "approved")


if __name__ == "__main__":
    unittest.main()
