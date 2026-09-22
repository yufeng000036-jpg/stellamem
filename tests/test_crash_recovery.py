"""P0-E: end-to-end crash recovery, stale lock and rollback on a synthetic root.

Everything here runs in a temporary directory. Real user memory is never touched.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import storage
from storage import Transaction, writer_lock
from store import MemoryStore
from test_memory_safety import verified


class CrashRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_crash_mid_transaction_then_restart_recovers(self):
        tx = Transaction(self.root)
        real = storage.atomic_write
        state = {"written": []}

        def flaky(path, content):
            if str(path).endswith("second.md"):
                raise OSError("simulated power loss")
            state["written"].append(str(path))
            return real(path, content)

        with patch("storage.atomic_write", side_effect=flaky):
            with self.assertRaises(OSError):
                tx.commit({"long-term/first.md": "one", "long-term/second.md": "two"})
        # Journal survived the crash and names the unfinished transaction.
        self.assertTrue(tx.journal.exists())
        journal = json.loads(tx.journal.read_text(encoding="utf-8"))
        self.assertEqual(journal["schema_version"], 2)
        self.assertIn("long-term/second.md", journal["files"])
        # Restart: recovery replays the pending transaction exactly once.
        Transaction(self.root).recover()
        self.assertEqual((self.root / "long-term/first.md").read_text(), "one")
        self.assertEqual((self.root / "long-term/second.md").read_text(), "two")
        self.assertFalse(tx.journal.exists())
        # Recovery is idempotent.
        Transaction(self.root).recover()
        self.assertEqual((self.root / "long-term/second.md").read_text(), "two")

    def test_recovery_refuses_to_clobber_external_edits(self):
        tx = Transaction(self.root)
        real = storage.atomic_write

        def flaky(path, content):
            if str(path).endswith("second.md"):
                raise OSError("simulated crash")
            return real(path, content)

        with patch("storage.atomic_write", side_effect=flaky):
            with self.assertRaises(OSError):
                tx.commit({"long-term/first.md": "one", "long-term/second.md": "two"})
        # A human edits the file while the transaction is unresolved.
        (self.root / "long-term/first.md").write_text("human edit", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            Transaction(self.root).recover()
        # The external edit is preserved; nothing was overwritten.
        self.assertEqual((self.root / "long-term/first.md").read_text(), "human edit")

    def test_commit_refuses_while_journal_pending(self):
        tx = Transaction(self.root)
        real = storage.atomic_write

        def flaky(path, content):
            if str(path).endswith("second.md"):
                raise OSError("crash")
            return real(path, content)

        with patch("storage.atomic_write", side_effect=flaky):
            with self.assertRaises(OSError):
                tx.commit({"long-term/first.md": "one", "long-term/second.md": "two"})
        with self.assertRaises(RuntimeError):
            tx.commit({"long-term/third.md": "three"})

    def test_rollback_after_crash_restores_prior_state(self):
        tx = Transaction(self.root)
        (self.root / "long-term").mkdir()
        (self.root / "long-term/projects.md").write_text("original", encoding="utf-8")
        txid = tx.commit({"long-term/projects.md": "updated"})
        self.assertEqual((self.root / "long-term/projects.md").read_text(), "updated")
        rb = tx.rollback(txid, yes=True)
        self.assertEqual((self.root / "long-term/projects.md").read_text(), "original")
        # Rollback is itself a recorded transaction with a backup.
        self.assertTrue((self.root / "system/transactions" / (rb + ".json")).exists())

    def test_rollback_refuses_when_later_edits_exist(self):
        tx = Transaction(self.root)
        txid = tx.commit({"long-term/projects.md": "first"})
        (self.root / "long-term/projects.md").write_text("later human edit", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            tx.rollback(txid, yes=True)
        self.assertEqual((self.root / "long-term/projects.md").read_text(), "later human edit")

    def test_stale_lock_from_dead_process_is_reusable(self):
        # A lock file left on disk by a crashed process must not block forever:
        # locking is advisory and released when the file handle closes.
        with writer_lock(self.root):
            pass
        self.assertTrue((self.root / "system/writer.lock").exists())
        # A fresh writer can acquire after the previous handle closed.
        with writer_lock(self.root):
            pass

    def test_no_duplicate_records_after_crash_and_replay(self):
        store = MemoryStore(self.root)
        store.ingest(verified())
        store.commit()
        # Simulate a crash just before the journal is cleared, then replay.
        tx = Transaction(self.root)
        txj = tx.journal
        txj.write_text(
            json.dumps({"schema_version": 2, "id": "0" * 32, "files": {}}), encoding="utf-8"
        )
        tx.recover()
        _, records = storage.load_records(self.root / "core/preferences.md")
        self.assertEqual(len(records), 1)
        self.assertFalse(txj.exists())

    def test_interrupted_write_leaves_canonical_memory_readable(self):
        store = MemoryStore(self.root)
        store.ingest(verified())
        store.commit()
        canonical = self.root / "core/preferences.md"
        before = canonical.read_text(encoding="utf-8")
        # A failed second commit must not corrupt the first: crash on the atomic
        # replacement of a second file in the same transaction.
        tx = Transaction(self.root)
        real = storage.atomic_write

        def flaky(path, content):
            if str(path).endswith("second.md"):
                raise OSError("crash")
            return real(path, content)

        valid = storage.render_records(
            "",
            [
                {
                    "schema_version": 2,
                    "claim_id": "c-2",
                    "revision_id": "r-2",
                    "status": "active",
                    "predicate": "p",
                    "value": "v",
                }
            ],
        )
        with patch("storage.atomic_write", side_effect=flaky):
            with self.assertRaises(OSError):
                tx.commit({"core/preferences.md": valid, "long-term/second.md": "two"})
        entry_id = json.loads(tx.journal.read_text(encoding="utf-8"))["id"]
        # Recovery is roll-forward: it completes the interrupted transaction rather
        # than corrupting it. The key guarantee is that files stay READABLE and a
        # valid managed record, never a half-written file.
        tx.recover()
        prefix, records = storage.load_records(canonical)
        self.assertTrue(records)
        self.assertIsInstance(records[0], dict)
        self.assertFalse(tx.journal.exists())
        # Rolling back the same transaction returns the exact prior bytes.
        rb = tx.rollback(entry_id, yes=True)
        self.assertTrue(rb)
        self.assertEqual(canonical.read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()
