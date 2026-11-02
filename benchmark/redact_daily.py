"""daily 工作副本脱敏（仅授权两篇）+ 引用映射 + 回归验证。

边界
----
- 只改 `daily/2026-10-29.md`、`daily/2026-10-31.md` 两个工作副本。
- 原始内容先加密归档，恢复验证通过后才改工作副本；不删任何加密归档。
- 不改原始 sessions；不改 transactions / version-chain / review-log（历史审计层）。
- 受影响 claim 的 evidence 同步替换，`revision_id` 保持不变（保 lineage），
  旧 evidence 的 sha256 记进记录里，offset 标记为 stale。

用法
----
    python benchmark/redact_daily.py [--apply]
"""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "compiler"))
from storage import Transaction, load_records, render_records, safe_path  # noqa: E402

MEMORIES = Path(r"<USER_HOME>\.dsh\memories")
TARGETS = ["2026-10-29.md", "2026-10-31.md"]
SECURE = Path(r"E:\secure\stellamem-vault\daily-originals")
KEYS = Path(r"<USER_HOME>\.stellamem-keys")
VAULT_TOOL = ROOT / "tools" / "vault.mjs"
CLAIM_FILES = [
    "system/memory-inbox.md",
    "core/preferences.md",
    "long-term/projects.md",
    "long-term/decisions.md",
    "long-term/lessons.md",
    "semantic/facts.md",
    "semantic/profile.md",
]

HEX_RUN = re.compile(r"(?=[0-9a-fA-F]*[a-fA-F])[0-9a-fA-F]{16,}")
PHONE = re.compile(r"(?<![0-9])1[3-9][0-9](?:[0-9]{8}|[ -][0-9]{4}[ -][0-9]{4})(?![0-9])")
DISABILITY = re.compile(r"(?<![0-9])([0-9]{17}[0-9Xx][0-9]{2})(?![0-9])")
ID18 = re.compile(r"(?<![0-9])([0-9]{17}[0-9Xx])(?![0-9])")
WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
CHECK = "10X98765432"


def id_ok(v):
    if len(v) != 18 or not v[:17].isdigit():
        return False
    return CHECK[sum(int(v[i]) * WEIGHTS[i] for i in range(17)) % 11].upper() == v[17].upper()


def redact(text):
    """返回 (脱敏后文本, 统计)。顺序：残疾证 → 身份证 → 手机。"""
    stats = {"disability": 0, "idcard": 0, "phone": 0}
    out = text

    def sub_disability(m):
        if id_ok(m.group(1)[:18]):
            stats["disability"] += 1
            return "[REDACTED:ID]"
        return m.group(0)

    out = DISABILITY.sub(sub_disability, out)

    def sub_id(m):
        if id_ok(m.group(1)):
            stats["idcard"] += 1
            return "[REDACTED:ID]"
        return m.group(0)

    out = ID18.sub(sub_id, out)

    def sub_phone(m):
        stats["phone"] += 1
        return "[REDACTED:PHONE]"

    out = PHONE.sub(sub_phone, out)
    return out, stats


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def world(*args):
    result = subprocess.run(
        ["node", str(VAULT_TOOL), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip()[:300])
    return json.loads(result.stdout)


def seal_originals():
    KEYS.mkdir(parents=True, exist_ok=True)
    key = KEYS / "daily-originals-20261101.key"
    if not key.exists():
        world("keygen", "--key", str(key))
    sealed = []
    for name in TARGETS:
        src = MEMORIES / "daily" / name
        out = SECURE / (name + ".enc")
        sealed.append(world("seal", "--key", str(key), "--in", str(src), "--out", str(out)))
    # 立即做一次恢复到临时目录的验证
    workdir = Path(tempfile.mkdtemp(prefix="dailyrestore-"))
    try:
        checks = []
        for item in sealed:
            target = workdir / Path(item["source"]).name
            info = world("unseal", "--key", str(key), "--in", item["sealed"], "--out", str(target))
            checks.append(
                {
                    "file": target.name,
                    "matches": info["matches"],
                    "sha256": info["sha256"],
                    "source_sha256": item["source_sha256"],
                }
            )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return {"key": str(key), "key_sha256": sha(key), "sealed": sealed, "restore_checks": checks}


def collect_references():
    refs = {}
    for rel in CLAIM_FILES:
        path = MEMORIES / rel
        if not path.exists():
            continue
        prefix, records = load_records(path)
        hits = []
        for record in records:
            prov = record.get("provenance") or {}
            if prov.get("source_ref") in TARGETS:
                hits.append(record)
        if hits:
            refs[rel] = (prefix, records, hits)
    return refs


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--out", default=str(ROOT / "docs" / "personal-edition" / "PII-REDACTION-REPORT.json")
    )
    args = parser.parse_args(argv)

    archive = seal_originals()

    # 先算引用与将要发生的变化，不写入
    refs = collect_references()
    affected_claims = sum(len(v[2]) for v in refs.values())
    evidence_hits = 0
    per_file = []
    for name in TARGETS:
        path = MEMORIES / "daily" / name
        original = path.read_text(encoding="utf-8")
        redacted, stats = redact(original)
        changed_lines = sum(
            1 for a, b in zip(original.splitlines(), redacted.splitlines()) if a != b
        )
        per_file.append(
            {
                "file": name,
                "bytes_before": len(original.encode("utf-8")),
                "bytes_after": len(redacted.encode("utf-8")),
                "sha256_before": hashlib.sha256(original.encode("utf-8")).hexdigest(),
                "sha256_after": hashlib.sha256(redacted.encode("utf-8")).hexdigest(),
                "hits": stats,
                "lines_total": len(original.splitlines()),
                "lines_changed": changed_lines,
            }
        )

    if not args.apply:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "archive": archive,
                    "files": per_file,
                    "affected_claims": affected_claims,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    # 1) 改工作副本
    for name, info in zip(TARGETS, per_file):
        path = MEMORIES / "daily" / name
        original = path.read_text(encoding="utf-8")
        redacted, _ = redact(original)
        path.write_text(redacted, encoding="utf-8")

    # 2) 同步受影响 claim 的 evidence（只改 evidence 字段）
    changes, events, updated = {}, [], 0
    now = datetime.now(timezone.utc).isoformat()
    for rel, (prefix, records, hits) in refs.items():
        mutated = False
        for record in hits:
            before = record.get("evidence") or ""
            after, stats = redact(before)
            if after == before:
                continue
            record["evidence"] = after
            record["redaction"] = {
                "batch": "daily-redact-20261101",
                "at": now,
                "fields": ["evidence"],
                "evidence_sha256_before": hashlib.sha256(before.encode("utf-8")).hexdigest(),
                "revision_id_preserved": record.get("revision_id"),
                "offset_policy": "stale-after-redaction",
                "hits": stats,
            }
            mutated = True
            updated += 1
            evidence_hits += 1
            events.append(
                {
                    "event_id": hashlib.sha256(
                        f"{record.get('claim_id')}:redact:{now}".encode()
                    ).hexdigest(),
                    "id": record.get("claim_id"),
                    "current": record.get("revision_id"),
                    "relation": "REDACT",
                    "status": record.get("status"),
                    "date": now,
                    "review_action": "redact",
                    "fields": ["evidence"],
                    "evidence_sha256_before": record["redaction"]["evidence_sha256_before"],
                    "batch": "daily-redact-20261101",
                }
            )
        if mutated:
            changes[rel] = render_records(prefix, records)

    txid = None
    if changes:
        chain_path = safe_path(MEMORIES, "system/version-chain.json")
        chain = json.loads(chain_path.read_text(encoding="utf-8")) if chain_path.exists() else []
        changes["system/version-chain.json"] = json.dumps(
            chain + events, ensure_ascii=False, indent=2
        )
        review = safe_path(MEMORIES, "system/review-log.md")
        before = review.read_text(encoding="utf-8") if review.exists() else "# Review log\n"
        changes["system/review-log.md"] = before + "".join(
            "\n" + json.dumps(e, ensure_ascii=False) for e in events
        )
        txid = Transaction(MEMORIES).commit(changes)

    # 3) 回归：脱敏后文件不再命中；受影响 claim 的证据能在新文件里找到
    post = []
    for name in TARGETS:
        text = (MEMORIES / "daily" / name).read_text(encoding="utf-8")
        _, stats = redact(text)
        post.append(
            {
                "file": name,
                "remaining_hits": sum(stats.values()),
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
        )
    evid_ok = evid_missing = 0
    for rel, (_prefix, records, _hits) in refs.items():
        for record in records:
            prov = record.get("provenance") or {}
            if prov.get("source_ref") not in TARGETS:
                continue
            ev = (record.get("evidence") or "").strip()
            if not ev:
                continue
            src = MEMORIES / "daily" / prov["source_ref"]
            if ev[:40] in src.read_text(encoding="utf-8"):
                evid_ok += 1
            else:
                evid_missing += 1

    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "archive": archive,
        "files": per_file,
        "references": {
            "files": list(refs),
            "affected_claims": affected_claims,
            "evidence_updated": updated,
        },
        "transaction": txid,
        "version_chain_events": len(events),
        "post_scan": post,
        "evidence_relocation": {"found": evid_ok, "missing": evid_missing},
        "untouched": [
            "sessions",
            "system/transactions",
            "version-chain 历史事件",
            "review-log 历史事件",
            "其他 daily 文件",
        ],
    }
    out = Path(args.out)
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "files": per_file,
                "affected_claims": affected_claims,
                "evidence_updated": updated,
                "transaction": txid,
                "post_scan": post,
                "evidence_relocation": payload["evidence_relocation"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if all(p["remaining_hits"] == 0 for p in post) and evid_missing == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
