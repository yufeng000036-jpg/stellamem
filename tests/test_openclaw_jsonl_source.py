"""Offline synthetic tests for the read-only OpenClaw JSONL source.

Fixtures imitate the on-disk shape verified against an OpenClaw 2026.10.10
host (header line, event lines with short ids, message events whose content
is a string or a list of parts). All content is fictional; no real OpenClaw
data is touched.
"""

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))
import openclaw_jsonl_source as jsonl_source
from openclaw_jsonl_source import read_session, resolve_pointer
from source_reader import SourceError

SESSION = "11111111-2222-3333-4444-555555555555.jsonl"
TS = "2026-01-01T00:00:00.000Z"


def header(session_uuid="11111111-2222-3333-4444-555555555555"):
    return {"type": "session", "id": session_uuid, "timestamp": TS, "version": 1}


def note(kind="model_change", mid="n1"):
    return {"type": kind, "id": mid, "parentId": None, "timestamp": TS}


def message(mid="m1", role="user", content="fictional message", ts=TS):
    return {
        "type": "message",
        "id": mid,
        "parentId": None,
        "timestamp": ts,
        "message": {"role": role, "content": content},
    }


class JsonlFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, lines, name=SESSION):
        path = self.dir / name
        text = "".join(
            (line if isinstance(line, str) else json.dumps(line)) + "\n" for line in lines
        )
        path.write_text(text, encoding="utf-8")
        return path

    def basic(self):
        return self.write(
            [
                header(),
                note(),
                message(),
                message(
                    "m2",
                    "assistant",
                    [{"type": "text", "text": "fictional reply"}],
                ),
            ]
        )


class ReadTests(JsonlFixture):
    def test_reads_user_and_assistant_with_full_provenance(self):
        self.basic()
        messages = read_session(self.dir, SESSION)
        self.assertEqual([m["text"] for m in messages], ["fictional message", "fictional reply"])
        self.assertEqual([m["speaker"] for m in messages], ["user", "assistant"])
        first = messages[0]["provenance"]
        self.assertEqual(first["provider"], "openclaw-jsonl")
        self.assertEqual(first["session_id"], SESSION)
        self.assertEqual(first["message_id"], "m1")
        self.assertEqual(first["sequence"], 2)  # header and note lines consume 0 and 1
        self.assertEqual(
            first["content_hash"],
            hashlib.sha256("fictional message".encode("utf-8")).hexdigest(),
        )
        raw_line = (self.dir / SESSION).read_bytes().split(b"\n")[2]
        self.assertEqual(first["event_hash"], hashlib.sha256(raw_line).hexdigest())
        self.assertEqual(messages[1]["provenance"]["sequence"], 3)

    def test_skips_non_message_events_and_tool_results(self):
        self.write(
            [
                header(),
                note("compaction"),
                note("custom_message"),
                message("t1", "toolResult", "tool output"),
                message(),
            ]
        )
        messages = read_session(self.dir, SESSION)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["provenance"]["message_id"], "m1")
        self.assertEqual(messages[0]["provenance"]["sequence"], 4)

    def test_missing_id_falls_back_to_line_identity(self):
        orphan = message()
        del orphan["id"]
        self.write([header(), orphan])
        messages = read_session(self.dir, SESSION)
        self.assertEqual(messages[0]["provenance"]["message_id"], "line-1")

    def test_duplicate_message_id_fails_closed(self):
        self.write([header(), message(), message()])
        with self.assertRaises(SourceError):
            read_session(self.dir, SESSION)

    def test_non_text_content_fails_closed(self):
        for content in (
            [{"type": "image", "data": "fictional"}],
            [{"type": "toolCall", "name": "fictional"}],
            [{"type": "text", "text": "ok"}, {"type": "toolCall", "name": "fictional"}],
        ):
            self.write([header(), message("m1", "assistant", content)])
            with self.assertRaises(SourceError, msg=repr(content)):
                read_session(self.dir, SESSION)

    def test_invalid_lines_fail_closed(self):
        self.write([header(), "{not json"])
        with self.assertRaises(SourceError):
            read_session(self.dir, SESSION)
        self.write([header(), message(ts="2026-01-01 00:00:00")])
        with self.assertRaises(SourceError):
            read_session(self.dir, SESSION)
        self.write([header(), "", message()])
        with self.assertRaises(SourceError):
            read_session(self.dir, SESSION)

    def test_limits_fail_closed(self):
        self.basic()
        with patch.object(jsonl_source, "MAX_EVENT_BYTES", 16):
            with self.assertRaises(SourceError):
                read_session(self.dir, SESSION)
        with patch.object(jsonl_source, "MAX_SESSION_EVENTS", 2):
            with self.assertRaises(SourceError):
                read_session(self.dir, SESSION)
        with patch.object(jsonl_source, "MAX_SESSION_BYTES", 32):
            with self.assertRaises(SourceError):
                read_session(self.dir, SESSION)

    def test_header_only_file_reads_as_empty(self):
        self.write([header()])
        self.assertEqual(read_session(self.dir, SESSION), [])

    def test_missing_and_empty_files_fail(self):
        with self.assertRaises(SourceError):
            read_session(self.dir, SESSION)
        (self.dir / SESSION).write_bytes(b"")
        with self.assertRaises(SourceError):
            read_session(self.dir, SESSION)

    def test_trailing_newline_and_crlf_are_tolerated(self):
        path = self.basic()
        data = path.read_bytes()
        path.write_bytes(data.replace(b"\n", b"\r\n"))
        messages = read_session(self.dir, SESSION)
        self.assertEqual(len(messages), 2)
        raw_line = data.split(b"\n")[2]
        self.assertEqual(
            messages[0]["provenance"]["event_hash"], hashlib.sha256(raw_line).hexdigest()
        )


class SessionNameTests(JsonlFixture):
    def test_deleted_names_are_refused_even_when_present(self):
        for name in (
            "11111111-2222-3333-4444-555555555555.deleted.jsonl",
            "11111111-2222-3333-4444-555555555555.jsonl.deleted.2026-01-01T00-00-00.000Z",
        ):
            self.write([header(), message()], name=name)
            with self.assertRaises(SourceError, msg=name):
                read_session(self.dir, name)

    def test_names_must_be_bare_jsonl_files(self):
        for name in ("../x.jsonl", "sub/x.jsonl", ".hidden.jsonl", "x.txt", "x.jsonl "):
            with self.assertRaises(SourceError, msg=name):
                read_session(self.dir, name)

    def test_trajectory_name_reads_only_when_named(self):
        name = "11111111-2222-3333-4444-555555555555.trajectory.jsonl"
        self.write([header(), message()], name=name)
        messages = read_session(self.dir, name)
        self.assertEqual(len(messages), 1)


class ResolvePointerTests(JsonlFixture):
    def pointer(self):
        self.basic()
        return read_session(self.dir, SESSION)[0]["provenance"]

    def test_roundtrip(self):
        pointer = self.pointer()
        found = resolve_pointer(self.dir, pointer)
        self.assertEqual(found["text"], "fictional message")

    def test_edit_is_detected_as_changed(self):
        pointer = self.pointer()
        self.write(
            [
                header(),
                note(),
                message(content="edited text"),
                message("m2", "assistant", [{"type": "text", "text": "fictional reply"}]),
            ]
        )
        with self.assertRaises(SourceError):
            resolve_pointer(self.dir, pointer)

    def test_removal_is_detected_as_missing(self):
        pointer = self.pointer()
        self.write([header(), note(), message("m2", "assistant", "fictional reply")])
        with self.assertRaises(SourceError):
            resolve_pointer(self.dir, pointer)

    def test_foreign_pointer_is_rejected(self):
        pointer = self.pointer()
        pointer = dict(pointer, provider="openclaw-sqlite")
        with self.assertRaises(SourceError):
            resolve_pointer(self.dir, pointer)


if __name__ == "__main__":
    unittest.main()
