"""第三阶段：365 天长期运行模拟。

覆盖：批量写入、重复导入去重、崩溃中断恢复、索引重建一致性、备份/恢复、规模下的检索延迟。
全部在沙箱运行，不触碰生产记忆。
"""

import json
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

import quarantine_vault as vault
import reliability as rel
import search as S
from indexer import Index
from storage import Transaction, load_records, render_records

SANDBOX = Path("./stellamem-data-sandbox/sim365")
ROOT = SANDBOX / "memories"
DB = SANDBOX / "index.db"
VAULT = SANDBOX / "vault"

TOPICS = [
    "记忆系统",
    "视频剪辑",
    "MBTI 内容",
    "星契物语",
    "本地模型",
    "备份恢复",
    "知识库",
    "时间线",
]
KINDS = ["project", "decision", "preference", "lesson", "fact", "procedure"]
VERBS = [
    "推进到第二阶段",
    "改用 SQLite 索引",
    "偏好大白话",
    "踩了编码的坑",
    "支持中文检索",
    "先备份再迁移",
]


def seed_day(day: date, idx: int) -> list[dict]:
    out = []
    for n in range(2):
        kind = KINDS[(idx * 2 + n) % len(KINDS)]
        topic = TOPICS[(idx + n) % len(TOPICS)]
        verb = VERBS[(idx * 3 + n) % len(VERBS)]
        text = f"{topic}{verb}，第{idx}天记录{n}"
        out.append(
            {
                "schema_version": 2,
                "claim_id": f"c-{day.isoformat()}-{n}",
                "revision_id": f"r-{day.isoformat()}-{n}",
                "event_id": f"e-{day.isoformat()}-{n}",
                "kind": kind,
                "scope": "global",
                "subject": topic,
                "predicate": f"day{idx}",
                "value": text,
                "evidence": text,
                "source": "user",
                "confidence": 0.9,
                "importance": 0.5,
                "sensitivity": "PRIVATE",
                "write_policy": "allow",
                "injection_policy": "recall",
                "evidence_verified": True,
                "status": "active" if n == 0 else "pending",
                "relation": "NEW",
                "auto_write": True,
                "observed_at": f"{day.isoformat()}T08:00:00+00:00",
                "provenance": {
                    "timestamp": f"{day.isoformat()}T0{8 + n}:00:00",
                    "record_id": f"rm-{day.isoformat()}-{n}",
                },
            }
        )
    return out


def write_day(day: date, idx: int) -> int:
    path = ROOT / "long-term" / "projects.md"
    prefix, records = load_records(path) if path.exists() else ("# 项目\n", [])
    existing = {r["revision_id"] for r in records}
    added = 0
    for rec in seed_day(day, idx):
        if rec["revision_id"] in existing:
            continue
        records.append(rec)
        added += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_records(prefix, records), encoding="utf-8")
    return added


def main():
    if SANDBOX.exists():
        import shutil

        shutil.rmtree(SANDBOX)
    SANDBOX.mkdir(parents=True)
    (ROOT / "system").mkdir(parents=True)
    (ROOT / "long-term").mkdir(parents=True)

    result = {}
    start = date(2026, 1, 1)

    # ---------- 1. 365 天写入 ----------
    t0 = time.time()
    written = 0
    for i in range(365):
        written += write_day(start + timedelta(days=i), i)
    result["ingest"] = {"days": 365, "claims": written, "seconds": round(time.time() - t0, 2)}

    # ---------- 2. 重复导入 ----------
    t0 = time.time()
    dup_added = 0
    for i in range(60):
        dup_added += write_day(start + timedelta(days=i), i)
    result["duplicate_import"] = {
        "reimported_days": 60,
        "new_rows": dup_added,
        "seconds": round(time.time() - t0, 3),
        "idempotent": dup_added == 0,
    }

    # ---------- 3. 索引构建与规模 ----------
    idx = Index(DB)
    t0 = time.time()
    _ = idx.build(ROOT)
    build_s = time.time() - t0
    stats = idx.stats()
    result["index"] = {
        "records": stats["records"],
        "build_seconds": round(build_s, 2),
        "db_mb": round(stats["db_bytes"] / 1024 / 1024, 2),
    }

    # ---------- 4. 检索延迟 ----------
    lat = []
    for q in [
        "记忆系统推进",
        "视频剪辑偏好",
        "备份恢复踩坑",
        "星契物语第二阶段",
        "本地模型选择",
    ] * 6:
        t = time.perf_counter()
        S.search(idx.con, q, limit=8)
        lat.append((time.perf_counter() - t) * 1000)
    lat.sort()
    result["search_latency"] = {
        "samples": len(lat),
        "p50_ms": round(statistics.median(lat), 2),
        "p95_ms": round(lat[int(len(lat) * 0.95) - 1], 2),
        "max_ms": round(lat[-1], 2),
    }

    # ---------- 5. 崩溃恢复 ----------
    # 复刻真实中断：journal 已落盘、文件仍是 before 状态，进程被杀。
    import uuid

    target = ROOT / "long-term" / "projects.md"
    # 必须用原始字节：Windows 下 text 模式会把 CRLF 规范成 LF，导致哈希对不上。
    before_bytes = target.read_bytes()
    before_text = before_bytes.decode("utf-8")
    after_bytes = before_bytes + b"\n<!-- crash-recovery-probe -->\n"
    after_text = after_bytes.decode("utf-8")
    txid = uuid.uuid4().hex
    entry = {
        "schema_version": 2,
        "id": txid,
        "files": {
            "long-term/projects.md": {
                "before": before_text,
                "before_hash": __import__("hashlib").sha256(before_bytes).hexdigest(),
                "after": after_text,
                "after_hash": __import__("hashlib").sha256(after_bytes).hexdigest(),
            }
        },
    }
    journal = ROOT / "system" / "transaction.json"
    journal.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
    Transaction(ROOT).recover()
    result["crash_recovery"] = {
        "journal_written": True,
        "completed_to_after": "crash-recovery-probe" in target.read_text(encoding="utf-8"),
        "journal_cleared": not journal.exists(),
    }
    # 未完成事务不允许继续写入
    journal.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
    blocked = False
    try:
        Transaction(ROOT).commit({"long-term/projects.md": "x"})
    except RuntimeError:
        blocked = True
    finally:
        journal.unlink(missing_ok=True)
    result["crash_recovery"]["write_blocked_while_pending"] = blocked

    # ---------- 6. 中断后索引重建一致性 ----------
    before = idx.stats()["records"]
    idx.close()
    DB.unlink(missing_ok=True)
    idx2 = Index(DB)
    idx2.build(ROOT)
    after = idx2.stats()["records"]
    result["index_rebuild"] = {"before": before, "after": after, "identical": before == after}

    # ---------- 7. 备份 / 恢复 ----------
    arch = SANDBOX / "snap.zip"
    manifest = rel.snapshot(ROOT, arch)
    check = rel.verify_snapshot(arch)
    restored = SANDBOX / "restored"
    rel.restore_snapshot(arch, restored)
    same = (restored / "long-term" / "projects.md").read_bytes() == target.read_bytes()
    result["backup_restore"] = {
        "files": len(manifest["files"]),
        "verify_ok": check["ok"],
        "restored_identical": same,
        "archive_mb": round(arch.stat().st_size / 1024 / 1024, 2),
    }

    # ---------- 8. 隔离保险库规模测试 ----------
    _, records = load_records(ROOT / "long-term" / "projects.md")
    for i in range(0, 40):
        records[i]["status"] = "quarantined"
    (ROOT / "long-term" / "projects.md").write_text(
        render_records("# 项目\n", records), encoding="utf-8"
    )
    t0 = time.time()
    sealed = vault.seal(ROOT, VAULT, batch="sim")
    seal_s = time.time() - t0
    probes = vault.probes_for(vault.collect(ROOT)) or [
        r["value"][:24] for r in records[:40] if len(r["value"]) >= 24
    ]
    residual = vault.residual_scan(ROOT, probes)
    result["vault"] = {
        "sealed": sealed["moved"],
        "seconds": round(seal_s, 2),
        "derived_residual": residual["derived_total"],
        "verify_ok": vault.verify(ROOT, VAULT, "sim")["ok"],
    }

    # ---------- 9. 落盘 ----------
    out = Path("./stellamem-data/sim365.json")
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))
    idx2.close()


if __name__ == "__main__":
    main()
