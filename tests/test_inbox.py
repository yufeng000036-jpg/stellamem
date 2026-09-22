"""P0-F: the minimal human approval loop must be auditable and safe.

Covers list / approve / reject / edit against a synthetic memory root. Nothing
here touches real user memory; the config is redirected via STELLAMEM_CONFIG.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import inbox
from storage import load_records
from store import INBOX, MemoryStore
from test_memory_safety import verified


class InboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config_path = self.root / "config.json"
        self.mem = self.root / "memory"
        self.mem.mkdir()
        self.config_path.write_text(
            json.dumps(
                {
                    "memories_root": str(self.mem),
                    "server_url": "http://127.0.0.1:8081",
                }
            ),
            encoding="utf-8",
        )
        self.env = patch.dict("os.environ", {"STELLAMEM_CONFIG": str(self.config_path)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.tmp.cleanup)

    def seed(self, kind="decision", **kwargs):
        # `kind` defaults to a non-protected kind so the approve path can write.
        # `evidence_verified` alone does not force pending; use a low confidence or
        # an explicit confirm flag where a pending candidate is needed.
        kwargs.setdefault("confidence", 0.5)
        store = MemoryStore(self.mem)
        c = verified(kind=kind, **kwargs)
        store.ingest(c)
        store.commit()
        return c

    def seed_protected(self):
        # Identity candidates always land pending and target a protected file.
        store = MemoryStore(self.mem)
        c = verified(kind="identity")
        store.ingest(c)
        store.commit()
        return c

    def write_target(self, kind):
        from claim_normalizer import ROUTE

        return self.mem / ROUTE[kind]

    def inbox_records(self):
        return load_records(self.mem / INBOX)[1]

    def cli(self, argv):
        return inbox.main(argv)

    def test_list_shows_pending_candidate(self):
        self.seed(kind="identity")
        self.assertEqual(self.cli(["list"]), 0)
        self.assertEqual(len(self.inbox_records()), 1)

    def test_reject_keeps_candidate_out_of_canonical_memory(self):
        self.seed()
        cid = self.inbox_records()[0]["claim_id"]
        self.assertEqual(self.cli(["reject", cid, "--reason", "not durable"]), 0)
        record = self.inbox_records()[0]
        self.assertEqual(record["status"], "rejected")
        # Evidence of the decision, but no canonical write.
        self.assertFalse(self.write_target("decision").exists())
        chain = json.loads((self.mem / "system/version-chain.json").read_text(encoding="utf-8"))
        self.assertTrue(any(e.get("relation") == "REJECTED" for e in chain))

    def test_approve_writes_canonical_memory_with_provenance(self):
        self.seed()
        cid = self.inbox_records()[0]["claim_id"]
        self.assertEqual(self.cli(["approve", cid]), 0)
        _, records = load_records(self.write_target("decision"))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["status"], "active")
        self.assertTrue(records[0]["provenance"])
        # Candidate is no longer pending in the inbox.
        self.assertEqual([r for r in self.inbox_records() if r["status"] == "pending"], [])

    def test_approve_refuses_protected_core_instead_of_silent_loss(self):
        # Identity targets core/user-core.md, which the transaction layer protects.
        # Approval must fail loudly rather than consume the candidate and write nothing.
        self.seed_protected()
        cid = self.inbox_records()[0]["claim_id"]
        self.assertEqual(self.cli(["approve", cid]), 2)
        # Candidate is untouched and still pending; no protected file was created.
        self.assertEqual([r["status"] for r in self.inbox_records()], ["pending"])
        self.assertFalse((self.mem / "core/user-core.md").exists())

    def test_approved_sensitive_still_requires_confirmation_for_injection(self):
        self.seed(sensitive=True)
        cid = self.inbox_records()[0]["claim_id"]
        self.assertEqual(self.cli(["approve", cid]), 0)
        _, records = load_records(self.write_target("decision"))
        self.assertEqual(records[0]["injection_policy"], "confirm")

    def test_edit_then_approve_records_history(self):
        self.seed()
        cid = self.inbox_records()[0]["claim_id"]
        self.assertEqual(self.cli(["edit", cid, "--value", "very concise"]), 0)
        pending = [r for r in self.inbox_records() if r["status"] == "pending"][0]
        self.assertEqual(pending["value"], "very concise")
        self.assertTrue(pending["edit_history"])
        self.assertEqual(pending["edit_history"][0]["original"]["value"], "concise")
        # Still pending until approved.
        self.assertFalse(self.write_target("decision").exists())
        self.assertEqual(self.cli(["approve", pending["claim_id"]]), 0)
        _, records = load_records(self.write_target("decision"))
        self.assertEqual(records[0]["value"], "very concise")

    def test_approve_with_inline_edit(self):
        self.seed()
        cid = self.inbox_records()[0]["claim_id"]
        self.assertEqual(self.cli(["approve", cid, "--value", "detailed"]), 0)
        _, records = load_records(self.write_target("decision"))
        self.assertEqual(records[0]["value"], "detailed")

    def test_approve_refuses_secret(self):
        self.seed()
        cid = self.inbox_records()[0]["claim_id"]
        secret = "sk-" + "Z" * 32
        self.assertEqual(self.cli(["approve", cid, "--value", secret]), 2)
        self.assertFalse(self.write_target("decision").exists())

    def test_unknown_id_is_nonzero(self):
        self.seed()
        self.assertEqual(self.cli(["approve", "c-does-not-exist"]), 2)
        self.assertEqual(self.cli(["reject", "r-nope"]), 2)

    def test_show_prints_full_record(self):
        self.seed()
        cid = self.inbox_records()[0]["claim_id"]
        self.assertEqual(self.cli(["show", cid]), 0)

    def test_operations_are_audited_in_review_log(self):
        self.seed()
        cid = self.inbox_records()[0]["claim_id"]
        self.cli(["reject", cid])
        log = (self.mem / "system/review-log.md").read_text(encoding="utf-8")
        self.assertIn("reject", log)

    def test_list_json_is_machine_readable(self):
        self.seed()
        self.assertEqual(self.cli(["list", "--json"]), 0)

    def test_approval_is_audited_in_version_chain_and_review_log(self):
        self.seed()
        cid = self.inbox_records()[0]["claim_id"]
        self.assertEqual(self.cli(["approve", cid]), 0)
        chain = json.loads((self.mem / "system/version-chain.json").read_text(encoding="utf-8"))
        self.assertTrue(any(e.get("review_action") == "approve" for e in chain))
        log = (self.mem / "system/review-log.md").read_text(encoding="utf-8")
        self.assertIn("approve", log)

    def test_approve_populates_claim_and_revision_ids(self):
        self.seed()
        cid = self.inbox_records()[0]["claim_id"]
        self.assertEqual(self.cli(["approve", cid]), 0)
        _, records = load_records(self.write_target("decision"))
        self.assertTrue(records[0]["claim_id"].startswith("c-"))
        self.assertTrue(records[0]["revision_id"].startswith("r-"))


if __name__ == "__main__":
    unittest.main()
