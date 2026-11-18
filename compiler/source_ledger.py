"""Source ledger: tombstones and authorized rotations for source pointers.

Why this exists (PR #117 post-mortem): after a host rotates a session file
(``session.jsonl`` -> ``session.jsonl.reset.<stamp>``) and reuses the canonical
filename, an old pointer's on-disk state is ambiguous. The same bytes can mean
"the file was rotated, the archive is the legitimate source" or "the user
deleted/edited that message and an archive copy happens to survive". No
content check can separate the two, so archive recovery after filename reuse
must never be automatic: it needs an authoritative, verifiable signal.

This module is that signal. A ledger is a small JSON document holding an
append-only, hash-chained list of records:

- ``tombstone``: a specific pointer identity (session_file + message_id +
  content_hash) was deleted or edited by the user. Resolution of a tombstoned
  pointer fails closed with SourceError even if the exact bytes still exist
  in the canonical file or any archive. Deletion wins over every copy.
- ``rotation``: the host rotated ``from_file`` to ``to_file`` and the archive
  bytes at record time hashed to ``file_sha256``. Only while the archive on
  disk still hashes to exactly that value may the reader resolve an old
  pointer from it after the canonical filename was reused.

Each record carries ``prev`` (the previous record's hash) and ``hash``
(SHA-256 of the canonical record body), so editing or truncating history is
detectable at load time: a ledger that fails verification is treated as no
authority at all and the reader raises instead of guessing. Callers that need
to detect a rollback to an older, still self-consistent snapshot can pin the
expected head hash (``source_ledger_head`` in the reader config, or the value
returned by the append functions here); a chain that does not contain the
pinned head is refused.

Behavior by scenario (enforced in ``source_reader.read_daily``):

- Active deletion: tombstone recorded -> pointer refused everywhere.
- Active edit: the old content_hash is tombstoned -> the old pointer is
  refused; the edited text is a new pointer identity and resolves normally.
- Normal rotation: rotation record + archive bytes unchanged -> the old
  pointer resolves from the archive, provenance marks the ledger path taken.
  Both missing and reused filenames require a rotation record for fallback.
- Archive retention: if the archive file is later replaced, truncated or
  deleted, its hash no longer matches the rotation record (or it is gone),
  so authorization lapses and resolution fails closed.
- Concurrent writers: appends hold ``cross_platform_lock`` on the ledger and
  rewrite it atomically, so records are never lost or interleaved.
- Rollback: a rolled-back ledger fails verification if the chain is broken,
  and fails the pinned-head check when the caller pins a head; without a pin
  a self-consistent old snapshot is indistinguishable from a legitimately
  old ledger — that residual risk is accepted and documented here rather
  than papered over.

The default ledger location is ``.stellamem-source-ledger.json`` inside the
configured sessions root (a dotfile, so host session scans ignore it); the
reader also accepts ``source_ledger_path`` in its config. Nothing in this
module touches ingestion or projection; it only serves source resolution.
"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from file_lock import atomic_write_text_locked, cross_platform_lock

LEDGER_FILENAME = ".stellamem-source-ledger.json"
SCHEMA_VERSION = 1
_KINDS = ("tombstone", "rotation")
_REASONS = ("deleted", "edited")


class LedgerError(RuntimeError):
    pass


def _canonical(body):
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def record_hash(body):
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()


def verify_records(records):
    if not isinstance(records, list):
        raise LedgerError("Source ledger records must be a list")
    prev = None
    for seq, record in enumerate(records):
        if not isinstance(record, dict) or record.get("kind") not in _KINDS:
            raise LedgerError("Source ledger record has unknown kind")
        if record.get("seq") != seq or record.get("prev") != prev:
            raise LedgerError("Source ledger chain is broken")
        body = {key: value for key, value in record.items() if key != "hash"}
        if record.get("hash") != record_hash(body):
            raise LedgerError("Source ledger record hash mismatch")
        prev = record["hash"]
    return records


def load_ledger(path):
    """Return the verified record list; a missing ledger means no records."""
    path = Path(path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LedgerError("Source ledger is unreadable") from exc
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise LedgerError("Source ledger has an unsupported schema")
    return verify_records(data.get("records"))


def ledger_head(records):
    return records[-1]["hash"] if records else None


def _append(path, payload):
    path = Path(path)
    with cross_platform_lock(path):
        records = load_ledger(path)
        record = dict(payload)
        record["seq"] = len(records)
        record["prev"] = ledger_head(records)
        record["hash"] = record_hash(record)
        records.append(record)
        text = json.dumps(
            {"schema_version": SCHEMA_VERSION, "records": records},
            ensure_ascii=False,
            indent=2,
        )
        atomic_write_text_locked(path, text + "\n")
    return record


def _bare_name(name):
    return bool(name) and Path(name).name == name and "/" not in name and "\\" not in name


def record_tombstone(
    path,
    *,
    session_file,
    message_id,
    content_hash,
    reason,
    timestamp=None,
    recorded_at=None,
):
    """Append a tombstone for one pointer identity. Returns the record."""
    if not _bare_name(session_file):
        raise LedgerError("Tombstone needs a bare session filename")
    if reason not in _REASONS:
        raise LedgerError("Tombstone reason must be 'deleted' or 'edited'")
    return _append(
        path,
        {
            "kind": "tombstone",
            "session_file": session_file,
            "message_id": str(message_id),
            "content_hash": content_hash,
            "timestamp": timestamp,
            "reason": reason,
            "recorded_at": recorded_at or _now(),
        },
    )


def record_rotation(path, *, from_file, to_file, recorded_at=None):
    """Append a rotation record authorizing one exact archive file.

    ``to_file`` is the rotated file on disk; its current bytes are hashed so
    later replacement or truncation revokes the authorization by itself.
    """
    to_file = Path(to_file)
    if not _bare_name(from_file):
        raise LedgerError("Rotation needs a bare canonical session filename")
    if not to_file.exists():
        raise LedgerError("Rotated session file does not exist")
    lines = to_file.read_bytes().splitlines()
    return _append(
        path,
        {
            "kind": "rotation",
            "session_base": from_file.split(".jsonl")[0],
            "from_file": from_file,
            "to_file": to_file.name,
            "file_sha256": file_sha256(to_file),
            "lines": len(lines),
            "recorded_at": recorded_at or _now(),
        },
    )


def is_tombstoned(records, pointer):
    for record in records:
        if record.get("kind") != "tombstone":
            continue
        if (
            record.get("session_file") == pointer.get("session_file")
            and record.get("message_id") == str(pointer.get("message_id"))
            and record.get("content_hash") == pointer.get("content_hash")
        ):
            return True
    return False


def authorized_rotations(records, session_file):
    return [
        record
        for record in records
        if record.get("kind") == "rotation" and record.get("from_file") == session_file
    ]


def _main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="Stellamem source ledger")
    sub = parser.add_subparsers(dest="command", required=True)
    tomb = sub.add_parser("tombstone", help="record a deleted/edited source message")
    tomb.add_argument("ledger")
    tomb.add_argument("--session-file", required=True)
    tomb.add_argument("--message-id", required=True)
    tomb.add_argument("--content-hash", required=True)
    tomb.add_argument("--reason", choices=_REASONS, required=True)
    tomb.add_argument("--timestamp")
    rot = sub.add_parser("rotate", help="record a session file rotation")
    rot.add_argument("ledger")
    rot.add_argument("--from-file", required=True)
    rot.add_argument("--to-file", required=True)
    ver = sub.add_parser("verify", help="verify a ledger and print its head hash")
    ver.add_argument("ledger")
    args = parser.parse_args(argv)
    if args.command == "tombstone":
        record = record_tombstone(
            args.ledger,
            session_file=args.session_file,
            message_id=args.message_id,
            content_hash=args.content_hash,
            reason=args.reason,
            timestamp=args.timestamp,
        )
    elif args.command == "rotate":
        record = record_rotation(args.ledger, from_file=args.from_file, to_file=args.to_file)
    else:
        records = load_ledger(args.ledger)
        print(ledger_head(records) or "")
        return 0
    print(record["hash"])
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
