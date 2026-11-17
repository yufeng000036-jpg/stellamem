"""Offline synthetic tests for the non-integrated SQLite reader prototype."""

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))
from openclaw_sqlite_source import read_session
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
