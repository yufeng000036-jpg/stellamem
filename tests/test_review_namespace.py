"""Human classification restores topic recall without broadening default reads."""

import contextlib
import io
import json
import subprocess
import unittest
from pathlib import Path

from test_inbox import InboxTests
from claim_normalizer import claim_id, normalize_claim
from storage import Transaction, load_records, writer_lock
from store import FILES, INBOX


class NamespaceReviewTests(unittest.TestCase):
    setUp = InboxTests.setUp
    seed = InboxTests.seed
    inbox_records = InboxTests.inbox_records
    cli = InboxTests.cli

    def project(self, subject, scopes):
        adapter = (Path(__file__).resolve().parents[1] / "adapters/openclaw/index.js").as_uri()
        result = subprocess.run(
            [
                "node",
                "--input-type=module",
                "-e",
                "import {project} from " + json.dumps(adapter) + ";"
                "console.log(await project(process.argv[1], 'fictional habitat preference',"
                "JSON.parse(process.argv[2])));",
                str(self.mem),
                json.dumps({"requesterSubject": subject, "activeScopes": scopes}),
            ],
            capture_output=True,
            check=True,
            timeout=10,
        )
        return result.stdout.decode("utf-8").strip()

    def active(self):
        return [
            r
            for f in FILES
            if f != INBOX
            for r in load_records(self.mem / f)[1]
            if r.get("status") == "active"
        ]

    def test_topic_requires_explicit_namespace_and_scope_then_rolls_back(self):
        self.seed(
            kind="preference",
            subject="Fictional Habitat",
            scope="habitat",
            value="prefer fictional habitat gardens",
        )
        candidate = self.inbox_records()[0]
        self.assertEqual(self.project("user", ["habitat"]), "")
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = self.cli(["approve", candidate["revision_id"], "--namespace", "user"])
        self.assertEqual(code, 0)
        txid = json.loads(output.getvalue())["transaction"]
        record = self.active()[0]
        self.assertEqual(record["subject"], "Fictional Habitat")
        self.assertEqual(record["namespace"], "user")
        self.assertEqual(record["provenance"], candidate["provenance"])
        self.assertIn("gardens", self.project("user", ["habitat"]))
        for subject, scopes in [("user", []), ("persona", ["habitat"]), ("viewer", ["habitat"])]:
            self.assertEqual(self.project(subject, scopes), "")
        with writer_lock(self.mem):
            Transaction(self.mem).rollback(txid, yes=True)
        self.assertEqual(self.active(), [])
        self.assertEqual(self.inbox_records()[0], candidate)

    def test_untagged_topic_approval_remains_noninjectable(self):
        self.seed(
            kind="preference", subject="Fictional Habitat", value="prefer fictional habitat gardens"
        )
        self.assertEqual(self.cli(["approve", self.inbox_records()[0]["revision_id"]]), 0)
        self.assertEqual(self.project("user", ["global"]), "")

    def test_namespace_ids_separate_identical_topic_slots(self):
        raw = dict(
            kind="fact",
            subject="Fictional Habitat",
            scope="global",
            predicate="color",
            value="blue",
        )
        self.assertEqual(claim_id(raw), claim_id(dict(raw)))
        self.assertEqual(
            len({claim_id(dict(raw, namespace=s)) for s in ("user", "persona", "viewer")}), 3
        )

    def test_edit_namespace_remains_pending_and_audited(self):
        self.seed(
            kind="preference", subject="Fictional Habitat", value="prefer fictional habitat gardens"
        )
        old = self.inbox_records()[0]
        self.assertEqual(self.cli(["edit", old["revision_id"], "--namespace", "persona"]), 0)
        new = self.inbox_records()[0]
        self.assertEqual(new["status"], "pending")
        self.assertEqual(new["edit_history"][-1]["edited"], {"namespace": "persona"})
        self.assertEqual(self.project("persona", ["global"]), "")

    def test_model_namespace_is_not_authority(self):
        c = normalize_claim(
            dict(
                kind="fact",
                subject="Fictional Habitat",
                namespace="user",
                predicate="color",
                value="blue",
                evidence="blue",
            )
        )
        self.assertNotIn("namespace", c)

    def test_explicit_legacy_namespace_cannot_bypass_occupied_slot(self):
        raw = dict(kind="fact", subject="user", scope="global", predicate="color", value="blue")
        self.assertEqual(claim_id(raw), claim_id(dict(raw, namespace="user")))
        self.seed(kind="preference", subject="user", value="prefer fictional habitat gardens")
        self.assertEqual(self.cli(["approve", self.inbox_records()[0]["revision_id"]]), 0)
        self.seed(kind="preference", subject="user", value="prefer fictional habitat forests")
        self.assertEqual(
            self.cli(["approve", self.inbox_records()[0]["revision_id"], "--namespace", "user"]), 2
        )
        self.assertEqual(len(self.active()), 1)

    def test_classified_pending_update_suppresses_old_projection(self):
        self.seed(
            kind="preference", subject="Fictional Habitat", value="prefer fictional habitat gardens"
        )
        first = self.inbox_records()[0]
        self.assertEqual(self.cli(["approve", first["revision_id"], "--namespace", "user"]), 0)
        self.assertIn("gardens", self.project("user", ["global"]))
        self.seed(
            kind="preference", subject="Fictional Habitat", value="prefer fictional habitat forests"
        )
        candidate = self.inbox_records()[0]
        self.assertEqual(self.cli(["edit", candidate["revision_id"], "--namespace", "user"]), 0)
        self.assertEqual(self.inbox_records()[0]["relation"], "UNCERTAIN")
        self.assertEqual(self.project("user", ["global"]), "")
        self.assertEqual(len(self.active()), 1)


del InboxTests

if __name__ == "__main__":
    unittest.main()
