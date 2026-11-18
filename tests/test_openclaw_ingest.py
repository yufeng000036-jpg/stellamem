"""Offline synthetic tests for the opt-in OpenClaw SQLite ingestion loop.

All fixtures are synthetic SQLite databases built in a temp directory; no
real OpenClaw data is touched. The compressed-row fixture embeds one real
zstd frame (base64) so the decoder-absent path is exercised identically in
every environment, and the decoder-present path runs wherever the optional
zstandard package is installed.
"""

import base64
import hashlib
import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))
import openclaw_sqlite_ingest as ingest
from openclaw_sqlite_ingest import (
    check_pointers,
    grant_database,
    grant_session,
    ingest_session,
    ledger_identity,
    load_allowlist,
    load_audit,
    load_pointer_store,
    record_source_deletion,
    reinstate_pointer,
    revoke_database,
    revoke_session,
    zstandard_available,
)
from source_reader import SourceError

# One real zstd frame of the JSON for event m2 below (132 bytes declared).
ZSTD_FRAME_B64 = (
    "KLUv/SCETQMAooUTGIAnbn8oCqptFeEY09Q9g3rMtZEe2cA8JceBxJnf2K50BVJLOymh1Vut1M"
    "6GYjU07gzKyRSUJCVJwKRhi5q/2oZQPAC+gJi/eAfPDo1GCQBCCPMTZMLXviKAVGC6UDbjXuKmazYB"
)
ZSTD_DECLARED = 132


def event(mid="m1", role="user", content="fictional message", ts="2026-01-01T00:00:00Z"):
    return {
        "type": "message",
        "id": mid,
        "timestamp": ts,
        "message": {"role": role, "content": content},
    }


class IngestFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = root / "host.sqlite"
        self.con = sqlite3.connect(self.db)
        self.con.execute(
            "CREATE TABLE transcript_events (session_id TEXT, seq INTEGER, "
            "event_json TEXT, event_zstd BLOB, event_utf8_bytes INTEGER, message_role TEXT)"
        )
        self.allowlist = root / "allowlist.json"
        self.store = root / "pointers.json"
        self.ledger = root / "source-ledger.json"

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def add(self, obj, *, seq=0, session="s1", role="user"):
        self.con.execute(
            "INSERT INTO transcript_events VALUES (?, ?, ?, NULL, NULL, ?)",
            (session, seq, json.dumps(obj), role),
        )
        self.con.commit()

    def add_compressed(self, *, seq=1, session="s1"):
        self.con.execute(
            "INSERT INTO transcript_events VALUES (?, ?, NULL, ?, ?, ?)",
            (session, seq, base64.b64decode(ZSTD_FRAME_B64), ZSTD_DECLARED, "user"),
        )
        self.con.commit()

    def grant(self, sessions="*"):
        return grant_database(
            self.allowlist,
            db_id="main",
            database=self.db,
            sessions=sessions,
            actor="operator",
        )

    def ingest_s1(self, **kwargs):
        return ingest_session(self.allowlist, self.store, db_id="main", session_id="s1", **kwargs)

    def entries(self):
        return load_pointer_store(self.store)

    def entry(self, record_id):
        return next(item for item in self.entries() if item["record_id"] == record_id)


class AllowlistTests(IngestFixture):
    def test_ingest_without_allowlist_is_denied_by_default(self):
        self.add(event())
        with self.assertRaises(SourceError):
            self.ingest_s1()
        self.assertEqual(self.entries(), [])

    def test_ingest_denied_for_unlisted_database_and_session(self):
        self.add(event())
        self.add(event("m9"), session="s2")
        self.grant(sessions=["s1"])
        with self.assertRaises(SourceError):
            ingest_session(self.allowlist, self.store, db_id="other", session_id="s1")
        with self.assertRaises(SourceError):
            ingest_session(self.allowlist, self.store, db_id="main", session_id="s2")
        self.assertEqual(self.entries(), [])

    def test_grants_and_revokes_are_hash_chained_audit_events(self):
        self.grant(sessions=["s1", "s2"])
        grant_session(self.allowlist, db_id="main", session_id="s3", actor="operator")
        revoke_session(self.allowlist, db_id="main", session_id="s2", actor="operator")
        events = load_audit(ingest.default_audit_path(self.allowlist))
        self.assertEqual(
            [item["action"] for item in events],
            ["grant_database", "grant_session", "revoke_session"],
        )
        self.assertEqual([item["seq"] for item in events], [0, 1, 2])
        self.assertEqual(events[1]["prev"], events[0]["hash"])
        self.assertEqual(events[2]["prev"], events[1]["hash"])
        self.assertTrue(all(item["actor"] == "operator" for item in events))
        self.assertEqual(load_allowlist(self.allowlist)["main"]["sessions"], ["s1", "s3"])

    def test_revoke_database_denies_further_ingest(self):
        self.add(event())
        self.grant()
        self.ingest_s1()
        revoke_database(self.allowlist, db_id="main", actor="operator")
        with self.assertRaises(SourceError):
            self.ingest_s1()
        actions = [item["action"] for item in load_audit(ingest.default_audit_path(self.allowlist))]
        self.assertEqual(actions, ["grant_database", "revoke_database"])

    def test_tampered_audit_log_refuses_further_changes(self):
        self.grant()
        audit_path = ingest.default_audit_path(self.allowlist)
        text = audit_path.read_text(encoding="utf-8").replace("operator", "mallory")
        audit_path.write_text(text, encoding="utf-8")
        with self.assertRaises(SourceError):
            load_audit(audit_path)
        with self.assertRaises(SourceError):
            grant_session(self.allowlist, db_id="main", session_id="s9", actor="operator")

    def test_grant_validation_fails_closed(self):
        with self.assertRaises(SourceError):
            grant_database(
                self.allowlist,
                db_id="bad/id",
                database=self.db,
                actor="operator",
            )
        with self.assertRaises(SourceError):
            grant_database(
                self.allowlist,
                db_id="main",
                database=Path(self.tmp.name) / "missing.sqlite",
                actor="operator",
            )
        with self.assertRaises(SourceError):
            self.grant(sessions=[])
        with self.assertRaises(SourceError):
            grant_session(self.allowlist, db_id="main", session_id="s1", actor="operator")
        self.grant()
        with self.assertRaises(SourceError):
            revoke_session(self.allowlist, db_id="main", session_id="s1", actor="operator")
        with self.assertRaises(SourceError):
            revoke_database(self.allowlist, db_id="ghost", actor="operator")

    def test_malformed_allowlist_fails_closed(self):
        self.add(event())
        self.allowlist.write_text("{not json", encoding="utf-8")
        with self.assertRaises(SourceError):
            load_allowlist(self.allowlist)
        with self.assertRaises(SourceError):
            self.ingest_s1()


class PointerStoreTests(IngestFixture):
    def test_ingest_registers_full_source_pointers(self):
        self.add(event())
        self.add(event("m2", "assistant", "fictional reply"), seq=1, role="assistant")
        self.grant()
        result = self.ingest_s1()
        self.assertEqual(len(result["registered"]), 2)
        entries = self.entries()
        self.assertEqual(len(entries), 2)
        pointer = entries[0]["pointer"]
        self.assertEqual(pointer["db_id"], "main")
        self.assertEqual(pointer["session_id"], "s1")
        self.assertEqual(pointer["message_id"], "m1")
        self.assertEqual(pointer["sequence"], 0)
        self.assertEqual(
            pointer["content_hash"],
            hashlib.sha256("fictional message".encode("utf-8")).hexdigest(),
        )
        self.assertEqual(len(pointer["event_hash"]), 64)
        self.assertEqual(entries[0]["status"], "active")
        self.assertEqual(
            ledger_identity(pointer),
            {
                "session_file": "main.openclaw-sqlite",
                "message_id": "m1",
                "content_hash": pointer["content_hash"],
            },
        )

    def test_reingest_is_idempotent(self):
        self.add(event())
        self.grant()
        first = self.ingest_s1()
        second = self.ingest_s1()
        self.assertEqual(len(first["registered"]), 1)
        self.assertEqual(second["registered"], [])
        self.assertEqual(second["existing"], first["registered"])
        self.assertEqual(len(self.entries()), 1)

    def test_record_id_collision_aborts_without_partial_write(self):
        self.add(event())
        self.add(event("m2"), seq=1)
        self.grant()
        with self.assertRaises(SourceError):
            self.ingest_s1(record_id_for=lambda message: "fixed-id")
        self.assertEqual(self.entries(), [])

    def test_custom_record_ids_are_used(self):
        self.add(event())
        self.grant()
        result = self.ingest_s1(
            record_id_for=lambda message: "rec-" + message["provenance"]["message_id"]
        )
        self.assertEqual(result["registered"], ["rec-m1"])
        self.assertEqual(self.entries()[0]["record_id"], "rec-m1")

    def test_check_with_no_registrations(self):
        report = check_pointers(self.allowlist, self.store)
        self.assertEqual(report["checked"], 0)
        self.assertEqual(report["marked"], [])


class DeletionPropagationTests(IngestFixture):
    def setUp(self):
        super().setUp()
        self.add(event())
        self.add(event("m2", content="second fictional"), seq=1)
        self.grant()
        self.result = self.ingest_s1()
        self.id1, self.id2 = self.result["registered"]

    def test_deleted_message_is_flagged_missing(self):
        self.con.execute("DELETE FROM transcript_events WHERE session_id='s1' AND seq=1")
        self.con.commit()
        report = check_pointers(self.allowlist, self.store)
        self.assertEqual(report["marked"], [{"record_id": self.id2, "reason": "source_missing"}])
        self.assertEqual(report["still_active"], 1)
        self.assertEqual(self.entry(self.id2)["status"], "needs_review")
        self.assertEqual(self.entry(self.id1)["status"], "active")

    def test_edited_message_is_flagged_changed(self):
        self.con.execute(
            "UPDATE transcript_events SET event_json=? WHERE session_id='s1' AND seq=1",
            (json.dumps(event("m2", content="edited text")),),
        )
        self.con.commit()
        report = check_pointers(self.allowlist, self.store)
        self.assertEqual(report["marked"], [{"record_id": self.id2, "reason": "source_changed"}])

    def test_tombstone_wins_over_present_bytes(self):
        pointer = self.entry(self.id1)["pointer"]
        record_source_deletion(self.ledger, pointer, reason="deleted")
        report = check_pointers(self.allowlist, self.store, ledger_path=self.ledger)
        self.assertEqual(report["marked"], [{"record_id": self.id1, "reason": "source_tombstoned"}])
        self.assertTrue(self.entry(self.id1)["tombstoned"])
        with self.assertRaises(SourceError):
            reinstate_pointer(
                self.allowlist, self.store, record_id=self.id1, ledger_path=self.ledger
            )
        with self.assertRaises(SourceError):
            self.ingest_s1(ledger_path=self.ledger)

    def test_tombstone_reason_beats_simultaneous_edit(self):
        pointer = self.entry(self.id2)["pointer"]
        record_source_deletion(self.ledger, pointer, reason="edited")
        self.con.execute(
            "UPDATE transcript_events SET event_json=? WHERE session_id='s1' AND seq=1",
            (json.dumps(event("m2", content="edited text")),),
        )
        self.con.commit()
        report = check_pointers(self.allowlist, self.store, ledger_path=self.ledger)
        self.assertEqual(report["marked"], [{"record_id": self.id2, "reason": "source_tombstoned"}])

    def test_no_auto_resurrection_then_manual_reinstate(self):
        self.con.execute("DELETE FROM transcript_events WHERE session_id='s1' AND seq=1")
        self.con.commit()
        check_pointers(self.allowlist, self.store)
        self.add(event("m2", content="second fictional"), seq=1)
        report = check_pointers(self.allowlist, self.store)
        self.assertEqual(report["marked"], [])
        self.assertEqual(report["resolves_again"], [self.id2])
        self.assertEqual(self.entry(self.id2)["status"], "needs_review")
        before = self.store.read_bytes()
        assigned = []
        with self.assertRaises(SourceError):
            self.ingest_s1(
                record_id_for=lambda m: assigned.append(m["text"]) or m["provenance"]["message_id"]
            )
        self.assertEqual(assigned, [])
        self.assertEqual(self.store.read_bytes(), before)
        with self.assertRaises(SourceError):
            self.ingest_s1()
        reinstate_pointer(self.allowlist, self.store, record_id=self.id2)
        self.assertEqual(self.ingest_s1()["existing"], [self.id1, self.id2])
        self.assertEqual(self.entry(self.id2)["status"], "active")
        self.assertIsNone(self.entry(self.id2)["review_reason"])

    def test_reinstate_refused_while_source_still_missing(self):
        self.con.execute("DELETE FROM transcript_events WHERE session_id='s1' AND seq=1")
        self.con.commit()
        check_pointers(self.allowlist, self.store)
        with self.assertRaises(SourceError):
            reinstate_pointer(self.allowlist, self.store, record_id=self.id2)
        self.assertEqual(self.entry(self.id2)["status"], "needs_review")

    def test_revoked_authorization_flags_records(self):
        revoke_database(self.allowlist, db_id="main", actor="operator")
        report = check_pointers(self.allowlist, self.store)
        self.assertEqual({item["reason"] for item in report["marked"]}, {"authorization_revoked"})
        self.assertEqual(len(report["marked"]), 2)

    def test_deleted_database_file_flags_missing_not_error(self):
        self.con.close()
        self.db.unlink()
        report = check_pointers(self.allowlist, self.store)
        self.assertEqual({item["reason"] for item in report["marked"]}, {"source_missing"})
        self.assertEqual(len(report["marked"]), 2)


class ConcurrencyDependencyTests(IngestFixture):
    def test_wal_writer_uncommitted_snapshot_then_change_detected(self):
        wal_db = Path(self.tmp.name) / "wal.sqlite"
        setup = sqlite3.connect(wal_db)
        setup.execute("PRAGMA journal_mode=WAL")
        setup.execute(
            "CREATE TABLE transcript_events (session_id TEXT, seq INTEGER, "
            "event_json TEXT, event_zstd BLOB, event_utf8_bytes INTEGER, message_role TEXT)"
        )
        setup.execute(
            "INSERT INTO transcript_events VALUES ('s1', 0, ?, NULL, NULL, 'user')",
            (json.dumps(event()),),
        )
        setup.commit()
        grant_database(self.allowlist, db_id="wal", database=wal_db, actor="operator")
        writer = sqlite3.connect(wal_db)
        try:
            writer.execute("BEGIN IMMEDIATE")
            writer.execute(
                "UPDATE transcript_events SET event_json=? WHERE session_id='s1'",
                (json.dumps(event(content="uncommitted rewrite")),),
            )
            result = ingest_session(self.allowlist, self.store, db_id="wal", session_id="s1")
            self.assertEqual(result["messages"][0]["text"], "fictional message")
            writer.commit()
        finally:
            writer.close()
            setup.close()
        report = check_pointers(self.allowlist, self.store)
        self.assertEqual(
            report["marked"],
            [{"record_id": result["registered"][0], "reason": "source_changed"}],
        )

    def test_ingest_and_check_do_not_mutate_source_file(self):
        self.add(event())
        self.grant()
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        self.ingest_s1()
        check_pointers(self.allowlist, self.store)
        self.assertEqual(before, hashlib.sha256(self.db.read_bytes()).hexdigest())

    def test_compressed_row_without_decoder_fails_whole_ingest(self):
        self.add(event())
        self.add_compressed()
        self.grant()
        with patch.dict(sys.modules, {"zstandard": None}):
            with self.assertRaises(SourceError) as caught:
                self.ingest_s1()
        self.assertIn("zstandard", str(caught.exception))
        self.assertEqual(self.entries(), [])

    @unittest.skipUnless(
        importlib.util.find_spec("zstandard"), "optional zstandard is not installed"
    )
    def test_compressed_row_ingests_when_decoder_available(self):
        self.add_compressed(seq=0)
        self.grant()
        result = self.ingest_s1()
        self.assertEqual(result["messages"][0]["text"], "fictional compressed")
        pointer = self.entries()[0]["pointer"]
        self.assertEqual(
            pointer["content_hash"],
            hashlib.sha256("fictional compressed".encode("utf-8")).hexdigest(),
        )
        report = check_pointers(self.allowlist, self.store)
        self.assertEqual(report["still_active"], 1)

    def test_zstandard_available_flag(self):
        self.assertEqual(zstandard_available(), importlib.util.find_spec("zstandard") is not None)
        with patch.dict(sys.modules, {"zstandard": None}):
            self.assertFalse(zstandard_available())


if __name__ == "__main__":
    unittest.main()
