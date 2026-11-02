"""隔离旧记忆：不删除、不覆盖、可追踪、可恢复。

为什么用"状态 + 清单"两层
------------------------
1. 数据层：把记录 `status` 从 `active` 改成 `quarantined`。注入闸门只认 `active`，
   所以立刻生效，不需要重启客户端。记录正文一个字节都不动，事务层留前后快照。
2. 治理层：`system/quarantine.json` 记 claim_id 清单 + 每条记录的内容 sha256。
   插件读取它做二次拦截；文件损坏时**默认拒绝注入**（fail closed）。

恢复：重新审核通过后 `restore --ids` 把状态改回 active，并留下审计事件。

CLI
---
    python compiler/quarantine.py plan   [--root ...] [--csv ...]
    python compiler/quarantine.py apply  [--batch q-20261101] [--reason ...]
    python compiler/quarantine.py verify
    python compiler/quarantine.py restore --ids c-xxx,c-yyy
"""

import argparse
import csv
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from storage import Transaction, load_records, render_records, safe_path

TARGET_FILES = [
    "core/preferences.md",
    "long-term/projects.md",
    "long-term/decisions.md",
    "long-term/lessons.md",
    "semantic/facts.md",
    "semantic/profile.md",
    "core/identity.md",
    "core/user-core.md",
]
QUARANTINE = "system/quarantine.json"
CSV_FIELDS = [
    "claim_id", "revision_id", "kind", "status", "file", "source", "speaker",
    "provider", "source_ref", "record_id", "entry_index", "precision",
    "timestamp", "observed_at", "confidence", "sensitivity", "injection_policy",
    "write_policy", "evidence_verified", "subject", "predicate", "value",
    "evidence", "record_sha256",
]


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


VOLATILE_KEYS = {"status", "quarantined_at", "quarantine_batch", "quarantine_reason", "restored_at"}


def record_hash(record):
    """内容指纹：排除隔离动作本身会改的字段，只对记忆内容负责。"""
    body = {k: v for k, v in record.items() if k not in VOLATILE_KEYS}
    return sha256_text(json.dumps(body, ensure_ascii=False, sort_keys=True))


def load(root):
    """返回 [(rel, prefix, records)]，只读。"""
    out = []
    for rel in TARGET_FILES:
        path = safe_path(root, rel)
        if not path.exists():
            continue
        prefix, records = load_records(path)
        out.append((rel, prefix, records))
    return out


def row(rel, record):
    prov = record.get("provenance") or {}
    return {
        "claim_id": record.get("claim_id"),
        "revision_id": record.get("revision_id"),
        "kind": record.get("kind"),
        "status": record.get("status"),
        "file": rel,
        "source": record.get("source"),
        "speaker": prov.get("speaker"),
        "provider": prov.get("provider"),
        "source_ref": prov.get("source_ref"),
        "record_id": prov.get("record_id"),
        "entry_index": prov.get("entry_index"),
        "precision": prov.get("precision"),
        "timestamp": prov.get("timestamp"),
        "observed_at": record.get("observed_at"),
        "confidence": record.get("confidence"),
        "sensitivity": record.get("sensitivity"),
        "injection_policy": record.get("injection_policy"),
        "write_policy": record.get("write_policy"),
        "evidence_verified": record.get("evidence_verified"),
        "subject": record.get("subject"),
        "predicate": record.get("predicate"),
        "value": record.get("value"),
        "evidence": record.get("evidence"),
        "record_sha256": record_hash(record),
    }


def active_records(root):
    found = []
    for rel, _prefix, records in load(root):
        for record in records:
            if record.get("status") == "active":
                found.append((rel, record))
    return found


def write_csv(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for item in rows:
            writer.writerow(item)
    return sha256_text(path.read_text(encoding="utf-8-sig"))


def plan(root, csv_path, batch="q-20261101", reason=""):
    rows = [row(rel, record) for rel, record in active_records(root)]
    csv_sha = write_csv(csv_path, rows)
    payload = {
        "schema_version": 1,
        "batch": batch,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason or "P1 收尾：旧 active 来源未经原文核验，暂停注入",
        "claim_ids": [r["claim_id"] for r in rows],
        "records": [
            {
                "claim_id": r["claim_id"],
                "file": r["file"],
                "revision_id": r["revision_id"],
                "record_sha256": r["record_sha256"],
            }
            for r in rows
        ],
        "csv": str(csv_path),
        "csv_sha256": csv_sha,
    }
    return rows, payload, csv_sha


def apply(root, payload):
    """把 active 改成 quarantined。正文不动，事务层留快照。"""
    ids = set(payload["claim_ids"])
    changes, events, touched = {}, [], 0
    now = datetime.now(timezone.utc).isoformat()
    for rel, prefix, records in load(root):
        mutated = False
        for record in records:
            if record.get("claim_id") not in ids or record.get("status") != "active":
                continue
            record["status"] = "quarantined"
            record["quarantined_at"] = now
            record["quarantine_batch"] = payload["batch"]
            record["quarantine_reason"] = payload["reason"]
            mutated = True
            touched += 1
            events.append(
                {
                    "event_id": sha256_text(
                        json.dumps({"id": record.get("claim_id"), "at": now, "action": "quarantine"})
                    ),
                    "id": record.get("claim_id"),
                    "current": record.get("revision_id"),
                    "relation": "QUARANTINE",
                    "status": "quarantined",
                    "date": now,
                    "provenance": record.get("provenance", {}),
                    "evidence": record.get("evidence", ""),
                    "review_action": "quarantine",
                    "batch": payload["batch"],
                }
            )
        if mutated:
            changes[rel] = render_records(prefix, records)
    if not changes:
        return {"quarantined": 0, "transaction": None}

    chain_path = safe_path(root, "system/version-chain.json")
    chain = json.loads(chain_path.read_text(encoding="utf-8")) if chain_path.exists() else []
    changes["system/version-chain.json"] = json.dumps(chain + events, ensure_ascii=False, indent=2)
    review = safe_path(root, "system/review-log.md")
    before = review.read_text(encoding="utf-8") if review.exists() else "# Review log\n"
    changes["system/review-log.md"] = before + "".join(
        "\n" + json.dumps(e, ensure_ascii=False) for e in events
    )
    changes[QUARANTINE] = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    txid = Transaction(root).commit(changes)
    return {"quarantined": touched, "transaction": txid, "files": sorted(changes)}


def verify(root):
    qpath = safe_path(root, QUARANTINE)
    if not qpath.exists():
        return {"ok": False, "reason": "quarantine.json missing"}
    payload = json.loads(qpath.read_text(encoding="utf-8"))
    ids = set(payload.get("claim_ids") or [])
    expected = {r["claim_id"]: r["record_sha256"] for r in payload.get("records", [])}

    by_id, wrong_status, active_left = {}, 0, 0
    active_ids = []
    for rel, _prefix, records in load(root):
        for record in records:
            cid = record.get("claim_id")
            if cid in ids:
                by_id[cid] = (rel, record)
                if record.get("status") != "quarantined":
                    wrong_status += 1
            elif record.get("status") == "active":
                active_left += 1
                active_ids.append(cid)

    missing = sorted(ids - set(by_id))
    hash_mismatch = []
    for cid, (rel, record) in by_id.items():
        # 状态字段会变，比较内容时排除隔离相关字段
        if record_hash(record) != expected.get(cid):
            hash_mismatch.append({"claim_id": cid, "file": rel})
    csv_path = Path(payload.get("csv") or "")
    csv_ok = csv_path.exists() and sha256_text(csv_path.read_text(encoding="utf-8-sig")) == payload.get(
        "csv_sha256")
    return {
        "ok": not missing and wrong_status == 0 and not hash_mismatch and csv_ok,
        "batch": payload.get("batch"),
        "quarantined_expected": len(ids),
        "quarantined_found": len(by_id),
        "missing": missing,
        "wrong_status": wrong_status,
        "hash_mismatch": hash_mismatch,
        "active_remaining": active_left,
        "active_remaining_ids": active_ids,
        "csv_ok": csv_ok,
        "csv": str(csv_path),
    }


def restore(root, ids, batch="restore"):
    wanted = set(ids)
    changes, events, moved = {}, [], 0
    now = datetime.now(timezone.utc).isoformat()
    for rel, prefix, records in load(root):
        mutated = False
        for record in records:
            if record.get("claim_id") not in wanted or record.get("status") != "quarantined":
                continue
            record["status"] = "active"
            record["restored_at"] = now
            record.pop("quarantine_reason", None)
            mutated = True
            moved += 1
            events.append(
                {
                    "event_id": sha256_text(json.dumps({"id": record.get("claim_id"), "at": now,
                                                        "action": "restore"})),
                    "id": record.get("claim_id"),
                    "current": record.get("revision_id"),
                    "relation": "RESTORE",
                    "status": "active",
                    "date": now,
                    "review_action": "restore",
                    "batch": batch,
                }
            )
        if mutated:
            changes[rel] = render_records(prefix, records)
    if not changes:
        return {"restored": 0}
    chain_path = safe_path(root, "system/version-chain.json")
    chain = json.loads(chain_path.read_text(encoding="utf-8")) if chain_path.exists() else []
    changes["system/version-chain.json"] = json.dumps(chain + events, ensure_ascii=False, indent=2)
    review = safe_path(root, "system/review-log.md")
    before = review.read_text(encoding="utf-8") if review.exists() else "# Review log\n"
    changes["system/review-log.md"] = before + "".join(
        "\n" + json.dumps(e, ensure_ascii=False) for e in events
    )
    # 清单里去掉已恢复的 id，保持清单与状态一致
    qpath = safe_path(root, QUARANTINE)
    if qpath.exists():
        payload = json.loads(qpath.read_text(encoding="utf-8"))
        payload["claim_ids"] = [c for c in payload.get("claim_ids", []) if c not in wanted]
        payload["records"] = [r for r in payload.get("records", []) if r["claim_id"] not in wanted]
        changes[QUARANTINE] = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    return {"restored": moved, "transaction": Transaction(root).commit(changes)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="旧记忆隔离")
    parser.add_argument("command", choices=["plan", "apply", "verify", "restore", "list"])
    parser.add_argument("--root", default=os.environ.get(
        "STELLAMEM_MEMORIES", r"<USER_HOME>\.dsh\memories"))
    parser.add_argument("--csv", default=None)
    parser.add_argument("--batch", default="q-20261101")
    parser.add_argument("--reason", default="")
    parser.add_argument("--ids", default="")
    parser.add_argument("--execute", action="store_true",
                        help="apply 需要显式加 --execute，避免误跑")
    args = parser.parse_args(argv)
    root = Path(args.root)

    if args.command == "list":
        rows = [row(rel, r) for rel, r in active_records(root)]
        print(json.dumps({"active": len(rows), "ids": [r["claim_id"] for r in rows]},
                         ensure_ascii=False, indent=2))
        return 0

    if args.command == "plan":
        csv_path = Path(args.csv) if args.csv else Path("ACTIVE-33-QUARANTINE.csv")
        rows, payload, csv_sha = plan(root, csv_path, args.batch, args.reason)
        print(json.dumps({"records": len(rows), "csv": str(csv_path), "csv_sha256": csv_sha},
                         ensure_ascii=False, indent=2))
        return 0

    if args.command == "apply":
        qpath = safe_path(root, QUARANTINE)
        if qpath.exists():
            payload = json.loads(qpath.read_text(encoding="utf-8"))
            payload.setdefault("claim_ids", [])
        else:
            csv_path = Path(args.csv) if args.csv else Path("ACTIVE-33-QUARANTINE.csv")
            rows, payload, _ = plan(root, csv_path, args.batch, args.reason)
        if args.ids:
            wanted = [x.strip() for x in args.ids.split(",") if x.strip()]
            payload["claim_ids"] = wanted
            payload["records"] = [r for r in payload.get("records", []) if r["claim_id"] in wanted]
        if not args.execute:
            print(json.dumps({"dry_run": True, "would_quarantine": len(payload["claim_ids"])},
                             ensure_ascii=False))
            return 0
        print(json.dumps(apply(root, payload), ensure_ascii=False, indent=2))
        return 0

    if args.command == "verify":
        result = verify(root)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1

    if args.command == "restore":
        ids = [x.strip() for x in args.ids.split(",") if x.strip()]
        if not ids:
            print("PROBLEM: restore needs --ids")
            return 2
        print(json.dumps(restore(root, ids, args.batch), ensure_ascii=False, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
