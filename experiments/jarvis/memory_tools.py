"""Explicit owner notes using the pinned Stellamem store, inbox and projection."""

import contextlib
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import uuid
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE.parents[1] / "compiler"))
from claim_normalizer import normalize_claim
from deletion_chain import forget_record
from inbox import cmd_approve
from security import contains_secret
from storage import Transaction, safe_path, writer_lock
from store import INBOX, MemoryStore
from subjects import subject_of


def verified(root, rec):
    p = rec.get("provenance", {})
    ref = p.get("source_ref", "")
    if rec.get("source") != "user":
        return False
    if p.get("provider") != "jarvis-console" or p.get("speaker") != "user":
        return False
    if not re.fullmatch(r"notes/[a-f0-9]{32}\.txt", ref):
        return False
    path = safe_path(root, ref)
    if not path.is_file():
        return False
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest() == p.get("content_hash") and data.decode(
        "utf-8"
    ) == rec.get("evidence") == rec.get("value")


def candidates(root):
    return [
        r
        for r in MemoryStore(root).records.get(INBOX, [])
        if subject_of(r) == "user" and r.get("status") == "pending"
    ]


def operate(root, action, arg):
    with writer_lock(root):
        tx = Transaction(root)
        tx.recover()
        if action == "记下":
            if contains_secret(arg):
                raise ValueError("疑似密钥")
            if not arg or len(arg) > 500:
                raise ValueError("记忆长度须为 1–500")
            ref = "notes/" + uuid.uuid4().hex + ".txt"
            rec = normalize_claim(
                dict(
                    kind="fact",
                    subject="user",
                    predicate=arg,
                    value=arg,
                    evidence=arg,
                    source="user",
                    confidence=0.4,
                )
            )
            if rec["value"] != arg or rec["evidence"] != arg:
                raise ValueError("请使用单行、规范空格")
            rec.update(
                evidence_verified=True,
                provenance=dict(
                    provider="jarvis-console",
                    source_ref=ref,
                    speaker="user",
                    timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    content_hash=hashlib.sha256(arg.encode()).hexdigest(),
                ),
            )
            store = MemoryStore(root)
            store.ingest(rec)
            if store.counts["pending"] != 1:
                raise ValueError("未进入审核队列")
            store.commit({ref: arg})
            row = next(r for r in candidates(root) if r["provenance"]["source_ref"] == ref)
            return "待审核 " + row["revision_id"]
        if action == "待审核":
            return (
                "\n".join(
                    r["revision_id"] + " " + r["value"]
                    for r in candidates(root)
                    if verified(root, r)
                )
                or "无待审核记忆"
            )
        if action == "批准":
            matches = [r for r in candidates(root) if r["revision_id"] == arg and verified(root, r)]
            if len(matches) != 1:
                raise ValueError("候选不存在或原文核验失败")
            with contextlib.redirect_stdout(io.StringIO()) as out:
                cmd_approve(root, Namespace(id=arg))
            data = json.loads(out.getvalue())
            return f"已批准 {data['approved']} 回滚号 {data['transaction']}"
        if action == "撤销批准":
            # Limit rollback to actual approval transactions for this namespace.
            if not re.fullmatch(r"[a-f0-9]{32}", arg):
                raise ValueError("回滚号无效")
            entry = json.loads(
                safe_path(root, "system/transactions/" + arg + ".json").read_text("utf-8")
            )
            if not {INBOX, "system/review-log.md"} <= entry["files"].keys() or any(
                key.startswith("notes/") for key in entry["files"]
            ):
                raise ValueError("不是审批事务")
            tx.rollback(arg, yes=True)
            return "已撤销批准"
        store = MemoryStore(root)
        active = {
            r["claim_id"]: r
            for path, rows in store.records.items()
            if path != INBOX
            for r in rows
            if subject_of(r) == "user" and r.get("status") == "active"
        }
        if action == "遗忘":
            if arg not in active:
                raise ValueError("无此有效记忆")
            result = forget_record(root, arg)
            return "已停止召回，保留原文及历史 " + result["layers"]["live"]["action"]
        env = dict(os.environ)
        for key in ("STELLAMEM_PROJECT_DIAGNOSTICS_DIR", "NODE_OPTIONS"):
            env.pop(key, None)
        result = subprocess.run(
            ["node", str(HERE / "projection.mjs"), str(root)],
            input=arg,
            capture_output=True,
            encoding="utf-8",
            timeout=12,
            check=True,
            env=env,
        )
        lines = []
        for line in result.stdout.splitlines()[1:]:
            item = json.loads(line)
            record = active.get(item.get("id"))
            if record and verified(root, record) and record["value"] == item.get("value"):
                p = record["provenance"]
                lines.append(f"{record['value']} [{record['claim_id']}] 本人原文 {p['source_ref']}")
        return "\n".join(lines) or "无可核验记忆"
