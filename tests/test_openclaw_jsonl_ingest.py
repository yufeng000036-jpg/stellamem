"""Offline synthetic tests for the opt-in OpenClaw JSONL ingestion loop.

The loop under test is the shared machinery in ``openclaw_sqlite_ingest``
driving the JSONL source in ``openclaw_jsonl_source``. All fixtures are
synthetic session files in a temp directory; no real OpenClaw data is
touched.
"""

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))
from openclaw_sqlite_ingest import (
    check_pointers,
    grant_database,
    ingest_session,
    ledger_identity,
    load_allowlist,
    load_pointer_store,
    record_source_deletion,
    reinstate_pointer,
    revoke_database,
)
from source_reader import SourceError

S1 = "11111111-2222-3333-4444-555555555555.jsonl"
S2 = "66666666-7777-8888-9999-000000000000.jsonl"
DELETED = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.deleted.jsonl"
TS = "2026-01-01T00:00:00.000Z"


def header():
    return {"type": "session", "id": "x", "timestamp": TS, "version": 1}


def message(mid="m1", role="user", content="fictional message"):
    return {
        "type": "message",
        "id": mid,
        "parentId": None,
        "timestamp": TS,
        "message": {"role": role, "content": content},
    }


class JsonlIngestFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.sessions = root / "sessions"
        self.sessions.mkdir()
        self.allowlist = root / "allowlist.json"
        self.store = root / "pointers.json"
        self.ledger = root / "source-ledger.json"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, lines, name=S1):
        path = self.sessions / name
        path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
        return path

    def basic(self, name=S1):
        return self.write(
            [
                header(),
                message(),
                message("m2", "assistant", [{"type": "text", "text": "fictional reply"}]),
            ],
            name=name,
        )

    def grant(self, sessions="*"):
        return grant_database(
            self.allowlist,
            db_id="jsonl-main",
            database=self.sessions,
            sessions=sessions,
            actor="operator",
        )

    def ingest(self, session=S1, **kwargs):
        return ingest_session(
            self.allowlist, self.store, db_id="jsonl-main", session_id=session, **kwargs
        )

    def entries(self):
        return load_pointer_store(self.store)

    def entry(self, record_id):
        return next(item for item in self.entries() if item["record_id"] == record_id)


class AuthorizationTests(JsonlIngestFixture):
    def test_default_deny_without_allowlist(self):
        self.basic()
        with self.assertRaises(SourceError):
            self.ingest()
        self.assertEqual(self.entries(), [])

    def test_unlisted_session_is_denied(self):
        self.basic()
        self.basic(S2)
        self.grant(sessions=[S1])
        with self.assertRaises(SourceError):
            self.ingest(S2)

    def test_grant_records_jsonl_provider(self):
        self.basic()
        self.grant()
        entry = load_allowlist(self.allowlist)["jsonl-main"]
        self.assertEqual(entry["provider"], "openclaw-jsonl")
        self.assertEqual(entry["path"], str(self.sessions.resolve()))

    def test_grant_deleted_directory_is_refused(self):
        deleted_dir = self.sessions.parent / "deleted"
        deleted_dir.mkdir()
        with self.assertRaises(SourceError):
            grant_database(
                self.allowlist,
                db_id="gone",
                database=deleted_dir,
                actor="operator",
            )

    def test_deleted_session_file_is_never_ingested(self):
        self.write([header(), message()], name=DELETED)
        self.grant(sessions=[DELETED])  # even an explicit listing cannot win
        with self.assertRaises(SourceError):
            self.ingest(DELETED)
        self.assertEqual(self.entries(), [])


class PointerTests(JsonlIngestFixture):
    def test_ingest_registers_jsonl_pointers(self):
        self.basic()
        self.grant()
        result = self.ingest()
        self.assertEqual(len(result["registered"]), 2)
        entries = self.entries()
        pointer = entries[0]["pointer"]
        self.assertEqual(pointer["provider"], "openclaw-jsonl")
        self.assertEqual(pointer["session_id"], S1)
        self.assertEqual(pointer["message_id"], "m1")
        self.assertEqual(pointer["sequence"], 1)
        self.assertTrue(result["registered"][0].startswith("openclaw-jsonl:jsonl-main:"))
        self.assertEqual(
            ledger_identity(pointer),
            {
                "session_file": S1,
                "message_id": "m1",
                "content_hash": pointer["content_hash"],
            },
        )

    def test_append_keeps_old_pointers_and_registers_only_new(self):
        path = self.basic()
        self.grant()
        first = self.ingest()
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(message("m3", "user", "appended fictional")) + "\n")
        report = check_pointers(self.allowlist, self.store)
        self.assertEqual(report["marked"], [])
        self.assertEqual(report["still_active"], 2)
        second = self.ingest()
        self.assertEqual(second["existing"], first["registered"])
        self.assertEqual(len(second["registered"]), 1)
        self.assertEqual(len(self.entries()), 3)


class PropagationTests(JsonlIngestFixture):
    def setUp(self):
        super().setUp()
        self.basic()
        self.grant()
        result = self.ingest()
        self.id1, self.id2 = result["registered"]

    def test_edit_in_place_flags_changed(self):
        self.write(
            [
                header(),
                message(content="edited text"),
                message("m2", "assistant", [{"type": "text", "text": "fictional reply"}]),
            ]
        )
        report = check_pointers(self.allowlist, self.store)
        self.assertEqual(report["marked"], [{"record_id": self.id1, "reason": "source_changed"}])
        self.assertEqual(self.entry(self.id2)["status"], "active")

    def test_deleting_last_message_flags_missing(self):
        self.write([header(), message()])
        report = check_pointers(self.allowlist, self.store)
        self.assertEqual(report["marked"], [{"record_id": self.id2, "reason": "source_missing"}])

    def test_midfile_insert_flags_the_tail_closed(self):
        self.write(
            [
                header(),
                {"type": "model_change", "id": "n1", "parentId": None, "timestamp": TS},
                message(),
                message("m2", "assistant", [{"type": "text", "text": "fictional reply"}]),
            ]
        )
        report = check_pointers(self.allowlist, self.store)
        self.assertEqual({item["record_id"] for item in report["marked"]}, {self.id1, self.id2})
        self.assertEqual({item["reason"] for item in report["marked"]}, {"source_missing"})

    def test_whole_file_deletion_flags_missing(self):
        (self.sessions / S1).unlink()
        report = check_pointers(self.allowlist, self.store)
        self.assertEqual(len(report["marked"]), 2)
        self.assertEqual({item["reason"] for item in report["marked"]}, {"source_missing"})

    def test_tombstone_wins_over_present_bytes(self):
        pointer = self.entry(self.id1)["pointer"]
        record_source_deletion(self.ledger, pointer, reason="deleted")
        report = check_pointers(self.allowlist, self.store, ledger_path=self.ledger)
        self.assertEqual(report["marked"], [{"record_id": self.id1, "reason": "source_tombstoned"}])
        with self.assertRaises(SourceError):
            reinstate_pointer(
                self.allowlist, self.store, record_id=self.id1, ledger_path=self.ledger
            )
        with self.assertRaises(SourceError):
            self.ingest(ledger_path=self.ledger)

    def test_revoked_authorization_flags_records(self):
        revoke_database(self.allowlist, db_id="jsonl-main", actor="operator")
        report = check_pointers(self.allowlist, self.store)
        self.assertEqual(len(report["marked"]), 2)
        self.assertEqual({item["reason"] for item in report["marked"]}, {"authorization_revoked"})


class LegacyAllowlistTests(JsonlIngestFixture):
    def test_sqlite_entry_without_provider_still_works(self):
        db = Path(self.tmp.name) / "host.sqlite"
        con = sqlite3.connect(db)
        con.execute(
            "CREATE TABLE transcript_events (session_id TEXT, seq INTEGER, "
            "event_json TEXT, event_zstd BLOB, event_utf8_bytes INTEGER, message_role TEXT)"
        )
        con.execute(
            "INSERT INTO transcript_events VALUES ('s1', 0, ?, NULL, NULL, 'user')",
            (
                json.dumps(
                    {
                        "type": "message",
                        "id": "m1",
                        "timestamp": TS,
                        "message": {"role": "user", "content": "fictional message"},
                    }
                ),
            ),
        )
        con.commit()
        con.close()
        self.allowlist.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "databases": {"main": {"path": str(db), "sessions": "*"}},
                }
            ),
            encoding="utf-8",
        )
        result = ingest_session(self.allowlist, self.store, db_id="main", session_id="s1")
        self.assertEqual(len(result["registered"]), 1)
        self.assertEqual(self.entries()[0]["pointer"]["provider"], "openclaw-sqlite")


if __name__ == "__main__":
    unittest.main()
