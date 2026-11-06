"""Pending auto-approval: clean user claims auto-activate after a grace period.

Covers the time-based second chance for the pending inbox (default 72h):
grace boundaries, every exclusion (sensitive / identity / user-core /
unverified / contradictory / secret / protected target / occupied slot /
non-user subject), backlog exclusion (no pending_since stays manual),
disabled config, dry-run, idempotency, audit marking, withdraw round-trip,
and config defaults + invalid-value rejection. Nothing here touches real user
memory; the config is redirected via STELLAMEM_CONFIG.
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import inbox
from claim_normalizer import ROUTE
from config_loader import load_config
from storage import load_records, render_records
from store import INBOX, MemoryStore
from test_memory_safety import verified
from test_policy import claim as raw_claim


def _now():
    return datetime.now(timezone.utc)


class AutoApproveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config_path = self.root / "config.json"
        self.mem = self.root / "memory"
        self.mem.mkdir()
        self.write_config()
        self.env = patch.dict("os.environ", {"STELLAMEM_CONFIG": str(self.config_path)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.tmp.cleanup)

    def write_config(self, **overrides):
        config = {
            "memories_root": str(self.mem),
            "server_url": "http://127.0.0.1:8081",
        }
        config.update(overrides)
        self.config_path.write_text(json.dumps(config), encoding="utf-8")

    def seed(self, kind="decision", **kwargs):
        # Same shape as test_inbox: low confidence forces pending without
        # tripping any policy exclusion (verified evidence, user source).
        if kind == "decision":
            kwargs.setdefault("value", "决定改用本地模型，因为云端太慢")
        kwargs.setdefault("confidence", 0.5)
        store = MemoryStore(self.mem)
        c = verified(kind=kind, **kwargs)
        store.ingest(c)
        store.commit()
        return c

    def seed_raw(self, **kwargs):
        # Unverified claim (evidence_verified unset) via test_policy.claim.
        kwargs.setdefault("kind", "decision")
        kwargs.setdefault("value", "决定改用本地模型，因为云端太慢")
        kwargs.setdefault("confidence", 0.5)
        store = MemoryStore(self.mem)
        c = raw_claim(**kwargs)
        store.ingest(c)
        store.commit()
        return c

    def write_target(self, kind):
        return self.mem / ROUTE[kind]

    def inbox_records(self):
        return load_records(self.mem / INBOX)[1]

    def pending_records(self):
        return [r for r in self.inbox_records() if r["status"] == "pending"]

    def cli(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = inbox.main(argv)
        return code, buf.getvalue()

    def cli_json(self, argv):
        code, out = self.cli(argv)
        return code, json.loads(out)

    def set_pending_field(self, cid, field, value):
        path = self.mem / INBOX
        prefix, records = load_records(path)
        for r in records:
            if r.get("claim_id") == cid:
                if value is None:
                    r.pop(field, None)
                else:
                    r[field] = value
        path.write_text(render_records(prefix, records), encoding="utf-8")

    def age_pending(self, cid, hours):
        stamp = (_now() - timedelta(hours=hours)).isoformat()
        self.set_pending_field(cid, "pending_since", stamp)

    def chain_events(self):
        chain_path = self.mem / "system/version-chain.json"
        if not chain_path.exists():
            return []
        return json.loads(chain_path.read_text(encoding="utf-8"))

    # -- stamping ----------------------------------------------------------

    def test_ingest_stamps_pending_since_on_pending_claims(self):
        self.seed()
        record = self.pending_records()[0]
        since = inbox._parse_pending_since(record.get("pending_since"))
        self.assertIsNotNone(since)
        self.assertLessEqual(_now() - since, timedelta(minutes=5))

    def test_ingest_does_not_stamp_auto_written_claims(self):
        store = MemoryStore(self.mem)
        c = verified(
            kind="decision",
            value="决定改用本地模型，因为云端太慢",
            confidence=0.99,
        )
        store.ingest(c)
        store.commit()
        _, records = load_records(self.write_target("decision"))
        self.assertEqual(len(records), 1)
        self.assertNotIn("pending_since", records[0])

    # -- grace boundaries ---------------------------------------------------

    def test_grace_just_before_72h_stays_pending(self):
        self.seed()
        cid = self.pending_records()[0]["claim_id"]
        self.age_pending(cid, 71 + 59 / 60)
        code, result = self.cli_json(["auto-approve"])
        self.assertEqual(code, 0)
        self.assertEqual(result["approved"], [])
        self.assertEqual([r["status"] for r in self.pending_records()], ["pending"])

    def test_grace_just_after_72h_auto_approves(self):
        self.seed()
        cid = self.pending_records()[0]["claim_id"]
        self.age_pending(cid, 72 + 1 / 60)
        code, result = self.cli_json(["auto-approve"])
        self.assertEqual(code, 0)
        self.assertEqual(result["approved"], [cid])
        self.assertEqual(self.pending_records(), [])
        _, records = load_records(self.write_target("decision"))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["status"], "active")
        self.assertEqual(records[0]["approved_via"], "auto")

    def test_custom_grace_hours_are_honoured(self):
        self.write_config(pending_auto_approve_hours=1)
        self.seed()
        cid = self.pending_records()[0]["claim_id"]
        self.age_pending(cid, 0.5)
        _, result = self.cli_json(["auto-approve"])
        self.assertEqual(result["approved"], [])
        self.age_pending(cid, 2)
        _, result = self.cli_json(["auto-approve"])
        self.assertEqual(result["approved"], [cid])

    # -- exclusions ---------------------------------------------------------

    def test_exclusions_stay_pending_after_grace(self):
        cases = {}

        def seed_case(label, seeder, **kwargs):
            # Distinct slot per case: same claim_id/revision_id twice in the
            # inbox trips the append-only guard, so each case gets its own
            # predicate and value (decision markers kept so the kind stays).
            # NOTE: normalize_claim drops `namespace`, so a namespace-scoped
            # record (as the API layer writes it) is simulated by setting
            # the field on the stored record afterwards.
            namespace = kwargs.pop("namespace", None)
            kwargs.setdefault("predicate", "pred_" + label)
            kwargs.setdefault("value", "决定改用本地模型，因为云端太慢（" + label + "）")
            seeder(**kwargs)
            cid = self.pending_records()[-1]["claim_id"]
            if namespace is not None:
                self.set_pending_field(cid, "namespace", namespace)
            cases[label] = cid

        seed_case("sensitive", self.seed, sensitive=True)
        seed_case("identity", self.seed, kind="identity")
        seed_case("user-core", self.seed, kind="user-core")
        seed_case("unverified", self.seed_raw)  # unverified evidence
        seed_case("assistant-source", self.seed, source="assistant")
        seed_case("missing-evidence", self.seed, evidence="")
        seed_case("persona", self.seed, namespace="persona")
        seed_case("viewer", self.seed, namespace="viewer")
        seed_case("unclassified", self.seed, subject="张三")

        for cid in cases.values():
            self.age_pending(cid, 100)
        code, result = self.cli_json(["auto-approve"])
        self.assertEqual(code, 0)
        self.assertEqual(result["approved"], [])
        self.assertEqual({r["claim_id"] for r in self.pending_records()}, set(cases.values()))
        reasons = {s["id"]: s["reason"] for s in result["skipped"]}
        for name, cid in cases.items():
            self.assertIn(cid, reasons, name)

    def test_non_new_relations_stay_pending(self):
        self.seed()
        cid = self.pending_records()[0]["claim_id"]
        for relation in ("CONTRADICTS", "UNCERTAIN", "COMPATIBLE", "DUPLICATE", "REINFORCES"):
            self.set_pending_field(cid, "relation", relation)
            self.age_pending(cid, 100)
            _, result = self.cli_json(["auto-approve"])
            self.assertEqual(result["approved"], [], relation)
            self.assertEqual([r["status"] for r in self.pending_records()], ["pending"], relation)

    def test_secret_bearing_record_stays_pending(self):
        self.seed()
        cid = self.pending_records()[0]["claim_id"]
        self.set_pending_field(cid, "value", "sk-" + "Z" * 32)
        self.age_pending(cid, 100)
        _, result = self.cli_json(["auto-approve"])
        self.assertEqual(result["approved"], [])
        self.assertEqual([r["status"] for r in self.pending_records()], ["pending"])

    def test_occupied_slot_blocks_second_identical_pending(self):
        # Two pending rows in the same slot, different values: same claim_id,
        # different revision_id. The first approval occupies the slot; the
        # second must stay pending instead of creating a second active value.
        self.seed(value="决定改用本地模型，因为云端太慢")
        self.seed(value="决定改用本地模型，因为云端更稳")
        self.assertEqual(len(self.pending_records()), 2)
        self.assertEqual(
            self.pending_records()[0]["claim_id"], self.pending_records()[1]["claim_id"]
        )
        self.assertNotEqual(
            self.pending_records()[0]["revision_id"],
            self.pending_records()[1]["revision_id"],
        )
        for r in self.pending_records():
            self.age_pending(r["claim_id"], 100)
        for r in self.pending_records():
            self.age_pending(r["claim_id"], 100)
        _, result = self.cli_json(["auto-approve"])
        self.assertEqual(len(result["approved"]), 1)
        self.assertEqual(len(result["skipped"]), 1)
        self.assertIn("occupies this slot", result["skipped"][0]["reason"])
        self.assertEqual(len(self.pending_records()), 1)
        # The survivor stays pending: approving it now would create a second
        # active value in the same slot, so even manual approval refuses it
        # until the conflict is resolved deliberately.
        survivor = self.pending_records()[0]
        self.assertEqual(self.cli(["approve", survivor["claim_id"]])[0], 2)
        self.assertEqual(len(self.pending_records()), 1)

    def test_duplicate_inbox_rows_stay_manual(self):
        # Two rows with the same (claim_id, revision_id) -- the shape real
        # production data has (20 groups, 41 rows). Approving one twin would
        # trip the append-only guard (the surviving twin changes the count),
        # so the sweep skips both with a reason instead of failing the batch.
        store = MemoryStore(self.mem)
        first = verified(
            kind="decision",
            value="决定改用本地模型，因为云端太慢",
            confidence=0.5,
        )
        store.ingest(first)
        second = verified(
            kind="decision",
            value="决定改用本地模型，因为云端太慢",
            confidence=0.5,
        )
        second["provenance"] = dict(second["provenance"], message_id="2")
        store.ingest(second)
        store.commit()
        self.assertEqual(len(self.pending_records()), 2)
        for r in self.pending_records():
            self.age_pending(r["claim_id"], 100)
        code, result = self.cli_json(["auto-approve"])
        self.assertEqual(code, 0)
        self.assertEqual(result["approved"], [])
        self.assertEqual(len(result["skipped"]), 2)
        self.assertTrue(all("duplicate inbox rows" in s["reason"] for s in result["skipped"]))
        self.assertEqual(len(self.pending_records()), 2)

    def test_occupied_slot_checked_directly(self):
        self.seed()
        cid = self.pending_records()[0]["claim_id"]
        self.assertEqual(self.cli(["approve", cid])[0], 0)
        store = MemoryStore(self.mem)
        record = {
            "status": "pending",
            "pending_since": (_now() - timedelta(hours=100)).isoformat(),
            "subject": "user",
            "kind": "decision",
            "sensitivity": "PRIVATE",
            "source": "user",
            "evidence": "x",
            "evidence_verified": True,
            "relation": "NEW",
            "write_policy": "allow",
            "claim_id": cid,
        }
        eligible, reason = inbox.auto_approve_eligible(record, store, 72, _now())
        self.assertFalse(eligible)
        self.assertIn("occupies this slot", reason)

    # -- backlog ------------------------------------------------------------

    def test_backlog_without_pending_since_never_auto_approves(self):
        self.seed()
        cid = self.pending_records()[0]["claim_id"]
        self.set_pending_field(cid, "pending_since", None)
        _, result = self.cli_json(["auto-approve"])
        self.assertEqual(result["approved"], [])
        self.assertEqual([r["status"] for r in self.pending_records()], ["pending"])

    def test_edit_does_not_stamp_backlog_record(self):
        self.seed()
        cid = self.pending_records()[0]["claim_id"]
        self.set_pending_field(cid, "pending_since", None)
        self.assertEqual(self.cli(["edit", cid, "--value", "决定改用本地模型，因为云端更稳"])[0], 0)
        pending = self.pending_records()[0]
        self.assertNotIn("pending_since", pending)
        _, result = self.cli_json(["auto-approve"])
        self.assertEqual(result["approved"], [])

    def test_edit_preserves_pending_since(self):
        self.seed()
        cid = self.pending_records()[0]["claim_id"]
        stamp = self.pending_records()[0]["pending_since"]
        self.assertEqual(self.cli(["edit", cid, "--value", "决定改用本地模型，因为云端更稳"])[0], 0)
        self.assertEqual(self.pending_records()[0]["pending_since"], stamp)

    # -- config / dry-run / idempotency --------------------------------------

    def test_disabled_config_is_a_noop(self):
        self.write_config(pending_auto_approve_enabled=False)
        self.seed()
        cid = self.pending_records()[0]["claim_id"]
        self.age_pending(cid, 100)
        code, result = self.cli_json(["auto-approve"])
        self.assertEqual(code, 0)
        self.assertFalse(result["enabled"])
        self.assertEqual(result["approved"], [])
        self.assertIsNone(result["transaction"])
        self.assertEqual([r["status"] for r in self.pending_records()], ["pending"])

    def test_dry_run_changes_nothing(self):
        self.seed()
        cid = self.pending_records()[0]["claim_id"]
        self.age_pending(cid, 100)
        before = self.chain_events()
        code, result = self.cli_json(["auto-approve", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["approved"], [cid])
        self.assertIsNone(result["transaction"])
        self.assertEqual([r["status"] for r in self.pending_records()], ["pending"])
        self.assertFalse(self.write_target("decision").exists())
        self.assertEqual(self.chain_events(), before)

    def test_second_sweep_is_a_noop(self):
        self.seed()
        cid = self.pending_records()[0]["claim_id"]
        self.age_pending(cid, 100)
        _, first = self.cli_json(["auto-approve"])
        self.assertEqual(first["approved"], [cid])
        events_after_first = self.chain_events()
        _, second = self.cli_json(["auto-approve"])
        self.assertEqual(second["approved"], [])
        self.assertEqual(second["checked"], 0)
        self.assertIsNone(second["transaction"])
        self.assertEqual(self.chain_events(), events_after_first)

    # -- audit + withdraw -----------------------------------------------------

    def test_auto_approval_is_audited_and_marked_auto(self):
        self.seed()
        cid = self.pending_records()[0]["claim_id"]
        self.age_pending(cid, 100)
        stamp = self.pending_records()[0]["pending_since"]
        self.assertEqual(self.cli(["auto-approve"])[0], 0)
        events = [e for e in self.chain_events() if e.get("review_action") == "auto-approve"]
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["approved_via"], "auto")
        self.assertEqual(event["rule"], "pending_auto_approve_hours=72")
        self.assertEqual(event["pending_since"], stamp)
        log = (self.mem / "system/review-log.md").read_text(encoding="utf-8")
        self.assertIn("auto-approve", log)
        _, records = load_records(self.write_target("decision"))
        self.assertEqual(records[0]["approved_via"], "auto")
        self.assertTrue(records[0]["auto_approved_at"])

    def test_withdraw_round_trip(self):
        self.seed()
        cid = self.pending_records()[0]["claim_id"]
        self.age_pending(cid, 100)
        self.assertEqual(self.cli(["auto-approve"])[0], 0)
        code, result = self.cli_json(["withdraw", cid])
        self.assertEqual(code, 0)
        self.assertTrue(result["back_to_pending"])
        # Back in the inbox, gone from the route file, clock cleared.
        pending = self.pending_records()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["claim_id"], cid)
        self.assertNotIn("pending_since", pending[0])
        self.assertNotIn("approved_via", pending[0])
        _, records = load_records(self.write_target("decision"))
        self.assertEqual(records, [])
        events = [e for e in self.chain_events() if e.get("review_action") == "withdraw"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["relation"], "WITHDRAWN")
        # A withdrawn record never auto-approves again: the veto sticks.
        _, result = self.cli_json(["auto-approve"])
        self.assertEqual(result["approved"], [])
        self.assertEqual([r["status"] for r in self.pending_records()], ["pending"])
        # ...but a human can still approve it afterwards.
        self.assertEqual(self.cli(["approve", cid])[0], 0)
        _, records = load_records(self.write_target("decision"))
        self.assertEqual(len(records), 1)
        self.assertNotEqual(records[0].get("approved_via"), "auto")

    def test_withdraw_refuses_manually_approved_record(self):
        self.seed()
        cid = self.pending_records()[0]["claim_id"]
        self.assertEqual(self.cli(["approve", cid])[0], 0)
        code, _ = self.cli(["withdraw", cid])
        self.assertEqual(code, 2)
        _, records = load_records(self.write_target("decision"))
        self.assertEqual(len(records), 1)

    def test_withdraw_unknown_id_is_nonzero(self):
        self.assertEqual(self.cli(["withdraw", "c-does-not-exist"])[0], 2)

    # -- manual review still works -------------------------------------------

    def test_manual_approve_still_works_inside_grace_period(self):
        self.seed()
        cid = self.pending_records()[0]["claim_id"]
        self.assertEqual(self.cli(["approve", cid])[0], 0)
        _, records = load_records(self.write_target("decision"))
        self.assertEqual(records[0]["status"], "active")
        self.assertNotIn("approved_via", records[0])

    def test_manual_reject_still_works_inside_grace_period(self):
        self.seed()
        cid = self.pending_records()[0]["claim_id"]
        self.assertEqual(self.cli(["reject", cid])[0], 0)
        self.assertEqual([r["status"] for r in self.inbox_records()], ["rejected"])
        _, result = self.cli_json(["auto-approve"])
        self.assertEqual(result["approved"], [])

    # -- list display ---------------------------------------------------------

    def test_list_shows_pending_since_and_eligibility(self):
        self.seed()
        cid = self.pending_records()[0]["claim_id"]
        code, out = self.cli(["list"])
        self.assertEqual(code, 0)
        self.assertIn("pending_since=", out)
        self.assertIn("auto_approve=in", out)
        self.age_pending(cid, 100)
        code, out = self.cli(["list"])
        self.assertIn("auto_approve=eligible", out)
        code, result = self.cli_json(["list", "--json"])
        self.assertEqual(code, 0)
        item = result[0]
        self.assertTrue(item["auto_approve"]["eligible"])
        self.assertEqual(item["auto_approve"]["status"], "eligible")
        self.assertIsNotNone(item["auto_approve"]["eligible_at"])

    # -- config validation -----------------------------------------------------

    def test_config_defaults_are_auto_on_72h(self):
        # No auto-approval keys in config: defaults apply, nothing raises.
        config = load_config()
        self.assertEqual(inbox.auto_approve_settings(config), (True, 72))

    def test_config_invalid_values_rejected(self):
        base = {"memories_root": str(self.mem), "server_url": "http://127.0.0.1:8081"}
        bad = [
            {"pending_auto_approve_enabled": "yes"},
            {"pending_auto_approve_enabled": 1},
            {"pending_auto_approve_enabled": None},
            {"pending_auto_approve_hours": 0},
            {"pending_auto_approve_hours": 721},
            {"pending_auto_approve_hours": "72"},
            {"pending_auto_approve_hours": True},
            {"pending_auto_approve_hours": 72.5},
        ]
        for override in bad:
            with self.subTest(override=override):
                config = dict(base)
                config.update(override)
                self.config_path.write_text(json.dumps(config), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_config()

    def test_config_valid_values_accepted(self):
        base = {"memories_root": str(self.mem), "server_url": "http://127.0.0.1:8081"}
        for override in (
            {"pending_auto_approve_enabled": False},
            {"pending_auto_approve_hours": 1},
            {"pending_auto_approve_hours": 720},
            {"pending_auto_approve_enabled": True, "pending_auto_approve_hours": 72},
        ):
            with self.subTest(override=override):
                config = dict(base)
                config.update(override)
                self.config_path.write_text(json.dumps(config), encoding="utf-8")
                loaded = load_config()
                self.assertEqual(
                    inbox.auto_approve_settings(loaded),
                    (
                        override.get("pending_auto_approve_enabled", True),
                        override.get("pending_auto_approve_hours", 72),
                    ),
                )


if __name__ == "__main__":
    unittest.main()
