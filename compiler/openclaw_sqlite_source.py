"""Experimental, read-only OpenClaw SQLite transcript reader.

Not wired to ingestion or a host hook. Callers must explicitly select a database
and a session; output is in-memory only. Unsupported or incomplete events abort
the entire read. A verified pointer resolver and host-version contract are still
required before this can be used for durable memories.
"""

import hashlib
import io
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from source_reader import SourceError

MAX_EVENT_BYTES = 4 * 1024 * 1024
MAX_SESSION_EVENTS = 50000
MAX_SESSION_BYTES = 64 * 1024 * 1024


def _decode_event(text, compressed, declared):
    if (text is None) == (compressed is None):
        raise SourceError("invalid transcript event storage")
    if compressed is not None:
        if not isinstance(declared, int) or not 0 <= declared <= MAX_EVENT_BYTES:
            raise SourceError("invalid compressed event size")
        try:
            import zstandard
        except ImportError:
            raise SourceError("compressed transcript requires optional zstandard") from None
        try:
            with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(compressed)) as stream:
                payload = stream.read(MAX_EVENT_BYTES + 1)
        except Exception:
            raise SourceError("invalid compressed transcript event") from None
        if len(payload) != declared or len(payload) > MAX_EVENT_BYTES:
            raise SourceError("compressed transcript size mismatch")
    else:
        if not isinstance(text, str):
            raise SourceError("invalid transcript text")
        payload = text.encode("utf-8")
        if len(payload) > MAX_EVENT_BYTES:
            raise SourceError("transcript event exceeds size limit")
    try:
        event = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeError):
        raise SourceError("invalid transcript JSON") from None
    if not isinstance(event, dict):
        raise SourceError("invalid transcript event")
    return event, hashlib.sha256(payload).hexdigest()


def _message_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list) and all(
        isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)
        for part in content
    ):
        return " ".join(part["text"] for part in content)
    raise SourceError("unsupported message content; no partial read")


def read_session(database, session_id):
    """Read one explicit session consistently; never writes a manifest or data."""
    if not isinstance(session_id, str) or not session_id.strip():
        raise SourceError("explicit session id required")
    try:
        path = Path(database).resolve(strict=True)
    except (OSError, TypeError, ValueError):
        raise SourceError("invalid transcript database path") from None
    connection = None
    try:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        rows = connection.execute(
            "SELECT seq, event_json, event_zstd, event_utf8_bytes, message_role "
            "FROM transcript_events WHERE session_id = ? ORDER BY seq LIMIT ?",
            (session_id, MAX_SESSION_EVENTS + 1),
        )
        messages = []
        seen_ids = set()
        total_bytes = 0
        count = 0
        for seq, text, compressed, declared, indexed_role in rows:
            count += 1
            if count > MAX_SESSION_EVENTS:
                raise SourceError("session exceeds event limit; no partial read")
            event, event_hash = _decode_event(text, compressed, declared)
            total_bytes += declared if compressed is not None else len(text.encode("utf-8"))
            if total_bytes > MAX_SESSION_BYTES:
                raise SourceError("session exceeds byte limit; no partial read")
            if event.get("type") != "message":
                if indexed_role in {"user", "assistant"}:
                    raise SourceError("transcript role index mismatch")
                continue
            message = event.get("message")
            if not isinstance(message, dict):
                raise SourceError("invalid transcript message")
            role = message.get("role")
            if role not in {"user", "assistant"}:
                if indexed_role in {"user", "assistant"}:
                    raise SourceError("transcript role index mismatch")
                continue
            if indexed_role != role:
                raise SourceError("transcript role index mismatch")
            event_id = event.get("id")
            timestamp = event.get("timestamp")
            if not isinstance(event_id, str) or not event_id or event_id in seen_ids:
                raise SourceError("missing or duplicate message id")
            try:
                parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    raise ValueError("timezone required")
            except (AttributeError, TypeError, ValueError):
                raise SourceError("invalid message timestamp") from None
            seen_ids.add(event_id)
            content = _message_text(message.get("content"))
            if not content:
                continue
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            messages.append(
                {
                    "text": content,
                    "speaker": role,
                    "timestamp": timestamp,
                    "offset": 0,
                    "provenance": {
                        "provider": "openclaw-sqlite",
                        "precision": "message",
                        "session_id": session_id,
                        "message_id": event_id,
                        "sequence": seq,
                        "speaker": role,
                        "timestamp": timestamp,
                        "content_hash": content_hash,
                        "event_hash": event_hash,
                    },
                }
            )
        if count == 0:
            raise SourceError("session has no transcript events")
        return messages
    except sqlite3.Error:
        raise SourceError("unable to read OpenClaw transcript database") from None
    finally:
        if connection is not None:
            connection.close()
