"""Source tombstone/rotation ledger: deletion must beat every surviving copy.

PR #117 tried to resolve old pointers from rotated archives after the
canonical filename was reused, and was blocked: the same on-disk state can
mean rotation or user deletion. These tests pin the ledger contract that
separates the two — a tombstone refuses a pointer everywhere, a rotation
record authorizes exactly one unchanged archive, and without either signal
the reused-filename case stays refused (the pre-#117 behavior).
"""

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))
from source_ledger import (
    LEDGER_FILENAME,
    LedgerError,
    load_ledger,
    record_rotation,
    record_tombstone,
)
from source_reader import SourceError, content_hash, read_daily


def entry(text, *, role="user", ts="2026-01-01T15:32:01Z", mid="m1"):
    return json.dumps(
        {
            "type": "message",
            "id": mid,
            "timestamp": ts,
            "message": {"role": role, "content": [{"type": "text", "text": text}]},
        }
    )


def pointer(session_file, text, *, idx=0, role="user", ts="2026-01-01T15:32:01Z", mid="m1"):
    return {
        "provider": "openclaw",
        "session_file": session_file,
        "session_key": session_file.split(".jsonl")[0],
        "entry_index": idx,
        "message_id": mid,
        "timestamp": ts,
        "speaker": role,
        "content_hash": content_hash(text),
        "precision": "message",
    }


class LedgerUnitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Path(self.tmp.name) / LEDGER_FILENAME

    def tearDown(self):
        self.tmp.cleanup()

    def tombstone(self, mid):
        return record_tombstone(
            self.ledger,
            session_file="session.jsonl",
            message_id=mid,
            content_hash=content_hash("text " + mid),
            reason="deleted",
            recorded_at="2026-01-02T00:00:00+00:00",
        )

    def test_chain_verification_detects_tampering(self):
        self.tombstone("a")
        self.tombstone("b")
        data = json.loads(self.ledger.read_text(encoding="utf-8"))
        data["records"][0]["reason"] = "edited"
        self.ledger.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(LedgerError):
            load_ledger(self.ledger)

    def test_concurrent_appends_keep_chain_valid(self):
        def worker(n):
            for i in range(5):
                self.tombstone("w%d-%d" % (n, i))

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        records = load_ledger(self.ledger)
        self.assertEqual(len(records), 20)
        self.assertEqual([r["seq"] for r in records], list(range(20)))
        self.assertEqual(len({r["hash"] for r in records}), 20)

    def test_rotation_requires_existing_archive(self):
        with self.assertRaises(LedgerError):
            record_rotation(
                self.ledger,
                from_file="session.jsonl",
                to_file=Path(self.tmp.name) / "session.jsonl.reset.2026",
            )


class TombstoneResolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sessions = self.root / "sessions"
        self.sessions.mkdir()
        self.memory = self.root / "memory"
        self.memory.mkdir()
        self.config = {"sessions_dir": str(self.sessions)}
        self.ledger = self.sessions / LEDGER_FILENAME
        self.archive = self.sessions / "session.jsonl.reset.2026"

    def tearDown(self):
        self.tmp.cleanup()

    def write_manifest(self, pointers):
        daily = self.memory / "2026-01-01.md"
        daily.write_text("summary only; original lives in the host session\n", encoding="utf-8")
        daily.with_suffix(".sources.json").write_text(
            json.dumps({"schema_version": 2, "pointers": pointers}), encoding="utf-8"
        )
        return daily

    def reuse_fixture(self):
        """#117's state: archive holds the old entry, canonical name reused."""
        self.archive.write_text(entry("old source"), encoding="utf-8")
        (self.sessions / "session.jsonl").write_text(
            entry("new source", mid="different"), encoding="utf-8"
        )
        return self.write_manifest([pointer("session.jsonl", "old source")])

    # Scenario: normal rotation, authorized by a ledger record.
    def test_rotation_with_record_resolves_after_filename_reuse(self):
        daily = self.reuse_fixture()
        record_rotation(self.ledger, from_file="session.jsonl", to_file=self.archive)
        message = read_daily(daily, self.config)[0]
        self.assertEqual(message["text"], "old source")
        self.assertEqual(message["provenance"]["resolved_session_file"], "session.jsonl.reset.2026")
        self.assertEqual(message["provenance"]["resolved_via"], "rotation-ledger")

    # The same fixture read as a deletion: no record, no resurrection.
    def test_filename_reuse_without_record_stays_refused(self):
        daily = self.reuse_fixture()
        with self.assertRaises(SourceError):
            read_daily(daily, self.config)

    # Scenario: active deletion of a whole session; archive copy must not win.
    def test_deleted_session_refused_despite_archive_copy(self):
        self.archive.write_text(entry("deleted text"), encoding="utf-8")
        record_tombstone(
            self.ledger,
            session_file="session.jsonl",
            message_id="m1",
            content_hash=content_hash("deleted text"),
            reason="deleted",
        )
        daily = self.write_manifest([pointer("session.jsonl", "deleted text")])
        with self.assertRaises(SourceError):
            read_daily(daily, self.config)

    # Scenario: deletion beats even a valid rotation authorization.
    def test_tombstone_beats_rotation_record(self):
        daily = self.reuse_fixture()
        record_rotation(self.ledger, from_file="session.jsonl", to_file=self.archive)
        record_tombstone(
            self.ledger,
            session_file="session.jsonl",
            message_id="m1",
            content_hash=content_hash("old source"),
            reason="deleted",
        )
        with self.assertRaises(SourceError):
            read_daily(daily, self.config)

    def test_manifest_naming_reset_archive_honors_original_tombstone(self):
        self.reuse_fixture()
        daily = self.write_manifest([pointer(self.archive.name, "old source")])
        with self.assertRaises(SourceError):
            read_daily(daily, self.config)
        record_rotation(self.ledger, from_file="session.jsonl", to_file=self.archive)
        self.assertEqual(read_daily(daily, self.config)[0]["text"], "old source")
        record_tombstone(
            self.ledger,
            session_file="session.jsonl",
            message_id="m1",
            content_hash=content_hash("old source"),
            reason="deleted",
        )
        with self.assertRaises(SourceError):
            read_daily(daily, self.config)

    # Scenario: active edit — old identity dies, edited identity resolves.
    def test_edit_tombstones_old_pointer_and_new_pointer_resolves(self):
        (self.sessions / "session.jsonl").write_text(entry("edited text"), encoding="utf-8")
        self.archive.write_text(entry("old source"), encoding="utf-8")
        record_rotation(self.ledger, from_file="session.jsonl", to_file=self.archive)
        record_tombstone(
            self.ledger,
            session_file="session.jsonl",
            message_id="m1",
            content_hash=content_hash("old source"),
            reason="edited",
        )
        old_daily = self.write_manifest([pointer("session.jsonl", "old source")])
        with self.assertRaises(SourceError):
            read_daily(old_daily, self.config)
        new_daily = self.write_manifest([pointer("session.jsonl", "edited text")])
        self.assertEqual(read_daily(new_daily, self.config)[0]["text"], "edited text")

    # Scenario: archive retention lapse — changed or missing archive bytes
    # revoke the rotation authorization by themselves.
    def test_archive_bytes_changed_after_recording_loses_authorization(self):
        daily = self.reuse_fixture()
        record_rotation(self.ledger, from_file="session.jsonl", to_file=self.archive)
        self.archive.write_text(
            entry("old source") + "\n" + entry("later", mid="m2"), encoding="utf-8"
        )
        with self.assertRaises(SourceError):
            read_daily(daily, self.config)

    def test_archive_deleted_after_recording_loses_authorization(self):
        daily = self.reuse_fixture()
        record_rotation(self.ledger, from_file="session.jsonl", to_file=self.archive)
        self.archive.unlink()
        with self.assertRaises(SourceError):
            read_daily(daily, self.config)

    # Scenario: rollback — a self-consistent old snapshot is only detectable
    # when the caller pins the head it saw when the tombstone was written.
    def test_rollback_detected_with_pinned_head(self):
        self.archive.write_text(entry("deleted text"), encoding="utf-8")
        record_rotation(self.ledger, from_file="session.jsonl", to_file=self.archive)
        snapshot = self.ledger.read_bytes()
        record = record_tombstone(
            self.ledger,
            session_file="session.jsonl",
            message_id="m1",
            content_hash=content_hash("deleted text"),
            reason="deleted",
        )
        daily = self.write_manifest([pointer("session.jsonl", "deleted text")])
        with self.assertRaises(SourceError):
            read_daily(daily, dict(self.config, source_ledger_head=record["hash"]))
        # Roll the ledger back to the self-consistent snapshot taken before
        # the tombstone existed: the chain still verifies, but the pinned
        # head is gone from it, so a pinning caller refuses.
        self.ledger.write_bytes(snapshot)
        self.assertEqual(len(load_ledger(self.ledger)), 1)
        with self.assertRaises(SourceError):
            read_daily(daily, dict(self.config, source_ledger_head=record["hash"]))
        # Without a pin the old snapshot is indistinguishable from a
        # legitimately older ledger; the tombstone is gone and the archive
        # resolves. This is the documented residual risk of an unpinned
        # rollback, asserted here so it can never change silently.
        self.assertEqual(read_daily(daily, self.config)[0]["text"], "deleted text")

    # A ledger that exists but cannot be verified is no authority at all.
    def test_corrupt_ledger_fails_closed(self):
        (self.sessions / "session.jsonl").write_text(entry("some text"), encoding="utf-8")
        self.ledger.write_text("{not json", encoding="utf-8")
        daily = self.write_manifest([pointer("session.jsonl", "some text")])
        with self.assertRaises(SourceError):
            read_daily(daily, self.config)

    # Glob metacharacters in a session name must not reach other sessions
    # through the pre-existing missing-file search either.
    def test_glob_metacharacters_cannot_cross_sessions(self):
        (self.sessions / "session1.jsonl.reset.2026").write_text(
            entry("old source"), encoding="utf-8"
        )
        daily = self.write_manifest([pointer("session[1].jsonl", "old source")])
        with self.assertRaises(SourceError):
            read_daily(daily, self.config)


if __name__ == "__main__":
    unittest.main()
