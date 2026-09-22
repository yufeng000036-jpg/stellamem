"""Atomic files and recoverable multi-file transactions for one local writer."""

import contextlib
import hashlib
import json
import os
import re
import uuid
from pathlib import Path

PROTECTED = {"core/identity.md", "core/user-core.md"}
MARK = "<!-- stellamem:v2 -->"
BLOCK = re.compile(r"(?m)^\x60\x60\x60stellamem\n([^\n]+)\n\x60\x60\x60\s*$")


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    data = content.encode("utf-8") if isinstance(content, str) else content
    try:
        with tmp.open("xb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def safe_path(root, rel):
    root = Path(root).resolve()
    path = root / rel
    if Path(rel).is_absolute() or ".." in Path(rel).parts or ":" in rel:
        raise ValueError("unsafe relative path")
    if not path.resolve().is_relative_to(root):
        raise ValueError("path leaves memory root")
    return path


def sha(data):
    return hashlib.sha256(data).hexdigest()


@contextlib.contextmanager
def writer_lock(root):
    path = safe_path(root, "system/writer.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as fh:
        fh.seek(0)
        if os.fstat(fh.fileno()).st_size == 0:
            fh.write(b"0")
            fh.flush()
        fh.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("another memory writer is active") from None
        try:
            yield
        finally:
            fh.seek(0)
            if os.name == "nt":
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def load_records(path):
    if not Path(path).exists():
        return "", []
    text = Path(path).read_text(encoding="utf-8")
    if MARK not in text:
        return text, []
    prefix, managed = text.split(MARK, 1)
    records = []
    for match in BLOCK.finditer(managed):
        record = json.loads(match[1])
        if not isinstance(record, dict) or record.get("schema_version") != 2:
            raise ValueError("invalid canonical record")
        records.append(record)
    # Refuse to overwrite a damaged managed section.
    if managed.strip() and not records:
        raise ValueError("unreadable managed section")
    if managed.count("```stellamem") != len(records):
        raise ValueError("incomplete managed record")
    return prefix, records


def render_records(prefix, records):
    lines = [prefix.rstrip(), "", MARK, ""]
    for record in records:
        lines.extend(
            ["```stellamem", json.dumps(record, ensure_ascii=False, sort_keys=True), "```", ""]
        )
    return "\n".join(lines)


class Transaction:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.journal = safe_path(self.root, "system/transaction.json")

    def recover(self):
        if not self.journal.exists():
            return
        entry = json.loads(self.journal.read_text(encoding="utf-8"))
        for rel, item in entry["files"].items():
            if rel.replace("\\", "/") in PROTECTED:
                raise ValueError("protected core in journal")
            path = safe_path(self.root, rel)
            current = sha(path.read_bytes()) if path.exists() else None
            if current not in (item["before_hash"], item["after_hash"]):
                raise RuntimeError("transaction recovery conflicts with external edits")
        for rel, item in entry["files"].items():
            atomic_write(safe_path(self.root, rel), item["after"])
        self.journal.unlink()

    def commit(self, changes):
        if self.journal.exists():
            raise RuntimeError("recover pending transaction first")
        txid = uuid.uuid4().hex
        changes = dict(changes, **{"system/generation": txid})
        files = {}
        for rel, text in changes.items():
            if rel.replace("\\", "/") in PROTECTED:
                raise ValueError("protected core cannot be written")
            path = safe_path(self.root, rel)
            before = path.read_bytes() if path.exists() else None
            after = text.encode("utf-8")
            if before == after:
                continue
            files[rel] = {
                "before": before.decode("utf-8") if before is not None else None,
                "before_hash": sha(before) if before is not None else None,
                "after": text,
                "after_hash": sha(after),
            }
        if not files:
            return None
        entry = {"schema_version": 2, "id": txid, "files": files}
        data = json.dumps(entry, ensure_ascii=False, indent=2)
        # Snapshots are local, never projected. Existing legacy secrets may exist
        # in pre-images; explicit user review is needed before sharing backups.
        atomic_write(safe_path(self.root, "system/transactions/" + txid + ".json"), data)
        atomic_write(self.journal, data)
        self.recover()
        return txid

    def rollback(self, txid, yes=False):
        if not re.fullmatch(r"[0-9a-f]{32}", txid):
            raise ValueError("invalid transaction ID")
        if self.journal.exists():
            raise RuntimeError("recover interrupted transaction first")
        entry = json.loads(
            safe_path(self.root, "system/transactions/" + txid + ".json").read_text(
                encoding="utf-8"
            )
        )
        changes = {}
        for rel, item in entry["files"].items():
            if rel.replace("\\", "/") in PROTECTED:
                raise ValueError("protected core cannot be restored")
            path = safe_path(self.root, rel)
            if not path.exists() or sha(path.read_bytes()) != item["after_hash"]:
                raise RuntimeError("rollback would overwrite later edits")
            # Newly created files become empty; never recursively delete user data.
            initial = ""
            if rel in {"system/extraction-cursor.json", "system/observe-cursor.json"}:
                initial = json.dumps({"schema_version": 2, "sources": {}})
            elif rel == "system/version-chain.json":
                initial = "[]"
            elif rel == "system/compile-status.json":
                initial = json.dumps({"status": "rolled_back"})
            changes[rel] = item["before"] if item["before"] is not None else initial
        if yes:
            return self.commit(changes)
        return list(changes)
