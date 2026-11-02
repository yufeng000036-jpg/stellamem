"""P0 可复现度量：一次运行产出报告里所有数字，并对输入文件做指纹。

用法：
    python benchmark/p0_metrics.py [--root DIR] [--sessions DIR] [--out FILE]

输出：
    - 终端表格
    - JSON（默认 memories/benchmark/results/p0_metrics_<UTC日期>.json）

设计约束：
    - 只读。不写 memories 下的任何数据文件，只写 benchmark/results 下的度量结果。
    - 不打印任何 PII，只打印命中条数。
    - 所有数字都能由 inputs 里的文件重新算出；inputs 记录路径/字节/sha256。
"""

import argparse
import collections
import hashlib
import json
import os
import platform
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

BLOCK = re.compile(r"```stellamem\s*\n(.*?)\n\`\`\`", re.S)
SPEAKER = re.compile(r"(?m)^\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*(user|assistant|system|tool):")
ACTIVE_DIRS = ["core", "long-term", "semantic", "episodic"]
PROJECTION_FILES = [
    "core/preferences.md",
    "long-term/projects.md",
    "long-term/decisions.md",
    "long-term/lessons.md",
    "semantic/facts.md",
    "semantic/profile.md",
]
PII = re.compile(r"(?<![0-9])(1[3-9][0-9]{9}|[0-9]{17}[0-9Xx])(?![0-9])")


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            data = fh.read(chunk)
            if not data:
                break
            h.update(data)
    return h.hexdigest()


def digest_pairs(items):
    h = hashlib.sha256()
    for name, size, mtime in items:
        h.update(f"{name}:{size}:{int(mtime)}\n".encode())
    return h.hexdigest()


def load_blocks(path):
    records, bad = [], 0
    text = path.read_text(encoding="utf-8", errors="replace")
    for m in BLOCK.finditer(text):
        try:
            records.append(json.loads(m.group(1)))
        except ValueError:
            bad += 1
    return records, bad


def counter(items, key):
    c = collections.Counter()
    for it in items:
        v = it.get(key)
        if isinstance(v, (dict, list)):
            v = type(v).__name__
        c[str(v)] += 1
    return dict(sorted(c.items(), key=lambda kv: -kv[1]))


def provenance_speaker(items):
    c = collections.Counter()
    for it in items:
        c[str((it.get("provenance") or {}).get("speaker"))] += 1
    return dict(sorted(c.items(), key=lambda kv: -kv[1]))


def pct(n, d):
    return round(n / d * 100, 1) if d else 0.0


def dir_stats(path):
    files = [f for f in path.rglob("*") if f.is_file()] if path.exists() else []
    return {"files": len(files), "bytes": sum(f.stat().st_size for f in files)}


def collect(root, sessions):
    metrics = {}
    inputs = []

    inbox_path = root / "system" / "memory-inbox.md"
    inbox, inbox_bad = load_blocks(inbox_path) if inbox_path.exists() else ([], 0)
    if inbox_path.exists():
        inputs.append(
            {
                "role": "inbox",
                "path": str(inbox_path),
                "bytes": inbox_path.stat().st_size,
                "sha256": sha256_file(inbox_path),
            }
        )
    n = len(inbox)
    metrics["inbox"] = {
        "count": n,
        "unparsable_blocks": inbox_bad,
        "status": counter(inbox, "status"),
        "kind": counter(inbox, "kind"),
        "source": counter(inbox, "source"),
        "injection_policy": counter(inbox, "injection_policy"),
        "write_policy": counter(inbox, "write_policy"),
        "provenance_speaker": provenance_speaker(inbox),
        "evidence_present_pct": pct(sum(1 for c in inbox if (c.get("evidence") or "").strip()), n),
        "evidence_verified_pct": pct(
            sum(1 for c in inbox if c.get("evidence_verified") is True), n
        ),
        "sensitive_pct": pct(
            sum(
                1
                for c in inbox
                if c.get("sensitive")
                or str(c.get("sensitivity")).upper() in ("SENSITIVE", "SECRET")
            ),
            n,
        ),
        "user_sourced": sum(1 for c in inbox if c.get("source") == "user"),
    }

    records_all, files = [], []
    for d in ACTIVE_DIRS:
        for f in sorted((root / d).glob("*.md")) if (root / d).exists() else []:
            recs, bad = load_blocks(f)
            if recs:
                files.append(
                    {"file": str(f.relative_to(root)), "records": len(recs), "unparsable": bad}
                )
                for r in recs:
                    r["_file"] = str(f.relative_to(root))
                    records_all.append(r)
    active = [r for r in records_all if r.get("status") == "active"]
    version_chain = root / "system" / "version-chain.json"
    if version_chain.exists():
        inputs.append(
            {
                "role": "version-chain",
                "path": str(version_chain),
                "bytes": version_chain.stat().st_size,
                "sha256": sha256_file(version_chain),
            }
        )
    metrics["memory_records"] = {
        "total": len(records_all),
        "status": counter(records_all, "status"),
        "files": files,
    }
    metrics["active"] = {
        "count": len(active),
        "kind": counter(active, "kind"),
        "source": counter(active, "source"),
        "provenance_speaker": provenance_speaker(active),
        "sensitivity": counter(active, "sensitivity"),
        "injection_policy": counter(active, "injection_policy"),
    }
    quarantine = root / "system" / "quarantine.json"
    if quarantine.exists():
        try:
            payload = json.loads(quarantine.read_text(encoding="utf-8"))
            metrics["quarantine"] = {
                "batch": payload.get("batch"),
                "claim_ids": len(payload.get("claim_ids") or []),
                "records": len(payload.get("records") or []),
                "file_sha256": sha256_file(quarantine),
            }
            inputs.append(
                {
                    "role": "quarantine",
                    "path": str(quarantine),
                    "bytes": quarantine.stat().st_size,
                    "sha256": sha256_file(quarantine),
                }
            )
        except ValueError:
            metrics["quarantine"] = {"error": "unparsable"}

    time_fields = [
        "valid_from",
        "valid_to",
        "occurred_at",
        "decided_at",
        "observed_at",
        "superseded_by",
    ]
    metrics["time"] = {
        k: pct(sum(1 for c in active if c.get(k) not in (None, "", [])), len(active))
        for k in time_fields
    }
    metrics["time"]["precision"] = counter([c.get("provenance") or {} for c in active], "precision")

    # 证据可核验性：字符串是否在源文件内，以及 offset 是否对得上
    exact = drift = missing = no_evidence = 0
    for c in active:
        pr = c.get("provenance") or {}
        ev = (c.get("evidence") or "").strip()
        if not ev:
            no_evidence += 1
            continue
        src = root / str(pr.get("provider") or "daily") / str(pr.get("source_ref") or "")
        if not pr.get("source_ref") or not src.exists():
            missing += 1
            continue
        text = src.read_text(encoding="utf-8", errors="replace")
        idx = text.find(ev)
        if idx >= 0 and pr.get("offset") is not None and abs(idx - int(pr["offset"])) <= 40:
            exact += 1
        elif idx >= 0:
            drift += 1
        elif re.sub(r"\s+", "", ev) in re.sub(r"\s+", "", text):
            drift += 1
        else:
            missing += 1
    total = len(active)
    metrics["evidence"] = {
        "found_in_source": exact + drift,
        "found_in_source_pct": pct(exact + drift, total),
        "offset_exact": exact,
        "offset_exact_pct": pct(exact, total),
        "offset_drift": drift,
        "not_found": missing,
        "no_evidence": no_evidence,
    }

    # 去重：源消息指纹 vs claim 级重复
    hashes = collections.Counter()
    texts = collections.Counter()
    for c in active:
        h = (c.get("provenance") or {}).get("content_hash")
        if h:
            hashes[h] += 1
        texts["|".join(str(c.get(k) or "").strip() for k in ("kind", "predicate", "value"))] += 1
    hash_dup_groups = {h: v for h, v in hashes.items() if v > 1}
    text_dup_groups = {t: v for t, v in texts.items() if v > 1 and t.strip("|")}
    metrics["dedup"] = {
        "source_hash_dup_groups": len(hash_dup_groups),
        "source_hash_dup_records": sum(hash_dup_groups.values()),
        "claim_text_dup_groups": len(text_dup_groups),
        "claim_text_dup_records": sum(text_dup_groups.values()),
        "note": "content_hash 是源消息正文哈希，同一消息的多条 claim 会共用一个值",
    }

    daily = root / "daily"
    daily_files = sorted(daily.glob("*.md")) if daily.exists() else []
    if daily_files:
        inputs.append(
            {
                "role": "daily",
                "path": str(daily),
                "bytes": dir_stats(daily)["bytes"],
                "sha256": digest_pairs(
                    [(f.name, f.stat().st_size, f.stat().st_mtime) for f in daily_files]
                ),
                "method": "sha256(name:size:mtime)",
            }
        )
    labeled = untagged = zero_user = 0
    user_lines = assistant_lines = 0
    for f in daily_files:
        t = f.read_text(encoding="utf-8", errors="replace")
        ms = SPEAKER.findall(t)
        if not ms:
            untagged += 1
            continue
        labeled += 1
        u = sum(1 for x in ms if x[1] == "user")
        a = sum(1 for x in ms if x[1] == "assistant")
        user_lines += u
        assistant_lines += a
        if u == 0:
            zero_user += 1
    metrics["daily"] = {
        "files": len(daily_files),
        "bytes": dir_stats(daily)["bytes"],
        "speaker_manifests": len(list(daily.glob("*.sources.json"))) if daily.exists() else 0,
        "files_with_speaker_tags": labeled,
        "files_without_any_tag": untagged,
        "files_with_zero_user_lines": zero_user,
        "user_lines": user_lines,
        "assistant_lines": assistant_lines,
        "user_line_share_pct": pct(user_lines, user_lines + assistant_lines),
    }

    session_files = (
        [
            f
            for f in sessions.glob("*.jsonl")
            if ".trajectory" not in f.name and ".deleted" not in f.name
        ]
        if sessions.exists()
        else []
    )
    if session_files:
        inputs.append(
            {
                "role": "sessions",
                "path": str(sessions),
                "bytes": sum(f.stat().st_size for f in session_files),
                "sha256": digest_pairs(
                    [(f.name, f.stat().st_size, f.stat().st_mtime) for f in session_files]
                ),
                "method": "sha256(name:size:mtime)",
            }
        )
    roles = collections.Counter()
    real_user = cron_user = short_user = 0
    real_chars = 0
    real_days = set()
    for f in session_files:
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.startswith("{"):
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            m = e.get("message") or {}
            role = m.get("role")
            if not role:
                continue
            roles[role] += 1
            if role != "user":
                continue
            content = m.get("content")
            if isinstance(content, list):
                content = " ".join(x.get("text", "") for x in content if isinstance(x, dict))
            content = str(content or "")
            if re.match(r"^\[cron:|^\[heartbeat|^\[system|Run daily memory compile", content):
                cron_user += 1
                continue
            cjk = len(re.findall(r"[\u4e00-\u9fff]", content))
            if len(content.strip()) < 4 or cjk < 2:
                short_user += 1
                continue
            real_user += 1
            real_chars += cjk
            ts = str(e.get("timestamp") or "")
            if len(ts) >= 10:
                real_days.add(ts[:10])
    metrics["sessions"] = {
        "files": len(session_files),
        "files_excluded_trajectory_or_deleted": len(list(sessions.glob("*.jsonl")))
        - len(session_files)
        if sessions.exists()
        else 0,
        "bytes": sum(f.stat().st_size for f in session_files),
        "roles": dict(roles),
        "user_messages_total": roles.get("user", 0),
        "user_cron_injected": cron_user,
        "user_short_or_non_cjk": short_user,
        "user_real_utterances": real_user,
        "user_real_cjk_chars": real_chars,
        "user_real_days": len(real_days),
        "user_real_first_day": min(real_days) if real_days else None,
        "user_real_last_day": max(real_days) if real_days else None,
    }

    # 原始档案层（若已建立）：这是唯一可信的"你到底说过多少话"口径。
    # session jsonl 是逐轮全量快照，按记录数统计会严重虚高（实测 12.3 倍）。
    raw_manifest = root / "_raw" / "MANIFEST.jsonl"
    if raw_manifest.exists():
        inputs.append(
            {
                "role": "raw-manifest",
                "path": str(raw_manifest),
                "bytes": raw_manifest.stat().st_size,
                "sha256": sha256_file(raw_manifest),
            }
        )
        seen = {}
        pii_records = secret_records = raw_total = 0
        raw_days = set()
        with raw_manifest.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                raw_total += 1
                if e.get("pii_hits"):
                    pii_records += 1
                if e.get("secret"):
                    secret_records += 1
                key = e.get("text_sha256")
                if not key or key in seen:
                    continue
                seen[key] = e
                if e.get("date_local"):
                    raw_days.add(e["date_local"])
        unique_user = [
            e
            for e in seen.values()
            if e.get("role") == "user" and e.get("cjk_len", 0) >= 2 and e.get("char_len", 0) >= 4
        ]
        stream_bytes = sum(
            f.stat().st_size for f in (root / "_raw" / "sessions").glob("*.jsonl.gz")
        )
        metrics["raw_archive"] = {
            "records": raw_total,
            "unique_texts": len(seen),
            "repetition_factor": round(raw_total / len(seen), 2) if seen else None,
            "unique_days_covered": len(raw_days),
            "unique_user_utterances": len(unique_user),
            "unique_user_cjk_chars": sum(e.get("cjk_len", 0) for e in unique_user),
            "pii_flagged_records": pii_records,
            "secret_flagged_records": secret_records,
            "compressed_bytes": stream_bytes,
            "manifest_bytes": raw_manifest.stat().st_size,
        }

    # 隐私：只数命中，不打印内容
    scanned = pii_files = 0
    pii_by_area = collections.Counter()
    for area in ["daily", "system", "core", "long-term", "semantic"]:
        for f in (root / area).rglob("*") if (root / area).exists() else []:
            if not f.is_file() or f.suffix.lower() not in (".md", ".json"):
                continue
            scanned += 1
            try:
                t = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            # 16 进制串里的 11/18 位数字是哈希碎片，排除掉再数
            stripped = re.sub(r"[0-9a-f]{16,}", " ", t)
            if PII.search(stripped):
                pii_files += 1
                pii_by_area[area] += 1
    metrics["privacy"] = {
        "files_scanned": scanned,
        "files_with_pii_pattern": pii_files,
        "by_area": dict(pii_by_area),
        "method": "手机号/身份证正则；先剔除 16 位以上十六进制串（哈希碎片）",
        "private_profile_in_memory_root": (root / "system" / ".private-profile.md").exists(),
    }

    tx = root / "system" / "transactions"
    tx_files = [f for f in tx.glob("*.json")] if tx.exists() else []
    metrics["storage"] = {
        "version_chain_bytes": version_chain.stat().st_size if version_chain.exists() else 0,
        "review_log_bytes": (root / "system" / "review-log.md").stat().st_size
        if (root / "system" / "review-log.md").exists()
        else 0,
        "inbox_bytes": inbox_path.stat().st_size if inbox_path.exists() else 0,
        "transaction_files": len(tx_files),
        "transaction_bytes": sum(f.stat().st_size for f in tx_files),
        "transaction_avg_bytes": int(sum(f.stat().st_size for f in tx_files) / len(tx_files))
        if tx_files
        else 0,
        "dirs": {
            d: dir_stats(root / d)
            for d in [
                "daily",
                "core",
                "long-term",
                "semantic",
                "episodic",
                "notes",
                "archive",
                "system",
                "raw",
                "_raw",
            ]
        },
    }
    return metrics, inputs


def main(argv=None):
    parser = argparse.ArgumentParser(description="P0 可复现度量")
    parser.add_argument(
        "--root", default=os.environ.get("STELLAMEM_MEMORIES", r"<USER_HOME>\.dsh\memories")
    )
    parser.add_argument(
        "--sessions",
        default=os.environ.get("STELLAMEM_SESSIONS", r"<USER_HOME>\.openclaw\agents\main\sessions"),
    )
    parser.add_argument("--out")
    args = parser.parse_args(argv)

    root = Path(args.root)
    sessions = Path(args.sessions)
    if not root.exists():
        print("PROBLEM: memories root missing: " + str(root))
        return 2

    metrics, inputs = collect(root, sessions)
    result = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "memories_root": str(root),
            "sessions_dir": str(sessions),
        },
        "inputs": inputs,
        "metrics": metrics,
    }
    out = (
        Path(args.out)
        if args.out
        else (
            root
            / "benchmark"
            / "results"
            / ("p0_metrics_" + datetime.now(timezone.utc).strftime("%Y%m%d") + ".json")
        )
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )

    m = metrics
    print(
        f"inbox        {m['inbox']['count']} 条  fact={m['inbox']['kind'].get('fact', 0)}  "
        f"source=user {m['inbox']['user_sourced']}  已验证 {m['inbox']['evidence_verified_pct']}%"
    )
    print(
        f"active       {m['active']['count']} 条（全部记录 {m['memory_records']['total']} 条，"
        f"状态 {m['memory_records']['status']}）"
    )
    if "quarantine" in m:
        print(
            f"隔离         批次 {m['quarantine'].get('batch')}  "
            f"{m['quarantine'].get('claim_ids')} 条在册"
        )
    print(
        f"证据         命中 {m['evidence']['found_in_source_pct']}%  偏移精确 {m['evidence']['offset_exact_pct']}%"
    )
    print(f"时间         valid_to {m['time']['valid_to']}%  decided_at {m['time']['decided_at']}%")
    print(
        f"daily        {m['daily']['files']} 篇  user 行 {m['daily']['user_lines']} / "
        f"assistant 行 {m['daily']['assistant_lines']}  ({m['daily']['user_line_share_pct']}%)"
    )
    print(
        f"sessions     {m['sessions']['files']} 文件 / {m['sessions']['bytes'] / 1048576:.1f}MB  "
        f"user 记录 {m['sessions']['user_messages_total']} 条（含重复快照）"
    )
    if "raw_archive" in m:
        r = m["raw_archive"]
        print(
            f"raw 档案     {r['records']} 条记录 → 唯一文本 {r['unique_texts']}"
            f"（{r['repetition_factor']}× 重复）  "
            f"唯一发言 {r['unique_user_utterances']} 条 / {r['unique_user_cjk_chars']} 汉字 / "
            f"{r['unique_days_covered']} 天  {r['compressed_bytes'] / 1048576:.1f}MB"
        )
    print(
        f"隐私         命中文件 {m['privacy']['files_with_pii_pattern']} / {m['privacy']['files_scanned']}  "
        f"{m['privacy']['by_area']}"
    )
    print(
        f"事务         {m['storage']['transaction_files']} 个 / "
        f"{m['storage']['transaction_bytes'] / 1048576:.0f}MB  平均 {m['storage']['transaction_avg_bytes'] / 1048576:.1f}MB"
    )
    print("输出: " + str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
