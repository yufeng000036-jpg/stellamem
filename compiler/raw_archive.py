"""原始档案层：不可变、可校验、可增量、可导出、可删除。

为什么要有这一层
----------------
`daily/*.md` 是模型写的日记，说话人是模型不是用户（实测 70 篇里 user 行只有 6 行）。
`C:\\Users\\hd\\.openclaw\\agents\\main\\sessions\\*.jsonl` 才是第一手档案：有 role、
有 ISO 时间戳、有 message id。本模块只做一件事——把它原样固化下来，不改一个字。

设计
----
- 存放：`<memories_root>/_raw/`
    sessions/<session>.jsonl.gz   每个源会话一个压缩流，一行一条记录
    MANIFEST.jsonl               只追加，一行一条索引（含 sha256）
    ledger.json                  每个源文件的 sha256/size/mtime/计数，用于增量与幂等
- record_id 由「源文件 sha256 + 行号 + message_id + role + timestamp」决定，
  同一份源重跑必然得到同一 id，因此重复导入不会产生重复记录。
- 已删除的会话（`*.deleted.jsonl`）默认跳过：删除意图优先于归档完整度。
- 敏感内容不删、不改、不截断（档案必须无损），只打标记；`_raw` 永远不进注入
  （projection.js 的 FORBIDDEN 正则里有 `_raw`）。

CLI
---
    python compiler/raw_archive.py ingest  [--root ...] [--sessions ...] [--roles user,assistant] [--dry-run]
    python compiler/raw_archive.py verify
    python compiler/raw_archive.py stats
    python compiler/raw_archive.py export --day 2026-09-15 --out file.jsonl
    python compiler/raw_archive.py forget --session <session_file>
"""

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = 1
STREAM_DIR = "sessions"
DEFAULT_ROLES = ("user", "assistant")
SECRET_HINT = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{30,}", re.I
)
PII = re.compile(r"(?<![0-9])(1[3-9][0-9]{9}|[0-9]{17}[0-9Xx])(?![0-9])")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def message_text(message):
    content = message.get("content")
    if isinstance(content, list):
        return "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return content if isinstance(content, str) else ""


def local_date(timestamp, offset_hours):
    if not isinstance(timestamp, str) or len(timestamp) < 19:
        return None
    try:
        base = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return timestamp[:10]
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    return (base + timedelta(hours=offset_hours)).strftime("%Y-%m-%d")


def make_record(session_file, file_sha, entry_index, entry, role, offset_hours):
    message = entry.get("message") or {}
    timestamp = entry.get("timestamp")
    message_id = str(message.get("id") or entry.get("id") or entry_index)
    text = message_text(message)
    # The id must survive an append to the same session file, so the file hash
    # is deliberately NOT part of it: (session, index, message id, role, time)
    # is stable for append-only transcripts.
    record_id = "rm-" + sha256_bytes(
        f"{session_file}:{entry_index}:{message_id}:{role}:{timestamp}".encode("utf-8")
    )[:24]
    return {
        "schema_version": SCHEMA,
        "record_id": record_id,
        "session_file": session_file,
        "session_id": session_file.replace(".jsonl", ""),
        "source_file_sha256": file_sha,
        "entry_index": entry_index,
        "message_id": message_id,
        "role": role,
        "timestamp": timestamp,
        "date_local": local_date(timestamp, offset_hours),
        "tz_offset_hours": offset_hours,
        "text": text,
        "text_sha256": sha256_bytes(text.encode("utf-8")),
        "char_len": len(text),
        "text_bytes": len(text.encode("utf-8")),
        "cjk_len": len(re.findall(r"[\u4e00-\u9fff]", text)),
        "secret": bool(SECRET_HINT.search(text)),
        "pii_hits": len(PII.findall(text)),
        "archived_at": datetime.now(timezone.utc).isoformat(),
    }


def read_ledger(root):
    path = root / "ledger.json"
    if not path.exists():
        return {"schema_version": SCHEMA, "sources": {}, "totals": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("sources", {})
    data.setdefault("totals", {})
    return data


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def gz_write(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            fh.write("\n")
    os.replace(tmp, path)


def gz_read(path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def manifest_entry(record):
    entry = {k: v for k, v in record.items() if k != "text"}
    entry["text_sha256"] = record["text_sha256"]
    return entry


def rebuild_manifest(root):
    """重扫所有流重建索引：索引永远是流的投影，不会与流不一致。"""
    path = root / "MANIFEST.jsonl"
    tmp = path.with_name("MANIFEST.jsonl.tmp")
    count = 0
    with tmp.open("w", encoding="utf-8") as out:
        for stream in sorted((root / STREAM_DIR).glob("*.jsonl.gz")):
            for record in gz_read(stream):
                out.write(json.dumps(manifest_entry(record), ensure_ascii=False, sort_keys=True) + "\n")
                count += 1
    os.replace(tmp, path)
    return count


def existing_ids(root):
    ids = set()
    path = root / "MANIFEST.jsonl"
    if not path.exists():
        return ids
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ids.add(json.loads(line)["record_id"])
            except (ValueError, KeyError):
                continue
    return ids


def ingest(root, sessions, roles=DEFAULT_ROLES, offset_hours=8, dry_run=False, force=False):
    root.mkdir(parents=True, exist_ok=True)
    ledger = read_ledger(root)
    report = {
        "scanned": 0, "skipped_deleted": 0, "skipped_unchanged": 0,
        "parsed_files": 0, "added": 0, "deduped_in_source": 0,
        "excluded_role": 0, "empty_text": 0, "secret_flagged": 0, "pii_flagged": 0,
        "roles": {}, "dry_run": dry_run,
    }
    for source in sorted(sessions.glob("*.jsonl")):
        if ".trajectory" in source.name:
            continue
        if ".deleted" in source.name:
            report["skipped_deleted"] += 1
            continue
        report["scanned"] += 1
        file_sha = sha256_file(source)
        stat = source.stat()
        known = ledger["sources"].get(source.name)
        if known and known.get("sha256") == file_sha and not force:
            report["skipped_unchanged"] += 1
            continue

        records, seen_here = [], set()
        with source.open("r", encoding="utf-8", errors="replace") as fh:
            for index, line in enumerate(fh):
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                message = entry.get("message") or {}
                role = message.get("role")
                if not role:
                    continue
                report["roles"][role] = report["roles"].get(role, 0) + 1
                if roles and role not in roles:
                    report["excluded_role"] += 1
                    continue
                text = message_text(message)
                if not text.strip():
                    report["empty_text"] += 1
                    continue
                record = make_record(source.name, file_sha, index, entry, role, offset_hours)
                if record["record_id"] in seen_here:
                    report["deduped_in_source"] += 1
                    continue
                if record["secret"]:
                    report["secret_flagged"] += 1
                if record["pii_hits"]:
                    report["pii_flagged"] += 1
                records.append(record)
                seen_here.add(record["record_id"])

        report["parsed_files"] += 1
        if not dry_run:
            # 重写整条流：源文件是权威，流只是它的无损投影。
            gz_write(root / STREAM_DIR / (source.name.replace(".jsonl", "") + ".jsonl.gz"), records)
        previous = known.get("records", 0) if known else 0
        report["added"] += max(0, len(records) - previous)
        if known:
            report["rewritten"] = report.get("rewritten", 0) + 1
        ledger["sources"][source.name] = {
            "sha256": file_sha,
            "bytes": stat.st_size,
            "mtime": int(stat.st_mtime),
            "records": len(records),
            "imported_at": datetime.now(timezone.utc).isoformat(),
        }

    if not dry_run:
        total = rebuild_manifest(root)
        compressed = sum(f.stat().st_size for f in (root / STREAM_DIR).glob("*.jsonl.gz"))
        ledger["totals"] = {
            "records": total,
            "sources": len(ledger["sources"]),
            "compressed_bytes": compressed,
            "last_ingest": datetime.now(timezone.utc).isoformat(),
            "roles": list(roles),
            "tz_offset_hours": offset_hours,
        }
        ledger["sessions_dir"] = str(sessions)
        write_json(root / "ledger.json", ledger)
        report["manifest_records"] = total
    return report


def verify(root):
    manifest_path = root / "MANIFEST.jsonl"
    if not manifest_path.exists():
        return {"ok": False, "reason": "manifest missing"}
    entries, bad_lines = [], 0
    with manifest_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except ValueError:
                bad_lines += 1
    by_file = {}
    for entry in entries:
        by_file.setdefault(entry["session_file"], []).append(entry)

    missing_stream = mismatch = count_mismatch = corrupt_stream = 0
    total_chars = 0
    for session_file, items in by_file.items():
        stream = root / STREAM_DIR / (session_file.replace(".jsonl", "") + ".jsonl.gz")
        if not stream.exists():
            missing_stream += 1
            continue
        try:
            archived = {r["record_id"]: r for r in gz_read(stream)}
        except (OSError, EOFError, ValueError):
            # 断电/写一半/被截断：报告损坏，不抛异常，交给 ingest --force 修
            corrupt_stream += 1
            continue
        if len(archived) != len(items):
            count_mismatch += 1
        for item in items:
            record = archived.get(item["record_id"])
            if record is None:
                mismatch += 1
                continue
            if sha256_bytes(record["text"].encode("utf-8")) != item["text_sha256"]:
                mismatch += 1
                continue
            total_chars += record["char_len"]
    extra = 0
    for session_file, items in by_file.items():
        stream = root / STREAM_DIR / (session_file.replace(".jsonl", "") + ".jsonl.gz")
        if stream.exists():
            try:
                extra += sum(1 for _ in gz_read(stream)) - len(items)
            except (OSError, EOFError, ValueError):
                continue
    return {
        "ok": (mismatch == 0 and missing_stream == 0 and count_mismatch == 0
               and bad_lines == 0 and corrupt_stream == 0),
        "manifest_records": len(entries),
        "sessions": len(by_file),
        "bad_manifest_lines": bad_lines,
        "missing_streams": missing_stream,
        "corrupt_streams": corrupt_stream,
        "text_hash_mismatch": mismatch,
        "count_mismatch": count_mismatch,
        "records_beyond_manifest": extra,
        "total_chars": total_chars,
    }


def stats(root):
    manifest_path = root / "MANIFEST.jsonl"
    roles, days = {}, {}
    dup_text = {}
    secret = pii = 0
    total = 0
    total_chars = 0
    total_bytes = 0
    first_by_text = {}
    with manifest_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            total += 1
            roles[e["role"]] = roles.get(e["role"], 0) + 1
            day = e.get("date_local") or "unknown"
            days[day] = days.get(day, 0) + 1
            total_chars += e.get("char_len", 0)
            total_bytes += e.get("text_bytes", 0)
            if e.get("secret"):
                secret += 1
            if e.get("pii_hits"):
                pii += 1
            key = e.get("text_sha256")
            if key:
                dup_text[key] = dup_text.get(key, 0) + 1
                first_by_text.setdefault(key, e)

    dup_groups = {k: v for k, v in dup_text.items() if v > 1}
    unique_by_role = {}
    unique_user = 0
    unique_user_cjk = 0
    unique_days = set()
    for entry in first_by_text.values():
        role = entry.get("role")
        unique_by_role[role] = unique_by_role.get(role, 0) + 1
        if entry.get("date_local"):
            unique_days.add(entry["date_local"])
        if role == "user" and entry.get("cjk_len", 0) >= 2 and entry.get("char_len", 0) >= 4:
            unique_user += 1
            unique_user_cjk += entry.get("cjk_len", 0)
    streams = list((root / STREAM_DIR).glob("*.jsonl.gz"))
    compressed = sum(f.stat().st_size for f in streams)
    return {
        "records": total,
        "roles": roles,
        "days_covered": len([d for d in days if d != "unknown"]),
        "first_day": min([d for d in days if d != "unknown"], default=None),
        "last_day": max([d for d in days if d != "unknown"], default=None),
        "total_chars": total_chars,
        "text_bytes": total_bytes,
        "compressed_bytes": compressed,
        "compression_ratio": round(total_bytes / compressed, 2) if compressed else None,
        "duplicate_text_groups": len(dup_groups),
        "duplicate_text_records": sum(dup_groups.values()),
        "repetition_factor": round(total / len(first_by_text), 2) if first_by_text else None,
        "unique_texts": len(first_by_text),
        "unique_by_role": unique_by_role,
        "unique_days_covered": len(unique_days),
        "unique_user_utterances": unique_user,
        "unique_user_cjk_chars": unique_user_cjk,
        "secret_flagged": secret,
        "pii_flagged": pii,
        "streams": len(streams),
        "top_days": sorted(days.items(), key=lambda kv: -kv[1])[:5],
    }


def export(root, day=None, session=None, out=None):
    out = out or "-"
    handle = sys.stdout if out == "-" else open(out, "w", encoding="utf-8")
    count = 0
    try:
        for stream in sorted((root / "sessions").glob("*.jsonl.gz")):
            if session and stream.name.replace(".jsonl.gz", "") != session.replace(".jsonl", ""):
                continue
            for record in gz_read(stream):
                if day and record.get("date_local") != day:
                    continue
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                count += 1
    finally:
        if handle is not sys.stdout:
            handle.close()
    return count


def forget(root, session):
    """按会话删除：源流、manifest 条目、ledger 一起删，并留删除记录。"""
    name = session if session.endswith(".jsonl") else session + ".jsonl"
    stream = root / "sessions" / (name.replace(".jsonl", "") + ".jsonl.gz")
    if not stream.exists():
        return {"removed": False, "reason": "session not archived"}
    removed_ids = {r["record_id"] for r in gz_read(stream)}
    stream.unlink()
    manifest_path = root / "MANIFEST.jsonl"
    kept, dropped = [], 0
    if manifest_path.exists():
        with manifest_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    if json.loads(line).get("record_id") in removed_ids:
                        dropped += 1
                        continue
                except ValueError:
                    pass
                kept.append(line if line.endswith("\n") else line + "\n")
        tmp = manifest_path.with_name("MANIFEST.jsonl.tmp")
        tmp.write_text("".join(kept), encoding="utf-8")
        os.replace(tmp, manifest_path)
    ledger = read_ledger(root)
    ledger["sources"].pop(name, None)
    ledger.setdefault("deletions", []).append({
        "session_file": name,
        "records": len(removed_ids),
        "at": datetime.now(timezone.utc).isoformat(),
    })
    write_json(root / "ledger.json", ledger)
    return {"removed": True, "records": len(removed_ids), "manifest_dropped": dropped}


def orphans(root, memories_root):
    """找出指向已消失原文的派生记忆（删除传播的核心检查）。"""
    import re as _re

    ids = {e["record_id"] for e in _iter_manifest(root)}
    block = _re.compile(r"```stellamem\s*\n(.*?)\n```", _re.S)
    scanned = referencing = 0
    missing = []
    for rel in ["system/memory-inbox.md", "core/preferences.md", "core/identity.md",
                "core/user-core.md", "long-term/projects.md", "long-term/decisions.md",
                "long-term/lessons.md", "semantic/facts.md", "semantic/profile.md"]:
        path = Path(memories_root) / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in block.finditer(text):
            try:
                claim = json.loads(match.group(1))
            except ValueError:
                continue
            scanned += 1
            prov = claim.get("provenance") or {}
            rid = prov.get("record_id")
            if prov.get("provider") != "raw" and not rid:
                continue
            referencing += 1
            if not rid or rid not in ids:
                missing.append({"claim_id": claim.get("claim_id"), "file": rel,
                                "record_id": rid, "source_ref": prov.get("source_ref")})
    return {
        "claims_scanned": scanned,
        "claims_referencing_raw": referencing,
        "orphaned_claims": len(missing),
        "orphans": missing[:50],
    }


def _iter_manifest(root):
    path = Path(root) / "MANIFEST.jsonl"
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def main(argv=None):
    parser = argparse.ArgumentParser(description="Stellamem 原始档案层")
    parser.add_argument("command",
                        choices=["ingest", "verify", "stats", "export", "forget", "orphans"])
    parser.add_argument("--root", default=os.environ.get(
        "STELLAMEM_RAW", r"<USER_HOME>\.dsh\memories\_raw"))
    parser.add_argument("--memories", default=os.environ.get(
        "STELLAMEM_MEMORIES", r"<USER_HOME>\.dsh\memories"))
    parser.add_argument("--sessions", default=os.environ.get(
        "STELLAMEM_SESSIONS", r"<USER_HOME>\.openclaw\agents\main\sessions"))
    parser.add_argument("--roles", default=",".join(DEFAULT_ROLES))
    parser.add_argument("--tz-offset", type=int, default=8)
    parser.add_argument("--day")
    parser.add_argument("--session")
    parser.add_argument("--out")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true",
                        help="忽略 ledger 指纹重新解析（用于修复被损坏的流）")
    args = parser.parse_args(argv)

    root = Path(args.root)
    if args.command == "ingest":
        roles = tuple(r.strip() for r in args.roles.split(",") if r.strip())
        report = ingest(root, Path(args.sessions), roles, args.tz_offset, args.dry_run, args.force)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if report["added"] or args.dry_run else 0
    if args.command == "verify":
        result = verify(root)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result.get("ok") else 1
    if args.command == "stats":
        print(json.dumps(stats(root), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "export":
        count = export(root, args.day, args.session, args.out)
        print(json.dumps({"exported": count, "out": args.out or "-"}, ensure_ascii=False))
        return 0
    if args.command == "forget":
        if not args.session:
            print("PROBLEM: forget needs --session")
            return 2
        print(json.dumps(forget(root, args.session), ensure_ascii=False))
        return 0
    if args.command == "orphans":
        print(json.dumps(orphans(root, Path(args.memories)), ensure_ascii=False, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
