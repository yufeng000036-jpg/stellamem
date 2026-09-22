"""Read complete sources and retain honest precision and offsets."""
import hashlib
import json
import re
from pathlib import Path


class SourceError(RuntimeError):
    pass


def content_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def session_text(entry):
    content = entry.get("message", {}).get("content", "")
    if isinstance(content, list):
        return " ".join(c.get("text", "") for c in content if isinstance(c, dict) and isinstance(c.get("text", ""), str))
    return content if isinstance(content, str) else ""


def read_daily(path, config=None):
    path = Path(path)
    manifest = path.with_suffix(".sources.json")
    if not manifest.exists():
        return parse_messages(path.read_text(encoding="utf-8"), path.name)
    config = config or {}
    if not config.get("sessions_dir"):
        raise SourceError("Source manifest needs configured sessions_dir")
    root = Path(config["sessions_dir"]).resolve()
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if data.get("schema_version") != 2 or not isinstance(data.get("pointers"), list):
        raise SourceError("Invalid source manifest")
    cache = {}
    messages = []
    for pointer in data["pointers"]:
        name = pointer.get("session_file", "")
        if not name or Path(name).name != name or "/" in name or "\\" in name:
            raise SourceError("Invalid source filename")
        candidates = [root / name]
        # Host may rename canonical session after /new; bounded same-session search.
        base = name.split(".jsonl")[0]
        if not candidates[0].exists():
            candidates = sorted(root.glob(base + ".jsonl*"))
        found = None
        for source in candidates:
            if source.resolve().parent != root:
                raise SourceError("Source path escaped configured root")
            if source not in cache:
                cache[source] = source.read_text(encoding="utf-8").splitlines()
            idx = pointer.get("entry_index")
            if not isinstance(idx, int) or idx < 0 or idx >= len(cache[source]):
                continue
            try:
                entry = json.loads(cache[source][idx])
            except ValueError:
                continue
            text = session_text(entry)
            if (content_hash(text) != pointer.get("content_hash")
                    or entry.get("timestamp") != pointer.get("timestamp")
                    or entry.get("message", {}).get("role") != pointer.get("speaker")
                    or str(entry.get("id") or entry.get("message", {}).get("id") or idx) != str(pointer.get("message_id"))):
                continue
            found = {"text": text, "speaker": pointer["speaker"],
                     "timestamp": pointer["timestamp"],
                     "provenance": dict(pointer, precision="message",
                                        resolved_session_file=source.name),
                     "offset": 0}
            break
        if found is None:
            raise SourceError("Source pointer missing or changed; not consumed")
        messages.append(found)
    return messages


def parse_messages(text, source_name):
    pattern = re.compile(r"(?m)^\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*(user|assistant|system|tool):\s*")
    matches = list(pattern.finditer(text))
    messages = []
    if not matches:
        return [{"text": text, "speaker": "unknown", "timestamp": source_name[:10],
                 "offset": 0, "provenance": {"provider": "daily", "source_ref": source_name,
                 "precision": "day", "content_hash": content_hash(text)}}]
    if text[:matches[0].start()].strip().lstrip("#").strip() not in ("", source_name[:10]):
        prefix = text[:matches[0].start()]
        messages.extend(parse_messages(prefix, source_name))
    for i, match in enumerate(matches):
        body = text[match.end():matches[i + 1].start() if i + 1 < len(matches) else len(text)]
        messages.append({"text": body, "speaker": match[2],
                         "timestamp": source_name[:10] + "T" + match[1],
                         "offset": match.end(), "provenance": {
                             "provider": "daily", "source_ref": source_name,
                             "entry_index": i, "precision": "message",
                             "content_hash": content_hash(body)}})
    return messages


def split_message(message, limit):
    text = message["text"]
    start = 0
    while start < len(text):
        end = min(start + limit, len(text))
        forced = False
        if end < len(text):
            boundaries = list(re.finditer(r"[.!?\u3002\uff01\uff1f\n](?:\s|$)?", text[start:end]))
            if boundaries:
                end = start + boundaries[-1].end()
            else:
                # An indivisible huge token cannot fit a finite context. Explicit,
                # contiguous offsets preserve all characters for retry/audit.
                forced = True
        piece = dict(message, text=text[start:end],
                     offset=message.get("offset", 0) + start,
                     continuation=start > 0, forced_boundary=forced)
        yield piece
        start = end


def chunk_messages(messages, max_chars=4000):
    if max_chars < 128:
        raise ValueError("chunk_chars must be >= 128")
    current, size = [], 0
    for message in messages:
        for piece in split_message(message, max_chars):
            if current and size + len(piece["text"]) > max_chars:
                yield current
                current, size = [], 0
            current.append(piece)
            size += len(piece["text"])
    if current:
        yield current
