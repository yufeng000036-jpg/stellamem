"""Bridge bidirectional access audit lines (Hermes 4.1).

Every bridge access leaves a line: who / which subject / read or write /
hit or reject. Synthetic placeholder data only.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

import bridge as B


def _create(store, **kw):
    return store.create("user", "persona", "result", {"x": "fictional", "result": "fixture"}, **kw)


class AccessLineTests(unittest.TestCase):
    def test_create_writes_write_hit_line(self):
        store = B.BridgeStore()
        req = _create(store)
        self.assertEqual(len(store.access_audit), 1)
        line = store.access_audit[0]
        self.assertEqual(line["actor"], "subject:user")
        self.assertEqual(line["direction"], "write")
        self.assertEqual(line["outcome"], "hit")
        self.assertEqual(line["request_id"], req["id"])
        self.assertEqual(line["source_subject"], "user")
        self.assertEqual(line["target_subject"], "persona")

    def test_create_with_explicit_actor(self):
        store = B.BridgeStore()
        _create(store, actor="operator-a")
        self.assertEqual(store.access_audit[0]["actor"], "operator-a")

    def test_rejected_create_writes_write_reject_line(self):
        store = B.BridgeStore()
        with self.assertRaises(ValueError):
            store.create("user", "persona", "no-such-template", {"x": "fictional"})
        self.assertEqual(store.requests, {})
        self.assertEqual(store.audit, [])
        self.assertEqual(len(store.access_audit), 1)
        line = store.access_audit[0]
        self.assertEqual(line["direction"], "write")
        self.assertEqual(line["outcome"], "reject")
        self.assertIsNone(line["request_id"])
        self.assertIn("no-such-template", line["note"])

    def test_rejected_create_access_line_goes_to_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bridge-access.jsonl"
            store = B.BridgeStore(access_audit_path=path)
            with self.assertRaises(ValueError):
                store.create("user", "user", "result", {"x": "fictional", "result": "fixture"})
            lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0]["outcome"], "reject")
            self.assertEqual(lines[0]["direction"], "write")

    def test_get_writes_read_hit_line(self):
        store = B.BridgeStore()
        req = _create(store)
        got = store.get(req["id"])
        self.assertEqual(got["id"], req["id"])
        line = store.access_audit[-1]
        self.assertEqual(line["direction"], "read")
        self.assertEqual(line["outcome"], "hit")
        self.assertEqual(line["request_id"], req["id"])
        self.assertEqual(line["source_subject"], "user")
        self.assertEqual(line["target_subject"], "persona")

    def test_get_unknown_id_writes_read_reject_line(self):
        store = B.BridgeStore()
        with self.assertRaises(KeyError):
            store.get("bridge-9999")
        line = store.access_audit[-1]
        self.assertEqual(line["direction"], "read")
        self.assertEqual(line["outcome"], "reject")
        self.assertEqual(line["request_id"], "bridge-9999")
        self.assertIsNone(line["source_subject"])

    def test_approved_copy_reject_then_hit(self):
        store = B.BridgeStore()
        req = _create(store)
        self.assertIsNone(store.approved_copy(req["id"]))
        line = store.access_audit[-1]
        self.assertEqual((line["direction"], line["outcome"]), ("read", "reject"))
        self.assertIn("pending", line["note"])
        store.approve(req["id"], "simulated-human", confirmed_full_text=True)
        copy_out = store.approved_copy(req["id"])
        self.assertIsNotNone(copy_out)
        line = store.access_audit[-1]
        self.assertEqual((line["direction"], line["outcome"]), ("read", "hit"))
        self.assertEqual(line["actor"], "subject:persona")

    def test_list_pending_writes_read_hit_line(self):
        store = B.BridgeStore()
        _create(store)
        pending = store.list_pending()
        self.assertEqual(len(pending), 1)
        line = store.access_audit[-1]
        self.assertEqual((line["direction"], line["outcome"]), ("read", "hit"))
        self.assertEqual(line["actor"], "reviewer")
        self.assertIn("pending=1", line["note"])

    def test_access_lines_go_to_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bridge-access.jsonl"
            store = B.BridgeStore(access_audit_path=path)
            req = _create(store)
            store.get(req["id"])
            lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(lines), 2)
            self.assertEqual([x["direction"] for x in lines], ["write", "read"])
            self.assertEqual(lines, store.access_audit)

    def test_reads_do_not_touch_the_lifecycle_audit(self):
        store = B.BridgeStore()
        req = _create(store)
        self.assertEqual(len(store.audit), 1)  # created
        store.get(req["id"])
        store.list_pending()
        store.approved_copy(req["id"])  # reject: not approved yet
        self.assertEqual(len(store.audit), 1)

    def test_approve_decision_stays_in_lifecycle_audit_only(self):
        store = B.BridgeStore()
        req = _create(store)
        store.approve(req["id"], "simulated-human", confirmed_full_text=True)
        # One access line (the create); the approval itself is a decision,
        # recorded in the lifecycle audit, not as an access.
        self.assertEqual(len(store.access_audit), 1)
        self.assertEqual(len(store.audit), 2)
        self.assertEqual([e["action"] for e in store.audit], ["created", "approved"])

    def test_access_log_returns_recent_lines(self):
        store = B.BridgeStore()
        _create(store)
        store.list_pending()
        recent = store.access_log(n=1)
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["direction"], "read")
        # The returned lines are copies: mutating them changes nothing.
        recent[0]["outcome"] = "tampered"
        self.assertEqual(store.access_audit[-1]["outcome"], "hit")


if __name__ == "__main__":
    unittest.main()
