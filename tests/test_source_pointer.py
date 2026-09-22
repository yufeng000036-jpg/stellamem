"""P0-C: source pointer lifecycle must degrade safely, never fabricate provenance.

Pointers replace copying whole conversations. These tests cover the OpenClaw
shapes named in the plan: normal session, reset/rotated session, renamed archive,
multiple session files, late message and same-minute messages. When a pointer can
no longer be resolved the reader must raise, not silently return invented text.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))
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


class SourcePointerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sessions = self.root / "sessions"
        self.sessions.mkdir()
        self.memory = self.root / "memory"
        self.memory.mkdir()
        self.config = {"sessions_dir": str(self.sessions)}

    def tearDown(self):
        self.tmp.cleanup()

    def write_manifest(self, pointers):
        daily = self.memory / "2026-01-01.md"
        daily.write_text("summary only; original lives in the host session\n", encoding="utf-8")
        daily.with_suffix(".sources.json").write_text(
            json.dumps({"schema_version": 2, "pointers": pointers}), encoding="utf-8"
        )
        return daily

    def test_normal_session_resolves_full_text(self):
        (self.sessions / "session.jsonl").write_text(
            entry("complete original text"), encoding="utf-8"
        )
        daily = self.write_manifest([pointer("session.jsonl", "complete original text")])
        messages = read_daily(daily, self.config)
        self.assertEqual(messages[0]["text"], "complete original text")
        self.assertEqual(messages[0]["provenance"]["precision"], "message")

    def test_same_minute_messages_are_distinct(self):
        rows = [
            entry("first", ts="2026-01-01T15:32:01Z", mid="a"),
            entry("second", ts="2026-01-01T15:32:01Z", mid="b"),
        ]
        (self.sessions / "session.jsonl").write_text("\n".join(rows), encoding="utf-8")
        daily = self.write_manifest(
            [
                pointer("session.jsonl", "first", idx=0, mid="a"),
                pointer("session.jsonl", "second", idx=1, mid="b"),
            ]
        )
        messages = read_daily(daily, self.config)
        self.assertEqual([m["text"] for m in messages], ["first", "second"])

    def test_reset_and_renamed_archive_session_still_resolve(self):
        # OpenClaw may rotate session.jsonl to session.jsonl.reset.<stamp>.
        (self.sessions / "session.jsonl.reset.2026").write_text(
            entry("survives reset"), encoding="utf-8"
        )
        daily = self.write_manifest([pointer("session.jsonl", "survives reset")])
        self.assertEqual(read_daily(daily, self.config)[0]["text"], "survives reset")

    def test_multiple_session_files_resolve_by_name(self):
        (self.sessions / "alpha.jsonl").write_text(entry("from alpha", mid="a"), encoding="utf-8")
        (self.sessions / "beta.jsonl").write_text(entry("from beta", mid="b"), encoding="utf-8")
        daily = self.write_manifest(
            [
                pointer("alpha.jsonl", "from alpha", mid="a"),
                pointer("beta.jsonl", "from beta", mid="b"),
            ]
        )
        self.assertEqual(
            [m["text"] for m in read_daily(daily, self.config)], ["from alpha", "from beta"]
        )

    def test_missing_source_raises_and_never_fabricates(self):
        daily = self.write_manifest([pointer("gone.jsonl", "the original text")])
        with self.assertRaises(SourceError) as ctx:
            read_daily(daily, self.config)
        self.assertIn("missing or changed", str(ctx.exception))

    def test_changed_content_hash_raises(self):
        (self.sessions / "session.jsonl").write_text(entry("tampered body"), encoding="utf-8")
        daily = self.write_manifest([pointer("session.jsonl", "the original text")])
        with self.assertRaises(SourceError):
            read_daily(daily, self.config)

    def test_speaker_mismatch_raises(self):
        (self.sessions / "session.jsonl").write_text(
            entry("original", role="assistant"), encoding="utf-8"
        )
        daily = self.write_manifest([pointer("session.jsonl", "original", role="user")])
        with self.assertRaises(SourceError):
            read_daily(daily, self.config)

    def test_entry_index_out_of_range_raises(self):
        (self.sessions / "session.jsonl").write_text(entry("only one"), encoding="utf-8")
        daily = self.write_manifest([pointer("session.jsonl", "only one", idx=99)])
        with self.assertRaises(SourceError):
            read_daily(daily, self.config)

    def test_invalid_manifest_rejected(self):
        daily = self.memory / "2026-01-01.md"
        daily.write_text("body", encoding="utf-8")
        daily.with_suffix(".sources.json").write_text(
            json.dumps({"schema_version": 1}), encoding="utf-8"
        )
        with self.assertRaises(SourceError):
            read_daily(daily, self.config)

    def test_manifest_requires_sessions_dir(self):
        (self.sessions / "session.jsonl").write_text(entry("x"), encoding="utf-8")
        daily = self.write_manifest([pointer("session.jsonl", "x")])
        with self.assertRaises(SourceError):
            read_daily(daily, {})

    def test_path_escape_in_filename_rejected(self):
        daily = self.write_manifest([pointer("../outside.jsonl", "x")])
        with self.assertRaises(SourceError):
            read_daily(daily, self.config)


if __name__ == "__main__":
    unittest.main()
