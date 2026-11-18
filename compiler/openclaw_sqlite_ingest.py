"""Opt-in ingestion loop for the OpenClaw SQLite transcript source.

Status: experimental and NOT wired to any production ingestion entry point.
Nothing in the compiler pipeline, the store, or the API imports this module;
the only way in is an operator (or a test) calling it explicitly. It turns
the one-shot read-only prototype in ``openclaw_sqlite_source`` into a closed
loop with four pieces the prototype deliberately lacked:

1. Persistent source pointers. Every ingested message is registered in a
   pointer store keyed by a caller-supplied record id. The stored pointer
   carries the full source identity -- (db_id, session_id, message_id,
   sequence, content_hash) plus the event hash, speaker and timestamp -- so
   any record can be traced back to the exact source message it came from.
   ``ledger_identity`` projects a pointer onto the identity triple used by
   the PR #122 source ledger (``session_file`` + ``message_id`` +
   ``content_hash``); for this provider the db_id plays the session_file
   role (the source-container slot), rendered as ``<db_id>.openclaw-sqlite``,
   so tombstones recorded through :func:`record_source_deletion` are real
   #122 ledger records, not a parallel reimplementation.

2. Session ownership authorization. Ingestion is default-deny: without an
   allowlist file there is no configuration and every ingest raises
   SourceError. The allowlist names databases by a stable logical ``db_id``
   bound to one resolved path, each with an explicit session list or ``"*"``.
   Grants and revokes go through the functions here (never by editing the
   reader's mind at call time) and every change appends a hash-chained event
   to the audit log next to the allowlist; a tampered audit chain refuses
   further changes instead of being extended. Editing the allowlist file
   directly bypasses the audit log -- the file is operator-controlled, and
   that residual is documented rather than hidden.

3. Deletion propagation. :func:`check_pointers` re-resolves every registered
   pointer: a message that vanished (``source_missing``), changed bytes
   (``source_changed``), lost its authorization (``authorization_revoked``)
   or is tombstoned in the source ledger (``source_tombstoned``) gets its
   record flagged ``needs_review``. Flagging is one-way: a later check never
   reactivates a flagged record, even if the bytes reappear -- only the
   explicit :func:`reinstate_pointer` can, and it refuses while a tombstone
   stands. Deletion wins over every copy, exactly as in #122. Propagation is
   pull-based: it happens when an operator runs a check, not on a schedule.

4. Concurrency and dependencies. All source reads go through the
   prototype's ``read_session``: one read-only connection (``mode=ro``,
   ``query_only``) inside a single transaction, which is a snapshot read.
   Against a WAL database a reader sees the last committed state as of
   transaction start and does not block the writer; against a rollback-
   journal database a concurrent write transaction can make the read fail
   closed with SourceError (retrying is the caller's decision). A WAL
   database that was not cleanly closed may need ``-shm``/``-wal`` recovery
   access to open read-only; if that fails we raise -- this module never
   opens a source read-write, never checkpoints, and never copies or
   mutates the source file. ``zstandard`` stays an optional, lazily imported
   dependency of the reader: it is only imported when a compressed row is
   actually decoded, and its absence raises the reader's SourceError
   unchanged, so a compressed session fails whole instead of silently
   skipping rows.

The loop is provider-generic. ``openclaw-sqlite`` containers are single
database files; ``openclaw-jsonl`` containers are sessions directories
read through ``openclaw_jsonl_source`` (for hosts whose OpenClaw version
still stores JSONL session files). Allowlist entries record their
provider at grant time (entries written before providers existed are
SQLite), pointers carry the provider, and the #122 ledger identity of a
JSONL pointer is its bare session file name. The JSONL reader refuses
``.deleted``-marked session files unconditionally, so a granted sessions
directory can never resurrect a session the user deleted. Under the
user's 2026-11-18 content-policy ruling the JSONL reader extracts the
text parts of mixed-content messages instead of failing the whole
session; the resulting pointers carry the reader's
``degraded_extraction``/``skipped_part_types`` annotation when present,
and :func:`ingest_session` reports the reader's skip counts
(``skipped_no_text``/``skipped_tool_result``) as ``source_stats`` for
JSONL sessions. Identity, hashing and drift comparison are unchanged:
the event hash still covers the raw source line, so editing a skipped
part flags ``source_changed`` exactly like editing extracted text.

What is still missing before this may be enabled for real data:

- The user's explicit authorization of real databases and sessions. The
  allowlist can only be populated by an operator grant; nothing here reads,
  scans for, or guesses at real OpenClaw data files.
- A host-version contract for the OpenClaw transcript schema. The reader
  fails closed on drift, but no version negotiation exists yet.
- Wiring from registered record ids into MemoryStore records and a review
  surface for ``needs_review`` entries. This module tracks record-id
  references and their source health only; it creates no memory records.
- A decision on check cadence (manual, hook, or schedule). Until then
  deletion propagation is only as fresh as the last manual check.

Pointers identify source messages; matching hashes do not prove subject
ownership -- the allowlist grant is the ownership assertion, and it is made
by the operator, not inferred from content.
"""

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import source_ledger
from file_lock import atomic_write_text_locked, cross_platform_lock
from openclaw_jsonl_source import read_session as _read_jsonl_session
from openclaw_jsonl_source import read_session_with_stats as _read_jsonl_session_with_stats
from openclaw_sqlite_source import read_session
from source_reader import SourceError

ALLOWLIST_SCHEMA_VERSION = 1
POINTER_STORE_SCHEMA_VERSION = 1
_DB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_POINTER_FIELDS = (
    "session_id",
    "message_id",
    "sequence",
    "speaker",
    "timestamp",
    "content_hash",
    "event_hash",
)
# Optional provenance annotations copied into the pointer when the reader
# produced them. They never take part in identity or drift comparison;
# ``degraded_extraction``/``skipped_part_types`` record that a JSONL
# message's text was extracted from mixed content under the 2026-11-18
# content policy, so the stored record itself shows the read was partial.
_POINTER_OPTIONAL_FIELDS = ("degraded_extraction", "skipped_part_types")
REVIEW_REASONS = (
    "source_missing",
    "source_changed",
    "source_tombstoned",
    "authorization_revoked",
)
PROVIDERS = ("openclaw-sqlite", "openclaw-jsonl")


def _entry_provider(entry):
    """Provider of an allowlist entry; pre-provider entries are SQLite."""
    provider = entry.get("provider", "openclaw-sqlite")
    if provider not in PROVIDERS:
        raise SourceError("OpenClaw ingest allowlist entry has an unknown provider")
    return provider


def _now():
    return datetime.now(timezone.utc).isoformat()


def zstandard_available():
    """True when the optional decoder for compressed transcript rows exists."""
    try:
        import zstandard  # noqa: F401
    except ImportError:
        return False
    return True


# --------------------------------------------------------------------------
# Authorization: allowlist + hash-chained audit log
# --------------------------------------------------------------------------


def default_audit_path(allowlist_path):
    path = Path(allowlist_path)
    return path.with_name(path.name + ".audit.jsonl")


def _validate_db_id(db_id):
    if not isinstance(db_id, str) or not _DB_ID_RE.match(db_id):
        raise SourceError("invalid OpenClaw database id")


def _validate_actor(actor):
    if not isinstance(actor, str) or not actor.strip():
        raise SourceError("an actor is required for audited authorization changes")


def _validate_sessions(sessions):
    if sessions == "*":
        return "*"
    if isinstance(sessions, (str, bytes)) or not hasattr(sessions, "__iter__"):
        raise SourceError("sessions must be '*' or a list of session ids")
    result = []
    for session_id in sessions:
        if not isinstance(session_id, str) or not session_id.strip():
            raise SourceError("allowlisted session ids must be non-empty strings")
        if session_id not in result:
            result.append(session_id)
    if not result:
        raise SourceError("granting zero sessions is a revoke, not a grant")
    return result


def _read_allowlist_doc(path):
    """Return the parsed allowlist document, None when unconfigured.

    A present but malformed allowlist is a hard error everywhere: default
    deny must never silently widen into 'no rules' or narrow into a crash
    that an operator works around by deleting the file.
    """
    path = Path(path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SourceError("OpenClaw ingest allowlist is unreadable") from None
    if not isinstance(data, dict) or data.get("schema_version") != ALLOWLIST_SCHEMA_VERSION:
        raise SourceError("OpenClaw ingest allowlist has an unsupported schema")
    databases = data.get("databases")
    if not isinstance(databases, dict):
        raise SourceError("OpenClaw ingest allowlist is malformed")
    for db_id, entry in databases.items():
        _validate_db_id(db_id)
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise SourceError("OpenClaw ingest allowlist entry is malformed")
        sessions = entry.get("sessions")
        if sessions != "*" and (
            not isinstance(sessions, list)
            or not all(isinstance(item, str) and item for item in sessions)
        ):
            raise SourceError("OpenClaw ingest allowlist entry is malformed")
        provider = entry.get("provider", "openclaw-sqlite")
        if provider not in PROVIDERS:
            raise SourceError("OpenClaw ingest allowlist entry is malformed")
    return data


def load_allowlist(path):
    """Return the allowlist's database map, or None when unconfigured."""
    doc = _read_allowlist_doc(path)
    return None if doc is None else doc["databases"]


def require_authorized(allowlist, db_id, session_id):
    """Return the allowlist entry for (db_id, session_id) or raise.

    ``allowlist`` is the map returned by :func:`load_allowlist`; None means
    no configuration exists and everything is denied.
    """
    _validate_db_id(db_id)
    if allowlist is None:
        raise SourceError(
            "no OpenClaw ingest allowlist is configured; ingestion is denied by default"
        )
    entry = allowlist.get(db_id)
    if entry is None:
        raise SourceError(f"OpenClaw database {db_id!r} is not allowlisted for ingestion")
    sessions = entry["sessions"]
    if sessions != "*" and session_id not in sessions:
        raise SourceError(f"OpenClaw session {session_id!r} is not allowlisted for ingestion")
    return entry


def _canonical(body):
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _audit_hash(body):
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def load_audit(path):
    """Return the verified audit events; a missing log means no events."""
    path = Path(path)
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        raise SourceError("OpenClaw ingest audit log is unreadable") from None
    events = []
    prev = None
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            raise SourceError("OpenClaw ingest audit log failed verification") from None
        if not isinstance(event, dict) or event.get("seq") != len(events):
            raise SourceError("OpenClaw ingest audit log failed verification")
        if event.get("prev") != prev:
            raise SourceError("OpenClaw ingest audit log failed verification")
        body = {key: value for key, value in event.items() if key != "hash"}
        if event.get("hash") != _audit_hash(body):
            raise SourceError("OpenClaw ingest audit log failed verification")
        prev = event["hash"]
        events.append(event)
    return events


def _append_audit(audit_path, *, actor, action, db_id, detail, recorded_at):
    audit_path = Path(audit_path)
    with cross_platform_lock(audit_path):
        events = load_audit(audit_path)
        event = {
            "seq": len(events),
            "prev": events[-1]["hash"] if events else None,
            "at": recorded_at,
            "actor": actor,
            "action": action,
            "db_id": db_id,
            "detail": detail,
        }
        event["hash"] = _audit_hash(event)
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with open(audit_path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(_canonical(event) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    return event


def _mutate_allowlist(
    allowlist_path, audit_path, *, actor, action, db_id, detail, mutate, recorded_at
):
    _validate_actor(actor)
    _validate_db_id(db_id)
    audit_path = Path(audit_path) if audit_path else default_audit_path(allowlist_path)
    load_audit(audit_path)  # refuse to extend a chain that no longer verifies
    allowlist_path = Path(allowlist_path)
    with cross_platform_lock(allowlist_path):
        doc = _read_allowlist_doc(allowlist_path) or {
            "schema_version": ALLOWLIST_SCHEMA_VERSION,
            "databases": {},
        }
        mutate(doc["databases"])
        text = json.dumps(doc, ensure_ascii=False, indent=2)
        atomic_write_text_locked(allowlist_path, text + "\n")
    return _append_audit(
        audit_path,
        actor=actor,
        action=action,
        db_id=db_id,
        detail=detail,
        recorded_at=recorded_at or _now(),
    )


def grant_database(
    allowlist_path,
    *,
    db_id,
    database,
    sessions="*",
    actor,
    audit_path=None,
    recorded_at=None,
    provider=None,
):
    """Allowlist one source container and the given sessions; audited.

    For ``openclaw-sqlite`` the container is one database file; for
    ``openclaw-jsonl`` it is the sessions directory holding the session
    files. ``provider`` defaults to whichever the path shape implies.
    A directory named ``deleted`` can never be granted for JSONL: it
    holds sessions the user deleted, and deletion wins.
    """
    try:
        resolved = Path(database).resolve(strict=True)
    except (OSError, TypeError, ValueError):
        raise SourceError("allowlisted database file does not exist") from None
    if provider is None:
        provider = "openclaw-jsonl" if resolved.is_dir() else "openclaw-sqlite"
    if provider not in PROVIDERS:
        raise SourceError("unknown OpenClaw source provider")
    if provider == "openclaw-sqlite":
        if not resolved.is_file():
            raise SourceError("allowlisted database path is not a file")
    else:
        if not resolved.is_dir():
            raise SourceError("allowlisted JSONL path is not a sessions directory")
        if resolved.name.casefold() == "deleted":
            raise SourceError("a deleted-sessions directory cannot be allowlisted")
    sessions = _validate_sessions(sessions)

    def mutate(databases):
        databases[db_id] = {
            "path": str(resolved),
            "sessions": sessions,
            "provider": provider,
        }

    return _mutate_allowlist(
        allowlist_path,
        audit_path,
        actor=actor,
        action="grant_database",
        db_id=db_id,
        detail={"path": str(resolved), "sessions": sessions, "provider": provider},
        mutate=mutate,
        recorded_at=recorded_at,
    )


def revoke_database(allowlist_path, *, db_id, actor, audit_path=None, recorded_at=None):
    """Remove a database from the allowlist entirely; audited."""

    def mutate(databases):
        if db_id not in databases:
            raise SourceError(f"OpenClaw database {db_id!r} is not allowlisted")
        del databases[db_id]

    return _mutate_allowlist(
        allowlist_path,
        audit_path,
        actor=actor,
        action="revoke_database",
        db_id=db_id,
        detail={},
        mutate=mutate,
        recorded_at=recorded_at,
    )


def grant_session(allowlist_path, *, db_id, session_id, actor, audit_path=None, recorded_at=None):
    """Add one session to a database's allowlist; audited."""
    if not isinstance(session_id, str) or not session_id.strip():
        raise SourceError("allowlisted session ids must be non-empty strings")

    def mutate(databases):
        entry = databases.get(db_id)
        if entry is None:
            raise SourceError(f"OpenClaw database {db_id!r} is not allowlisted")
        if entry["sessions"] != "*" and session_id not in entry["sessions"]:
            entry["sessions"] = [*entry["sessions"], session_id]

    return _mutate_allowlist(
        allowlist_path,
        audit_path,
        actor=actor,
        action="grant_session",
        db_id=db_id,
        detail={"session_id": session_id},
        mutate=mutate,
        recorded_at=recorded_at,
    )


def revoke_session(allowlist_path, *, db_id, session_id, actor, audit_path=None, recorded_at=None):
    """Remove one session from a database's allowlist; audited.

    Refuses to punch a hole in a ``"*"`` grant: narrow the database to an
    explicit session list first (re-grant), so a wildcard never silently
    becomes a list nobody reviewed.
    """

    def mutate(databases):
        entry = databases.get(db_id)
        if entry is None:
            raise SourceError(f"OpenClaw database {db_id!r} is not allowlisted")
        if entry["sessions"] == "*":
            raise SourceError(
                "cannot revoke one session from a wildcard grant; "
                "re-grant the database with an explicit session list"
            )
        if session_id not in entry["sessions"]:
            raise SourceError(f"OpenClaw session {session_id!r} is not allowlisted")
        entry["sessions"] = [item for item in entry["sessions"] if item != session_id]

    return _mutate_allowlist(
        allowlist_path,
        audit_path,
        actor=actor,
        action="revoke_session",
        db_id=db_id,
        detail={"session_id": session_id},
        mutate=mutate,
        recorded_at=recorded_at,
    )


# --------------------------------------------------------------------------
# Persistent source pointers
# --------------------------------------------------------------------------


def build_pointer(db_id, provenance):
    """Bind a reader provenance dict to an allowlisted database id."""
    _validate_db_id(db_id)
    if not isinstance(provenance, dict) or provenance.get("provider") not in PROVIDERS:
        raise SourceError("invalid OpenClaw source provenance")
    if provenance.get("precision") != "message":
        raise SourceError("invalid OpenClaw source precision")
    if any(key not in provenance for key in _POINTER_FIELDS):
        raise SourceError("incomplete OpenClaw source provenance")
    pointer = {
        "provider": provenance["provider"],
        "precision": "message",
        "db_id": db_id,
    }
    for key in _POINTER_FIELDS:
        pointer[key] = provenance[key]
    for key in _POINTER_OPTIONAL_FIELDS:
        if key in provenance:
            pointer[key] = provenance[key]
    return pointer


def ledger_identity(pointer):
    """Project a pointer onto the #122 ledger identity triple.

    For ``openclaw-sqlite`` the db_id takes the ``session_file``
    (source-container) slot, rendered as ``<db_id>.openclaw-sqlite`` so
    it stays a bare name for the ledger. For ``openclaw-jsonl`` the
    session file itself is the natural container identity, so the
    pointer's ``session_id`` (a bare ``<uuid>.jsonl`` file name) takes
    the slot.
    """
    if not isinstance(pointer, dict):
        raise SourceError("invalid OpenClaw source pointer")
    _validate_db_id(pointer.get("db_id"))
    if "message_id" not in pointer or "content_hash" not in pointer:
        raise SourceError("incomplete OpenClaw source pointer")
    if pointer.get("provider") == "openclaw-jsonl":
        session_file = pointer.get("session_id")
        if (
            not isinstance(session_file, str)
            or Path(session_file).name != session_file
            or "/" in session_file
            or "\\" in session_file
        ):
            raise SourceError("JSONL ledger identity needs a bare session file name")
    else:
        session_file = f"{pointer['db_id']}.openclaw-sqlite"
    return {
        "session_file": session_file,
        "message_id": str(pointer["message_id"]),
        "content_hash": pointer["content_hash"],
    }


def record_source_deletion(ledger_path, pointer, *, reason, timestamp=None):
    """Record a real #122 tombstone for one ingested pointer identity."""
    identity = ledger_identity(pointer)
    try:
        return source_ledger.record_tombstone(
            ledger_path,
            session_file=identity["session_file"],
            message_id=identity["message_id"],
            content_hash=identity["content_hash"],
            reason=reason,
            timestamp=timestamp,
        )
    except source_ledger.LedgerError as exc:
        raise SourceError(str(exc)) from None


def _load_tombstones(ledger_path):
    if ledger_path is None:
        return None
    try:
        return source_ledger.load_ledger(ledger_path)
    except source_ledger.LedgerError as exc:
        raise SourceError(str(exc)) from None


def _is_tombstoned(records, pointer):
    if records is None:
        return False
    return source_ledger.is_tombstoned(records, ledger_identity(pointer))


def load_pointer_store(path):
    """Return the registered pointer entries; missing store means none."""
    path = Path(path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SourceError("OpenClaw pointer store is unreadable") from None
    if not isinstance(data, dict) or data.get("schema_version") != POINTER_STORE_SCHEMA_VERSION:
        raise SourceError("OpenClaw pointer store has an unsupported schema")
    entries = data.get("records")
    if not isinstance(entries, list):
        raise SourceError("OpenClaw pointer store is malformed")
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("record_id"), str):
            raise SourceError("OpenClaw pointer store entry is malformed")
        pointer = entry.get("pointer")
        if not isinstance(pointer, dict) or any(
            key not in pointer for key in ("db_id", *_POINTER_FIELDS)
        ):
            raise SourceError("OpenClaw pointer store entry is malformed")
        if entry.get("status") not in ("active", "needs_review"):
            raise SourceError("OpenClaw pointer store entry is malformed")
    return entries


def _save_pointer_store(path, entries):
    text = json.dumps(
        {"schema_version": POINTER_STORE_SCHEMA_VERSION, "records": entries},
        ensure_ascii=False,
        indent=2,
    )
    atomic_write_text_locked(path, text + "\n")


def _default_record_id(pointer):
    return (
        f"{pointer['provider']}:{pointer['db_id']}:{pointer['session_id']}:{pointer['message_id']}"
    )


def ingest_session(
    allowlist_path,
    store_path,
    *,
    db_id,
    session_id,
    record_id_for=None,
    ledger_path=None,
    now=None,
):
    """Read one authorized session and register a pointer per message.

    Registration is all-or-nothing: any denial, read failure, tombstoned
    identity, or record-id collision aborts before the store is touched.
    Re-ingesting an unchanged session is idempotent (existing pointers are
    reported, not duplicated); a record id that now points at different
    bytes is a collision and fails closed so the drift is reviewed, not
    silently overwritten.
    """
    allowlist = load_allowlist(allowlist_path)
    entry = require_authorized(allowlist, db_id, session_id)
    source_stats = None
    if _entry_provider(entry) == "openclaw-jsonl":
        messages, source_stats = _read_jsonl_session_with_stats(entry["path"], session_id)
    else:
        messages = read_session(entry["path"], session_id)
    tombstones = _load_tombstones(ledger_path)
    stamp = now or _now()
    candidates = []
    for message in messages:
        pointer = build_pointer(db_id, message["provenance"])
        if _is_tombstoned(tombstones, pointer):
            raise SourceError(
                "source message is tombstoned; a deleted identity cannot be re-ingested"
            )
        record_id = record_id_for(message) if record_id_for else _default_record_id(pointer)
        if not isinstance(record_id, str) or not record_id.strip():
            raise SourceError("record_id_for must return a non-empty record id")
        candidates.append((record_id, pointer))
    store_path = Path(store_path)
    with cross_platform_lock(store_path):
        entries = load_pointer_store(store_path)
        by_id = {item["record_id"]: item for item in entries}
        registered, existing = [], []
        for record_id, pointer in candidates:
            prior = by_id.get(record_id)
            if prior is not None:
                if prior["pointer"] != pointer:
                    raise SourceError(
                        "record id already registered with a different source "
                        "pointer; review the drift instead of overwriting it"
                    )
                existing.append(record_id)
                continue
            record = {
                "record_id": record_id,
                "pointer": pointer,
                "status": "active",
                "review_reason": None,
                "tombstoned": False,
                "ingested_at": stamp,
                "updated_at": stamp,
                "history": [{"at": stamp, "event": "registered"}],
            }
            entries.append(record)
            by_id[record_id] = record
            registered.append(record_id)
        if registered:
            _save_pointer_store(store_path, entries)
    result = {
        "db_id": db_id,
        "session_id": session_id,
        "registered": registered,
        "existing": existing,
        "messages": messages,
    }
    if source_stats is not None:
        result["source_stats"] = source_stats
    return result


# --------------------------------------------------------------------------
# Deletion propagation
# --------------------------------------------------------------------------


def _session_message_index(db_path, session_id, provider="openclaw-sqlite"):
    """Map (sequence, message_id) -> provenance for one session.

    Returns None when the database file or the session is gone. Environment
    failures (corrupt database, missing zstandard decoder, unreadable
    schema) raise SourceError instead of being mistaken for deletions.
    """
    path = Path(db_path)
    if provider == "openclaw-jsonl":
        if not path.is_dir() or not (path / session_id).is_file():
            return None
        messages = _read_jsonl_session(path, session_id)
        return {
            (message["provenance"]["sequence"], message["provenance"]["message_id"]): message[
                "provenance"
            ]
            for message in messages
        }
    if not path.exists():
        return None
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            row = connection.execute(
                "SELECT COUNT(*) FROM transcript_events WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        raise SourceError("unable to inspect OpenClaw transcript database") from None
    if row is None or row[0] == 0:
        return None
    messages = read_session(path, session_id)
    return {
        (message["provenance"]["sequence"], message["provenance"]["message_id"]): message[
            "provenance"
        ]
        for message in messages
    }


def _compare(pointer, observed):
    if observed is None:
        return "source_missing"
    for key in ("content_hash", "event_hash", "speaker", "timestamp"):
        if observed[key] != pointer[key]:
            return "source_changed"
    return None


def check_pointers(allowlist_path, store_path, *, ledger_path=None, now=None):
    """Re-resolve every registered pointer and flag records needing review.

    One-way: this never reactivates a flagged record. A flagged record
    whose source resolves byte-identically again is reported under
    ``resolves_again`` for a human to reinstate explicitly.
    """
    allowlist = load_allowlist(allowlist_path)
    tombstones = _load_tombstones(ledger_path)
    stamp = now or _now()
    store_path = Path(store_path)
    marked, resolves_again = [], []
    with cross_platform_lock(store_path):
        entries = load_pointer_store(store_path)
        groups = {}
        for entry in entries:
            pointer = entry["pointer"]
            groups.setdefault((pointer["db_id"], pointer["session_id"]), []).append(entry)
        indexes = {}
        for (db_id, session_id), group in groups.items():
            authorized = None
            try:
                authorized = require_authorized(allowlist, db_id, session_id)
            except SourceError:
                authorized = None
            for entry in group:
                pointer = entry["pointer"]
                reason = None
                if _is_tombstoned(tombstones, pointer):
                    reason = "source_tombstoned"
                    entry["tombstoned"] = True
                elif authorized is None:
                    reason = "authorization_revoked"
                else:
                    group_key = (db_id, session_id)
                    if group_key not in indexes:
                        indexes[group_key] = _session_message_index(
                            authorized["path"], session_id, _entry_provider(authorized)
                        )
                    index = indexes[group_key]
                    observed = (
                        None
                        if index is None
                        else index.get((pointer["sequence"], pointer["message_id"]))
                    )
                    reason = _compare(pointer, observed)
                if reason is None:
                    if entry["status"] == "needs_review":
                        resolves_again.append(entry["record_id"])
                    continue
                if entry["status"] == "active" or entry.get("review_reason") != reason:
                    entry["status"] = "needs_review"
                    entry["review_reason"] = reason
                    entry["updated_at"] = stamp
                    entry.setdefault("history", []).append(
                        {"at": stamp, "event": "flagged", "reason": reason}
                    )
                    marked.append({"record_id": entry["record_id"], "reason": reason})
        _save_pointer_store(store_path, entries)
        still_active = sum(1 for entry in entries if entry["status"] == "active")
    return {
        "checked": len(entries),
        "still_active": still_active,
        "marked": marked,
        "resolves_again": resolves_again,
    }


def reinstate_pointer(allowlist_path, store_path, *, record_id, ledger_path=None, now=None):
    """Manually return one flagged record to active after re-resolution.

    Refuses while a tombstone stands for the pointer identity (deletion
    wins), while the source is unauthorized, or while the source does not
    resolve byte-identically right now.
    """
    allowlist = load_allowlist(allowlist_path)
    tombstones = _load_tombstones(ledger_path)
    stamp = now or _now()
    store_path = Path(store_path)
    with cross_platform_lock(store_path):
        entries = load_pointer_store(store_path)
        entry = next((item for item in entries if item["record_id"] == record_id), None)
        if entry is None:
            raise SourceError(f"unknown pointer record id {record_id!r}")
        if entry["status"] != "needs_review":
            raise SourceError("only a record flagged for review can be reinstated")
        pointer = entry["pointer"]
        if entry.get("tombstoned") or _is_tombstoned(tombstones, pointer):
            raise SourceError("a tombstoned source pointer cannot be reinstated; deletion wins")
        authorized = require_authorized(allowlist, pointer["db_id"], pointer["session_id"])
        index = _session_message_index(
            authorized["path"], pointer["session_id"], _entry_provider(authorized)
        )
        observed = (
            None if index is None else index.get((pointer["sequence"], pointer["message_id"]))
        )
        reason = _compare(pointer, observed)
        if reason is not None:
            raise SourceError(f"source pointer does not resolve cleanly: {reason}")
        entry["status"] = "active"
        entry["review_reason"] = None
        entry["updated_at"] = stamp
        entry.setdefault("history", []).append({"at": stamp, "event": "reinstated"})
        _save_pointer_store(store_path, entries)
    return entry
