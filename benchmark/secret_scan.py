"""P0 密钥暴露清点：扫描但不输出密钥原文。

输出：SECRET-EXPOSURE-INVENTORY.csv + incident.json
所有位置只报路径/次数/哈希前缀，key 本身只存在于内存。
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

OUT = Path(r"D:/Stellamem/docs/p0")
OUT.mkdir(parents=True, exist_ok=True)

PAT = re.compile(r"sk-[A-Za-z0-9]{24,}")
SKIP_EXT = {".enc", ".gz", ".zip", ".7z", ".png", ".jpg", ".jpeg", ".webp",
            ".mp4", ".mp3", ".wav", ".gguf", ".safetensors", ".bin", ".db"}
MAX_BYTES = 64 * 1024 * 1024


def find_keys() -> list[str]:
    keys = set()
    ws = Path(r"<USER_HOME>/.openclaw/workspace")
    for p in ws.glob("*.md"):
        try:
            for m in PAT.finditer(p.read_text(encoding="utf-8", errors="ignore")):
                keys.add(m.group(0))
        except OSError:
            pass
    return sorted(keys)


def scan_file(p: Path, needle: str) -> int:
    try:
        if p.stat().st_size > MAX_BYTES:
            return 0
        data = p.read_bytes()
    except OSError:
        return 0
    return data.count(needle.encode())


def walk(root: Path, needle: str, label: str, rows: list, limit_files: int | None = None):
    n = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in
                       {".git", "node_modules", "__pycache__", ".venv", "site-packages"}]
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.suffix.lower() in SKIP_EXT:
                continue
            c = scan_file(p, needle)
            if c:
                rows.append({"area": label, "path": str(p), "occurrences": c})
            n += 1
            if limit_files and n >= limit_files:
                return


def git_hits(root: Path, needle: str, rows: list):
    try:
        r = subprocess.run(["git", "-C", str(root), "log", "--all", "-S", needle,
                            "--oneline"], capture_output=True, text=True, timeout=120)
        hits = [l for l in r.stdout.splitlines() if l.strip()]
        if hits:
            rows.append({"area": "git-history", "path": f"{root} ({len(hits)} commits)",
                         "occurrences": len(hits)})
        idx = Path(root) / ".git" / "index"
        if idx.exists():
            c = scan_file(idx, needle)
            if c:
                rows.append({"area": "git-index", "path": str(idx), "occurrences": c})
    except Exception:
        pass


def main() -> int:
    keys = find_keys()
    if not keys:
        print(json.dumps({"keys_found": 0}))
        return 0
    # 只处理第一个（多把则全部列出但都不打印原文）
    report = []
    for k in keys:
        h = hashlib.sha256(k.encode()).hexdigest()
        report.append({"sha256": h, "len": len(k), "masked": k[:3] + "***" + k[-2:]})
    needle = keys[0]

    rows: list[dict] = []
    targets = [
        (Path(r"<USER_HOME>/.openclaw/workspace"), "workspace"),
        (Path(r"<USER_HOME>/.openclaw/agents"), "sessions"),
        (Path(r"<USER_HOME>/.openclaw/hooks"), "hooks"),
        (Path(r"<USER_HOME>/.openclaw/extensions"), "extensions"),
        (Path(r"<USER_HOME>/.dsh/memories"), "memories"),
        (Path(r"D:/Stellamem"), "project"),
        (Path(r"E:/mem-backup-2026-10-31"), "backup"),
        (Path(r"E:/stellamem-p2"), "p2-sandbox"),
    ]
    for root, label in targets:
        if root.exists():
            walk(root, needle, label, rows)
    git_hits(Path(r"D:/Stellamem"), needle, rows)

    # WorkBuddy 项目转录（会话记录）
    wb = Path(r"<USER_HOME>/.workbuddy/projects")
    if wb.exists():
        walk(wb, needle, "workbuddy-transcripts", rows)

    rows.sort(key=lambda r: (-r["occurrences"], r["area"]))
    csvp = OUT / "SECRET-EXPOSURE-INVENTORY.csv"
    with csvp.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["area", "path", "occurrences"])
        w.writeheader()
        w.writerows(rows)

    total = sum(r["occurrences"] for r in rows)
    res = {
        "keys_found": len(keys),
        "keys": report,
        "files_with_hit": len(rows),
        "total_occurrences": total,
        "by_area": {},
        "inventory_csv": str(csvp),
    }
    for r in rows:
        res["by_area"][r["area"]] = res["by_area"].get(r["area"], 0) + r["occurrences"]
    (OUT / "incident.json").write_text(json.dumps(res, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    print(json.dumps({k: res[k] for k in
                      ("keys_found", "keys", "files_with_hit", "total_occurrences", "by_area")},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
