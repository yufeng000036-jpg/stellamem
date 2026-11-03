"""Synthetic safety regressions discovered during stage C review."""

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))
import nightly
import pending_batch
import persona_sync
import subjects
from bridge import BridgeStore
from datetime import datetime, timedelta, timezone
from unittest.mock import patch


class GovernanceBoundaryTests(unittest.TestCase):
    def test_present_null_namespace_does_not_fall_back(self):
        self.assertIsNone(subjects.subject_of({"namespace": None, "subject": "user"}))

    def test_malformed_namespace_fails_closed(self):
        for value in ([], {}, True, 17):
            self.assertIsNone(subjects.subject_of({"namespace": value, "subject": "user"}))
            self.assertFalse(subjects.can_retrieve({"subject": "user"}, value))

    def test_duplicate_pending_record_ids_are_ambiguous(self):
        records = [
            {"claim_id": "x", "status": "pending"},
            {"claim_id": "x", "status": "pending", "namespace": "persona"},
        ]
        with self.assertRaises(ValueError):
            pending_batch.plan_batch(records, {"x": "keep"})

    def test_sync_rejects_conflicting_namespace(self):
        event = dict(
            event_id="e",
            subject="persona",
            namespace="user",
            status="reviewed",
            reviewed_by="simulated-reviewer",
            reviewed_at="2026-01-01",
            text="fictional",
        )
        with self.assertRaises(ValueError):
            persona_sync.prepare_sync_pack([event], "simulated-reviewer", True)

    def test_nightly_preserves_conflict_without_winner(self):
        events = [
            dict(
                event_id="a",
                subject="user",
                scope="global",
                predicate="city",
                value="fictional A",
                importance=9,
            ),
            dict(
                event_id="b",
                subject="user",
                scope="global",
                predicate="city",
                value="fictional B",
                importance=1,
            ),
        ]
        before = copy.deepcopy(events)
        result = nightly.consolidate(events)
        self.assertEqual(result["kept"], [])
        self.assertEqual(len(result["conflicts"]), 1)
        self.assertEqual(result["source_events"], events)
        self.assertEqual(events, before)
        result["source_events"][0]["value"] = "changed copy"
        self.assertEqual(events, before)

    def test_nightly_rejects_nonfinite_score(self):
        for value in (float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                nightly.consolidate([dict(event_id="a", importance=value)])


class BridgeStateTests(unittest.TestCase):
    def test_returned_fields_do_not_mutate_approval_content(self):
        store = BridgeStore()
        request = store.create(
            "user", "persona", "result", {"x": "fictional", "result": "original"}
        )
        request["fields"]["result"] = "unreviewed"
        self.assertEqual(store.get(request["id"])["fields"]["result"], "original")

    def test_approved_copy_expires_at_boundary(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        clock = [now]
        store = BridgeStore(now_fn=lambda: clock[0])
        request = store.create(
            "user", "persona", "result", {"x": "fictional", "result": "original"}
        )
        store.approve(request["id"], "simulated-human", True)
        clock[0] = now + timedelta(hours=72)
        self.assertIsNone(store.approved_copy(request["id"]))

    def test_audit_failure_cannot_approve(self):
        store = BridgeStore()
        request = store.create(
            "user", "persona", "result", {"x": "fictional", "result": "original"}
        )
        with patch.object(store, "_audit", side_effect=OSError("synthetic disk failure")):
            with self.assertRaises(OSError):
                store.approve(request["id"], "simulated-human", True)
        self.assertEqual(store.get(request["id"])["status"], "pending")
