"""把原始档案层接成编译输入：说话人、时间、记录指针都来自原文，不靠猜。

与 `source_reader.read_daily` 的差别：
- daily 是散文，多数行没有 speaker，只能记 unknown；
- raw 每条都有 role 和 ISO 时间戳，于是 `source` 天然可信；
- 定位用 `record_id`（唯一、可反查），不再依赖会漂移的字符 offset。
"""

import gzip
import json
from pathlib import Path

STREAM_DIR = "sessions"


class RawError(RuntimeError):
    pass


def _manifest(root):
    path = Path(root) / "MANIFEST.jsonl"
    if not path.exists():
        raise RawError("raw manifest missing; run raw_archive.py ingest")
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def _stream(root, session_file):
    path = Path(root) / STREAM_DIR / (session_file.replace(".jsonl", "") + ".jsonl.gz")
    if not path.exists():
        raise RawError("archive stream missing: " + session_file)
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def messages_for_day(root, day, roles=("user",), limit=None, unique=True):
    """取某一天（本地时区）的原文消息，按时间排序。

    OpenClaw 的 session jsonl 是**逐轮全量快照**：同一句话会在每轮追加时重复出现
    （实测 83,595 条记录只有 6,778 条唯一文本，12.3 倍重复）。所以消费端默认按
    `text_sha256` 去重，只保留最早出现的那条，并把出现次数记在 provenance 里。
    档案层本身不去重——原文必须无损。
    """
    wanted = {}
    for entry in _manifest(root):
        if entry.get("date_local") != day:
            continue
        if roles and entry.get("role") not in roles:
            continue
        wanted.setdefault(entry["session_file"], []).append(entry)

    messages = []
    seen = {}
    for session_file, entries in sorted(wanted.items()):
        ids = {e["record_id"]: e for e in entries}
        for record in _stream(root, session_file):
            meta = ids.get(record["record_id"])
            if meta is None:
                continue
            key = record["text_sha256"]
            if unique and key in seen:
                seen[key]["provenance"]["occurrences"] += 1
                continue
            message = {
                "text": record["text"],
                "speaker": record["role"],
                "timestamp": record.get("timestamp") or day,
                "offset": 0,
                "provenance": {
                    "provider": "raw",
                    "source_ref": session_file,
                    "record_id": record["record_id"],
                    "message_id": record.get("message_id"),
                    "entry_index": meta.get("entry_index", 0),
                    "precision": "message",
                    "content_hash": record["text_sha256"],
                    "date_local": record.get("date_local"),
                    "occurrences": 1,
                    "unique": True,
                },
            }
            if unique:
                seen[key] = message
            messages.append(message)
    messages.sort(key=lambda m: (m["timestamp"] or "", m["provenance"]["record_id"]))
    return messages[:limit] if limit else messages


def get_record(root, record_id):
    """按 record_id 反查原文（get_source_evidence 的最小实现）。"""
    for entry in _manifest(root):
        if entry["record_id"] != record_id:
            continue
        for record in _stream(root, entry["session_file"]):
            if record["record_id"] == record_id:
                return record
        raise RawError("manifest points at a record the stream no longer has")
    return None


def days(root):
    seen = {}
    for entry in _manifest(root):
        day = entry.get("date_local")
        if day:
            seen[day] = seen.get(day, 0) + 1
    return dict(sorted(seen.items()))
