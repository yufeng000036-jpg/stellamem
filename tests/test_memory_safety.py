"""Permanent integration regressions; fixtures contain no real user data."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))
from claim_normalizer import ROUTE, THRESHOLDS, claim_key, claim_slot
from pipeline import compile_messages
from source_reader import chunk_messages, parse_messages
from sleep_purify import run
from storage import Transaction, load_records, safe_path, writer_lock
from store import MemoryStore
from test_policy import claim


def echo_llm(system, payload):
    messages = json.loads(payload)
    if isinstance(messages, dict):
        return '[{"relation":"CONTRADICTS","confidence":0.99}]'
    return json.dumps(
        [
            dict(
                kind="preference",
                subject="user",
                scope="global",
                predicate="topic_" + str(m["text"].strip()),
                value=m["text"].strip(),
                evidence=m["text"].strip(),
                source=m["speaker"],
                message_index=i,
                confidence=0.99,
            )
            for i, m in enumerate(messages)
        ]
    )


def verified(**kwargs):
    c = claim(**kwargs)
    c["evidence_verified"] = True
    c["provenance"] = {
        "provider": "test",
        "precision": "message",
        "speaker": "user",
        "timestamp": "2026-01-01T15:32:01+00:00",
        "message_id": "1",
    }
    return c


class MemorySafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "daily").mkdir()
        self.config = {"memories_root": str(self.root), "chunk_chars": 256}

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text):
        (self.root / "daily/2026-01-01.md").write_text(text, encoding="utf-8")

    def test_all_types_have_threshold(self):
        self.assertEqual(set(ROUTE), set(THRESHOLDS))

    def test_slot_scope_and_revision(self):
        self.assertNotEqual(claim_slot(claim()), claim_slot(claim(scope="work")))
        self.assertNotEqual(claim_key(claim()), claim_key(claim(value="detailed")))

    def test_long_conversations_10_100_500_include_middle_and_tail(self):
        for count in (10, 100, 500):
            messages = parse_messages(
                "".join("[15:32:01] user: important topic " + str(i) + "\n" for i in range(count)),
                "2026-01-01.md",
            )
            claims = [
                c
                for result in compile_messages(messages, self.config, echo_llm)
                for c in result["claims"]
            ]
            values = {c["value"] for c in claims}
            self.assertEqual(len(values), count)
            self.assertIn("important topic " + str(count // 2), values)
            self.assertIn("important topic " + str(count - 1), values)

    def test_huge_single_message_no_character_loss(self):
        for text in ("sentence one. " * 1000 + "IMPORTANT TAIL", "x" * 15000):
            msgs = parse_messages(text, "2026-01-01.md")
            chunks = list(chunk_messages(msgs, 256))
            self.assertEqual("".join(m["text"] for chunk in chunks for m in chunk), text)
            self.assertTrue(all(sum(len(m["text"]) for m in c) <= 256 for c in chunks))

    def test_failure_does_not_advance_or_write_partial_memory(self):
        self.write(
            "".join("[15:32:01] user: " + str(i) + " unique " + "z" * 80 + "\n" for i in range(10))
        )
        calls = 0

        def failing(system, payload):
            nonlocal calls
            calls += 1
            if calls == 2:
                return '[{"value":"incomplete"'
            return echo_llm(system, payload)

        result = run(self.config, llm=failing)
        self.assertFalse(result["ok"])
        self.assertEqual(result["failed_chunk"], 1)
        self.assertFalse((self.root / "system/extraction-cursor.json").exists())
        self.assertFalse((self.root / "core/preferences.md").exists())
        result = run(self.config, llm=echo_llm)
        self.assertTrue(result["ok"])
        self.assertEqual(result["active"], 10)
        self.assertEqual(run(self.config, llm=echo_llm)["sources"], 0)

    def test_same_day_appends_are_processed(self):
        self.write("[15:32:01] user: one\n")
        self.assertEqual(run(self.config, llm=echo_llm)["active"], 1)
        self.write("[15:32:01] user: one\n[15:32:20] user: two\n[15:32:59] user: three\n")
        self.assertEqual(run(self.config, llm=echo_llm)["active"], 2)

    def test_secret_never_reaches_model_or_any_new_file(self):
        secret = "sk-" + "X" * 32
        self.write("[15:32:01] user: " + secret + "\n[15:32:20] user: concise\n")

        def model(system, payload):
            self.assertNotIn(secret, payload)
            return echo_llm(system, payload)

        result = run(self.config, llm=model)
        self.assertTrue(result["ok"])
        self.assertEqual(result["blocked"], 1)
        for path in self.root.rglob("*"):
            if path.is_file() and "daily" not in path.parts:
                self.assertNotIn(secret, path.read_text(encoding="utf-8"))

    def test_assistant_and_quoted_statements_cannot_be_laundered(self):
        for role, body in (
            ("assistant", "keep replies concise"),
            ("user", "he said: keep replies concise"),
        ):
            self.write("[15:32:01] " + role + ": " + body + "\n")
            self.assertEqual(run(self.config, llm=echo_llm)["active"], 0)

    def test_sensitive_and_identity_even_verified_go_pending(self):
        store = MemoryStore(self.root)
        for c in (
            verified(kind="identity"),
            verified(sensitive=True),
            verified(value="I feel lonely"),
        ):
            store.ingest(c)
        store.commit()
        self.assertEqual(store.counts["active"], 0)
        self.assertEqual(store.counts["pending"], 3)
        self.assertFalse((self.root / "core/user-core.md").exists())

    def test_false_evidence_and_ambiguous_source_are_pending(self):
        messages = parse_messages(
            "[15:32:01] user: concise\n[15:32:20] assistant: concise\n", "2026-01-01.md"
        )
        raw = dict(
            kind="preference",
            subject="user",
            predicate="reply",
            value="concise",
            evidence="concise",
            message_index=0,
            source="user",
            confidence=1,
        )
        c = list(compile_messages(messages, llm=lambda *_: json.dumps([raw])))[0]["claims"][0]
        self.assertFalse(c["evidence_verified"])
        self.assertEqual(c["provenance"]["precision"], "chunk")

    def test_relation_and_version_chain(self):
        old = verified()
        store = MemoryStore(self.root)
        store.ingest(old)
        store.commit()
        changed = verified(value="detailed", evidence="I now prefer detailed replies")
        changed["provenance"] = dict(
            old["provenance"], timestamp="2026-02-01T00:00:00+00:00", message_id="2"
        )
        store = MemoryStore(self.root)
        store.ingest(changed, llm=lambda *_: '[{"relation":"SUPERSEDES","confidence":0.99}]')
        store.commit()
        _, records = load_records(self.root / "core/preferences.md")
        self.assertEqual([r["status"] for r in records], ["superseded", "active"])
        chain = json.loads((self.root / "system/version-chain.json").read_text(encoding="utf-8"))
        self.assertEqual(chain[-1]["relation"], "SUPERSEDES")
        self.assertTrue(chain[-1]["supersedes"])

    def test_conflicts_and_uncertain_do_not_overwrite(self):
        store = MemoryStore(self.root)
        store.ingest(verified())
        store.commit()
        before = (self.root / "core/preferences.md").read_bytes()
        store = MemoryStore(self.root)
        store.ingest(
            verified(value="detailed"),
            llm=lambda *_: '[{"relation":"CONTRADICTS","confidence":0.99}]',
        )
        store.commit()
        self.assertEqual((self.root / "core/preferences.md").read_bytes(), before)
        self.assertIn(
            "CONTRADICTS", (self.root / "system/conflict-log.md").read_text(encoding="utf-8")
        )

    def test_observe_does_not_prevent_review(self):
        self.write("[15:32:01] user: concise\n")
        self.assertEqual(run(self.config, mode="observe", llm=echo_llm)["pending"], 1)
        self.assertEqual(run(self.config, llm=echo_llm)["active"], 1)

    def test_transaction_rollback_preserves_later_edits(self):
        tx = Transaction(self.root)
        txid = tx.commit({"long-term/projects.md": "first"})
        self.assertIn("long-term/projects.md", tx.rollback(txid))
        (self.root / "long-term/projects.md").write_text("user edit", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            tx.rollback(txid, yes=True)

    def test_rollback_restores_with_backup_and_protects_identity(self):
        tx = Transaction(self.root)
        tx.commit({"long-term/projects.md": "first"})
        txid = tx.commit({"long-term/projects.md": "second"})
        rollback_id = tx.rollback(txid, yes=True)
        self.assertEqual((self.root / "long-term/projects.md").read_text(), "first")
        self.assertTrue((self.root / "system/transactions" / (rollback_id + ".json")).exists())
        with self.assertRaises(ValueError):
            tx.commit({"core/identity.md": "changed"})

    def test_interrupted_transaction_replays_idempotently(self):
        tx = Transaction(self.root)
        import storage

        actual = storage.atomic_write

        def fail(path, content):
            if str(path).endswith("second.md"):
                raise OSError("synthetic disk failure")
            return actual(path, content)

        with patch("storage.atomic_write", side_effect=fail):
            with self.assertRaises(OSError):
                tx.commit({"long-term/first.md": "one", "long-term/second.md": "two"})
        self.assertTrue(tx.journal.exists())
        tx.recover()
        tx.recover()
        self.assertEqual((self.root / "long-term/second.md").read_text(), "two")
        self.assertFalse(tx.journal.exists())

    def test_writer_lock_refuses_parallel_writer(self):
        with writer_lock(self.root):
            with self.assertRaises(RuntimeError):
                with writer_lock(self.root):
                    pass

    def test_unsafe_paths_refused(self):
        for rel in ("../outside", "core/../../outside", "C:/outside"):
            with self.assertRaises(ValueError):
                safe_path(self.root, rel)

    def test_no_import_time_config(self):
        import extract
        import sleep_purify

        self.assertIs(extract.normalize_claim, sleep_purify.normalize_claim)
        self.assertIn("TAIL", extract.build_user("x" * 12000 + "TAIL", "day"))
