"""离线凭证归属分析：只输出编号/位置/次数/时间/服务线索，不输出任何密钥材料。

输出：D:/Stellamem/docs/p0/credential-map.json（供报告引用）
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

OUT = Path(r"D:/Stellamem/docs/p0")
OLD_SHA = "4b82da1d6815dc343129d9ab623bff0a613636d193a3ad231736ace0534e3f01"
KEY_RE = re.compile(r"sk-[A-Za-z0-9]{24,}")
# 服务线索词（出现即视为上下文证据）
SERVICE_HINTS = {
    "deepseek": ["deepseek", "DeepSeek"],
    "openai": ["openai", "OpenAI", "api.openai.com"],
    "minimax": ["minimax", "MiniMax", "minimaxi"],
    "anthropic": ["anthropic", "claude", "Claude"],
    "moonshot": ["moonshot", "kimi", "Kimi"],
    "zhipu": ["zhipu", "glm", "GLM", "bigmodel"],
    "siliconflow": ["siliconflow", "SiliconFlow"],
    "dashscope": ["dashscope", "通义", "qwen", "Qwen"],
}

ROOTS = [
    Path(r"<USER_HOME>/.openclaw"),
    Path(r"<USER_HOME>/.workbuddy/projects"),
    Path(r"D:/Stellamem"),
    Path(r"E:/stellamem-p2"),
]
SKIP_DIR = {".git", "node_modules", "__pycache__", ".venv", "site-packages"}
SKIP_EXT = {".enc", ".gz", ".zip", ".png", ".jpg", ".jpeg", ".webp", ".gguf",
            ".safetensors", ".bin", ".db", ".mp4", ".mp3", ".wav"}
MAXB = 64 * 1024 * 1024


def classify_file(p: Path) -> str:
    s = str(p).replace("\\", "/")
    if "auth-profiles" in s:
        return "active-credential-store"
    if "/extensions/" in s or "/hooks/" in s:
        return "plugin-or-hook"
    if "/sessions/" in s or s.endswith(".jsonl"):
        return "session-log"
    if "/workbuddy/projects/" in s:
        return "transcript"
    if s.startswith("D:/Stellamem"):
        return "project"
    if "/workspace/" in s:
        return "workspace"
    return "other"


def main() -> int:
    found: dict[str, dict] = defaultdict(lambda: {
        "occurrences": 0, "files": {}, "services": defaultdict(int), "samples": []
    })

    for root in ROOTS:
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIR]
            for fn in filenames:
                p = Path(dirpath) / fn
                if p.suffix.lower() in SKIP_EXT:
                    continue
                try:
                    if p.stat().st_size > MAXB:
                        continue
                    text = p.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if "sk-" not in text:
                    continue
                try:
                    mtime = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d")
                except OSError:
                    mtime = "UNKNOWN"
                for m in KEY_RE.finditer(text):
                    h = hashlib.sha256(m.group(0).encode()).hexdigest()
                    if h == OLD_SHA:
                        continue
                    rec = found[h]
                    rec["occurrences"] += 1
                    key = str(p)
                    if key not in rec["files"]:
                        rec["files"][key] = {"kind": classify_file(p), "count": 0, "date": mtime}
                    rec["files"][key]["count"] += 1
                    # 上下文窗口判定服务
                    lo = max(0, m.start() - 300)
                    hi = min(len(text), m.end() + 300)
                    ctx = text[lo:hi].lower()
                    for svc, hints in SERVICE_HINTS.items():
                        if any(x.lower() in ctx for x in hints):
                            rec["services"][svc] += 1
                    if len(rec["samples"]) < 2:
                        rec["samples"].append({"file": key, "date": mtime})

    # 排序：按出现次数降序，编号 CRED-01..
    ordered = sorted(found.items(), key=lambda kv: -kv[1]["occurrences"])
    rows = []
    for i, (h, rec) in enumerate(ordered, 1):
        kinds = {v["kind"] for v in rec["files"].values()}
        has_active = "active-credential-store" in kinds
        dates = sorted(v["date"] for v in rec["files"].values() if v["date"] != "UNKNOWN")
        svc = max(rec["services"].items(), key=lambda x: x[1])[0] if rec["services"] else "UNKNOWN"
        rows.append({
            "id": f"CRED-{i:02d}",
            "hash_prefix8": h[:8],            # 8 位哈希前缀：不可逆，仅用于内部核对
            "service_guess": svc,
            "service_evidence_count": dict(rec["services"]),
            "occurrences": rec["occurrences"],
            "file_count": len(rec["files"]),
            "files": [{"path": k, "kind": v["kind"], "count": v["count"], "date": v["date"]}
                      for k, v in sorted(rec["files"].items(), key=lambda kv: -kv[1]["count"])],
            "first_date": dates[0] if dates else "UNKNOWN",
            "last_date": dates[-1] if dates else "UNKNOWN",
            "in_active_store": has_active,
            "kinds": sorted(kinds),
        })

    (OUT / "credential-map.json").write_text(
        json.dumps({"total": len(rows), "rows": rows}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print(json.dumps({"total": len(rows),
                      "summary": [{"id": r["id"], "svc": r["service_guess"],
                                   "occ": r["occurrences"], "files": r["file_count"],
                                   "active": r["in_active_store"]} for r in rows]},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
