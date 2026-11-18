"""Offline synthetic tests for the non-integrated SQLite reader prototype."""

import argparse
import hashlib
import json
import shutil
import subprocess
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))
from inbox import cmd_approve
import openclaw_sqlite_source as sqlite_source
from openclaw_sqlite_source import read_session, resolve_pointer
from pipeline import compile_messages
from storage import load_records, writer_lock
from store import INBOX, MemoryStore
from source_reader import SourceError


def event(mid="m1", role="user", content="fictional message"):
    return {
        "type": "message",
        "id": mid,
        "timestamp": "2026-01-01T00:00:00Z",
        "message": {
            "role": role,
            "content": content,
            "__openclaw": {"modelPromptProjection": {"text": "injected sentinel"}},
        },
    }


class SqliteSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "host.sqlite"
        self.con = sqlite3.connect(self.db)
        self.con.execute(
            "CREATE TABLE transcript_events (session_id TEXT, seq INTEGER, "
            "event_json TEXT, event_zstd BLOB, event_utf8_bytes INTEGER, message_role TEXT)"
        )

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def add(self, obj, *, seq=0, session="s1", role="user"):
        self.con.execute(
            "INSERT INTO transcript_events VALUES (?, ?, ?, NULL, NULL, ?)",
            (session, seq, json.dumps(obj), role),
        )
        self.con.commit()

    def test_one_session_uses_original_message_and_does_not_write_database(self):
        self.add(event())
        self.add(
            event("m2", "assistant", [{"type": "text", "text": "fictional reply"}]),
            seq=1,
            role="assistant",
        )
        self.add(event("other", content="other session secret"), session="s2")
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        rows = read_session(self.db, "s1")
        self.assertEqual([r["speaker"] for r in rows], ["user", "assistant"])
        self.assertEqual(rows[0]["text"], "fictional message")
        self.assertNotIn("injected sentinel", str(rows))
        self.assertNotIn("other session secret", str(rows))
        self.assertEqual(rows[0]["provenance"]["message_id"], "m1")
        self.assertEqual(before, hashlib.sha256(self.db.read_bytes()).hexdigest())

    def test_mismatch_unsupported_and_duplicate_fail_entire_read(self):
        for bad, role in (
            (event("m2", "assistant"), "user"),
            (event("m2", content=[{"type": "image", "data": "x"}]), "user"),
            (event("m1"), "user"),
        ):
            with self.subTest(bad=bad):
                self.con.execute("DELETE FROM transcript_events")
                self.add(event())
                self.add(bad, seq=1, role=role)
                with self.assertRaises(SourceError):
                    read_session(self.db, "s1")

    def test_schema25_requires_a_complete_active_window(self):
        self.con.executescript("""
            PRAGMA user_version=25;
            ALTER TABLE transcript_events DROP COLUMN message_role;
            CREATE TABLE session_transcript_active_events
                (session_id, active_position, event_seq, context_eligible, message_position);
            CREATE TABLE session_transcript_index_state
                (session_id, indexed_seq, active_event_count, needs_rebuild);
            INSERT INTO session_transcript_index_state VALUES ('s1', 1, 1, 0);
            INSERT INTO session_transcript_active_events VALUES ('s1', 0, 1, 1, 0);
        """)
        for sid in ("s1", "s2"):
            for seq, item in enumerate(({"type": "session", "id": sid}, event(content=sid))):
                self.con.execute(
                    "INSERT INTO transcript_events VALUES (?, ?, ?, NULL, NULL)",
                    (sid, seq, json.dumps(item)),
                )
        self.con.commit()
        self.assertEqual([m["text"] for m in read_session(self.db, "s1")], ["s1"])
        snapshot = sqlite3.connect(":memory:")
        self.addCleanup(snapshot.close)
        self.con.backup(snapshot)
        for sql in (
            "UPDATE session_transcript_index_state SET needs_rebuild=1",
            "UPDATE session_transcript_index_state SET indexed_seq=0",
            "UPDATE session_transcript_active_events SET context_eligible=0",
            "DELETE FROM session_transcript_active_events",
            """UPDATE transcript_events SET event_json='{"type":"reset"}' WHERE seq=1""",
        ):
            with self.subTest(sql=sql):
                self.con.execute(sql)
                self.con.commit()
                with self.assertRaises(SourceError):
                    read_session(self.db, "s1")
                snapshot.backup(self.con)

    def test_compressed_missing_decoder_and_corruption_fail_closed(self):
        self.add(event())
        self.con.execute(
            "INSERT INTO transcript_events VALUES (?, ?, NULL, ?, ?, ?)",
            ("s1", 1, b"not zstd", 12, None),
        )
        self.con.commit()
        with patch.dict(sys.modules, {"zstandard": None}):
            with self.assertRaises(SourceError):
                read_session(self.db, "s1")
        with self.assertRaises(SourceError):
            read_session(self.db, "s1")

    def test_duplicate_or_invalid_sequence_fails(self):
        for bad_seq in (-1, 0):
            with self.subTest(seq=bad_seq):
                self.con.execute("DELETE FROM transcript_events")
                self.add(event())
                self.add(event("m2"), seq=bad_seq)
                with self.assertRaises(SourceError):
                    read_session(self.db, "s1")

    def test_limits_fail_without_partial_result(self):
        self.add(event())
        self.add(event("m2"), seq=1)
        with patch("openclaw_sqlite_source.MAX_SESSION_EVENTS", 1):
            with self.assertRaises(SourceError):
                read_session(self.db, "s1")
        with patch("openclaw_sqlite_source.MAX_SESSION_BYTES", 1):
            with self.assertRaises(SourceError):
                read_session(self.db, "s1")

    def test_pointer_revalidates_and_rejects_changed_or_deleted_event(self):
        self.add(event())
        message = read_session(self.db, "s1")[0]
        pointer = message["provenance"]
        self.assertEqual(resolve_pointer(self.db, pointer)["text"], "fictional message")
        self.con.execute(
            "UPDATE transcript_events SET event_json=? WHERE session_id=? AND seq=0",
            (json.dumps(event(content="changed text")), "s1"),
        )
        self.con.commit()
        with self.assertRaises(SourceError):
            resolve_pointer(self.db, pointer)
        self.con.execute("DELETE FROM transcript_events WHERE session_id=?", ("s1",))
        self.con.commit()
        with self.assertRaises(SourceError):
            resolve_pointer(self.db, pointer)

    def test_wal_snapshot_is_consistent_and_later_change_is_detected(self):
        self.con.execute("PRAGMA journal_mode=WAL")
        self.add(event())
        self.add(event("m2", content="second original"), seq=1)
        real_decode = sqlite_source._decode_event
        changed = False

        def decode_then_change(*args):
            nonlocal changed
            decoded = real_decode(*args)
            if not changed:
                changed = True
                self.con.execute(
                    "UPDATE transcript_events SET event_json=? WHERE session_id=? AND seq=1",
                    (json.dumps(event("m2", content="second modified")), "s1"),
                )
                self.con.commit()
            return decoded

        with patch("openclaw_sqlite_source._decode_event", side_effect=decode_then_change):
            messages = read_session(self.db, "s1")
        self.assertEqual(messages[1]["text"], "second original")
        with self.assertRaises(SourceError):
            resolve_pointer(self.db, messages[1]["provenance"])

    def test_incomplete_or_cross_session_pointer_is_rejected(self):
        self.add(event())
        pointer = read_session(self.db, "s1")[0]["provenance"]
        with self.assertRaises(SourceError):
            resolve_pointer(self.db, {**pointer, "session_id": "missing"})
        with self.assertRaises(SourceError):
            resolve_pointer(self.db, {"provider": "openclaw-sqlite"})

    @unittest.skipUnless(shutil.which("node"), "Node is needed for the adapter")
    def test_explicit_reviewed_prototype_path_projects_assistant_history(self):
        self.add(event("m1", "assistant", "fictional nebula tea"), role="assistant")
        source = read_session(self.db, "s1")[0]
        verified = resolve_pointer(self.db, source["provenance"])

        def model(system, payload):
            return json.dumps(
                [
                    dict(
                        kind="fact",
                        subject="user",
                        scope="global",
                        predicate="fictional beverage",
                        value="fictional nebula tea",
                        evidence="fictional nebula tea",
                        confidence=0.99,
                        source="assistant",
                        message_index=0,
                    )
                ]
            )

        claims = [
            c
            for batch in compile_messages([verified], {"chunk_chars": 512}, model)
            for c in batch["claims"]
        ]
        memory = Path(self.tmp.name) / "memory"
        memory.mkdir()
        store = MemoryStore(memory)
        store.ingest(claims[0])
        store.commit()
        pending = load_records(memory / INBOX)[1]
        self.assertEqual(pending[0]["source"], "assistant")
        args = argparse.Namespace(
            id=pending[0]["claim_id"],
            source="assistant",
            kind=None,
            subject=None,
            scope=None,
            predicate=None,
            value=None,
        )
        with writer_lock(memory):
            self.assertEqual(cmd_approve(memory, args), 0)
        adapter = Path(__file__).resolve().parents[1] / "adapters/openclaw/index.js"
        js = (
            "const {project} = await import(process.argv[1]); "
            "console.log(await project(process.argv[2], 'fictional beverage'));"
        )
        result = subprocess.run(
            ["node", "--input-type=module", "-e", js, adapter.as_uri(), str(memory)],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("fictional nebula tea", result.stdout)
        self.assertNotIn("injected sentinel", result.stdout)

    def test_invalid_or_missing_session_fails(self):
        self.add(event())
        for session in ("", "missing"):
            with self.assertRaises(SourceError):
                read_session(self.db, session)

    def test_compressed_event_when_decoder_available(self):
        try:
            import zstandard
        except ImportError:
            self.skipTest("optional zstandard is not installed")
        raw = json.dumps(event()).encode()
        self.con.execute(
            "INSERT INTO transcript_events VALUES (?, ?, NULL, ?, ?, ?)",
            ("s1", 0, zstandard.ZstdCompressor().compress(raw), len(raw), "user"),
        )
        self.con.commit()
        self.assertEqual(read_session(self.db, "s1")[0]["text"], "fictional message")


if __name__ == "__main__":
    unittest.main()
