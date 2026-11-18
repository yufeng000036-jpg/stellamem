"""Experimental, read-only OpenClaw JSONL transcript reader.

Sibling of ``openclaw_sqlite_source`` for hosts whose OpenClaw version still
stores transcripts as JSONL session files (one ``<uuid>.jsonl`` per session
in a flat sessions directory) instead of the SQLite ``transcript_events``
table. Not wired to ingestion or a host hook by itself; the opt-in loop in
``openclaw_sqlite_ingest`` drives it through the same allowlist, pointer
store, tombstone and deletion-propagation machinery as the SQLite source.
Callers must explicitly select a sessions directory and one session file;
output is in-memory only. Unsupported or incomplete events abort the entire
read, exactly as in the SQLite prototype.

Format (verified by read-only inspection of an OpenClaw 2026.10.10 host;
see the pull request for the full recon notes):

- Line 0 is a ``{"type": "session", ...}`` header; every line is one JSON
  object with ``id``, ``parentId``, ``timestamp`` and ``type``.
- ``type == "message"`` lines carry ``message: {role, content, ...}``.
  Roles seen in the wild: ``user``, ``assistant`` and ``toolResult``;
  only ``user``/``assistant`` are transcript messages here, the rest are
  skipped (there is no role index to cross-check, unlike the SQLite
  table). Other event types (``session``, ``model_change``,
  ``thinking_level_change``, ``custom``, ``custom_message``,
  ``compaction``) are skipped.
- ``content`` is a string or a list of parts. A message is readable only
  when every part is a text part (the shared ``_message_text`` rule of the
  SQLite reader): ``toolCall`` parts, ``image`` parts and any other
  non-text shape abort the whole read. Real agent sessions use tool calls
  heavily, so many real files fail closed here; widening that policy is a
  separate decision, not something this reader smuggles in.

Message identity:

- Preferred: the event's own ``id`` -- every event line in the inspected
  host carries a short stable id, unique within its file. Duplicates
  among kept messages abort the read.
- Fallback: when a message event has no usable ``id``, the pointer's
  message_id is derived as ``line-<seq>`` and the identity rests on the
  triple (session file, line sequence, content_hash).
- ``sequence`` is the physical line number over *all* lines (header and
  skipped events included), and ``event_hash`` is the SHA-256 of the raw
  line bytes (line terminator excluded). Appends never move earlier
  identities. A mid-file edit changes the edited line's hashes; a
  mid-file insert or delete shifts later sequences, so later pointers
  stop resolving and are flagged ``needs_review`` by the ingest loop --
  fail-closed, never a silent re-binding to the wrong message.

File selection rules (enforced here, not just by the allowlist):

- ``session_id`` is a bare file name ending in ``.jsonl``; path
  separators, dotfiles and symlink escapes out of the granted directory
  are refused.
- Names carrying a ``.deleted`` marker (``<uuid>.deleted.jsonl``,
  ``<uuid>.jsonl.deleted.<stamp>``) are refused unconditionally: those
  are sessions the user deleted, and no allowlist entry can resurrect
  them through this reader.
- ``<uuid>.trajectory.jsonl`` files share the directory and parse with
  the same reader, but nothing enumerates the directory: a trajectory
  file is read only when an operator names it explicitly and the
  allowlist lists that exact name.
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path

from openclaw_sqlite_source import (
    MAX_EVENT_BYTES,
    MAX_SESSION_BYTES,
    MAX_SESSION_EVENTS,
    _message_text,
)
from source_reader import SourceError

PROVIDER = "openclaw-jsonl"


def _validate_session_name(session_id):
    if not isinstance(session_id, str) or not session_id.strip():
        raise SourceError("explicit session id required")
    if (
        Path(session_id).name != session_id
        or "/" in session_id
        or "\\" in session_id
        or session_id.startswith(".")
    ):
        raise SourceError("OpenClaw JSONL session id must be a bare file name")
    if not session_id.endswith(".jsonl"):
        raise SourceError("OpenClaw JSONL session id must name a .jsonl file")
    if ".deleted" in session_id:
        raise SourceError(
            "OpenClaw session file is marked deleted; deleted sessions are never read"
        )
    return session_id


def _resolve_session_file(directory, session_id):
    _validate_session_name(session_id)
    try:
        root = Path(directory).resolve(strict=True)
    except (OSError, TypeError, ValueError):
        raise SourceError("invalid OpenClaw sessions directory") from None
    if not root.is_dir():
        raise SourceError("OpenClaw sessions path is not a directory")
    try:
        path = (root / session_id).resolve(strict=True)
    except (OSError, TypeError, ValueError):
        raise SourceError("OpenClaw session file does not exist") from None
    if path.parent != root or not path.is_file():
        raise SourceError("OpenClaw session file escapes the sessions directory")
    return path


def read_session(directory, session_id):
    """Read one explicit JSONL session consistently; never writes anything."""
    path = _resolve_session_file(directory, session_id)
    try:
        data = path.read_bytes()
    except OSError:
        raise SourceError("unable to read OpenClaw session file") from None
    if len(data) > MAX_SESSION_BYTES:
        raise SourceError("session exceeds byte limit; no partial read")
    raw_lines = data.split(b"\n")
    if raw_lines and raw_lines[-1] == b"":
        raw_lines.pop()  # a single trailing newline is not an event
    messages = []
    seen_ids = set()
    count = 0
    for seq, raw in enumerate(raw_lines):
        if raw.endswith(b"\r"):
            raw = raw[:-1]
        if not raw.strip():
            raise SourceError("blank line in transcript; no partial read")
        if len(raw) > MAX_EVENT_BYTES:
            raise SourceError("transcript event exceeds size limit")
        count += 1
        if count > MAX_SESSION_EVENTS:
            raise SourceError("session exceeds event limit; no partial read")
        event_hash = hashlib.sha256(raw).hexdigest()
        try:
            event = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError):
            raise SourceError("invalid transcript JSON") from None
        if not isinstance(event, dict):
            raise SourceError("invalid transcript event")
        if event.get("type") != "message":
            continue
        message = event.get("message")
        if not isinstance(message, dict):
            raise SourceError("invalid transcript message")
        role = message.get("role")
        if role not in {"user", "assistant"}:
            continue
        event_id = event.get("id")
        if event_id is not None and (not isinstance(event_id, str) or not event_id.strip()):
            raise SourceError("invalid message id")
        if event_id is not None:
            if event_id in seen_ids:
                raise SourceError("duplicate message id")
            seen_ids.add(event_id)
        timestamp = event.get("timestamp")
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("timezone required")
        except (AttributeError, TypeError, ValueError):
            raise SourceError("invalid message timestamp") from None
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
                    "provider": PROVIDER,
                    "precision": "message",
                    "session_id": session_id,
                    "message_id": event_id if event_id is not None else f"line-{seq}",
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


def resolve_pointer(directory, pointer):
    """Re-read a single previously observed message and reject any drift.

    `directory` and `pointer` must come from an explicitly authorized
    caller; matching hashes and IDs do not prove subject ownership or
    authorization.
    """
    if not isinstance(pointer, dict) or pointer.get("provider") != PROVIDER:
        raise SourceError("invalid JSONL source pointer")
    if pointer.get("precision") != "message":
        raise SourceError("invalid JSONL source precision")
    required = (
        "session_id",
        "sequence",
        "message_id",
        "speaker",
        "timestamp",
        "content_hash",
        "event_hash",
    )
    if any(key not in pointer for key in required):
        raise SourceError("incomplete JSONL source pointer")
    candidates = read_session(directory, pointer["session_id"])
    for message in candidates:
        observed = message["provenance"]
        if observed["sequence"] != pointer["sequence"]:
            continue
        if any(observed[key] != pointer[key] for key in required):
            raise SourceError("JSONL source pointer changed")
        return message
    raise SourceError("JSONL source pointer missing")
