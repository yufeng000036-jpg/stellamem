"""P0 全量复扫：按 A–E 五类分别统计，不输出任何密钥原文。

分类口径：
  A 活动代码      —— .py/.js/.ts/.ps1/.bat/.cmd 文件
  B 活动工作区    —— workspace 下的非代码文本（.md/.txt/.json/.yaml…）
  C 受限历史日志  —— OpenClaw sessions + WorkBuddy transcripts
  D 加密档案      —— vault 中含该证据的密文数量
  E 新密钥暴露    —— 除旧密钥外的其它 sk- 凭证数量
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

OUT = Path(r"./stellamem-data/docs/p0")
CODE_EXT = {".py", ".js", ".ts", ".ps1", ".bat", ".cmd", ".mjs", ".cjs"}
TEXT_EXT = {".md", ".txt", ".json", ".yaml", ".yml", ".ini", ".cfg", ".env", ".csv", ".jsonl"}
SKIP_DIR = {".git", "node_modules", "__pycache__", ".venv", "site-packages", ".obsidian"}
MAXB = 64 * 1024 * 1024

ROOTS = {
    "project": Path(r"./stellamem-data"),
    "workspace": Path(r"<USER_HOME>/.openclaw/workspace"),
    "sessions": Path(r"<USER_HOME>/.openclaw/agents"),
    "transcripts": Path(r"<USER_HOME>/.workbuddy/projects"),
}

OLD_SHA = "4b82da1d6815dc343129d9ab623bff0a613636d193a3ad231736ace0534e3f01"


def old_key() -> str:
    """取回旧密钥用于比对。优先 MEMORY.md；已清理则从加密备份临时解密取回，
    用后立即删除临时文件，密钥只在内存中。"""
    p = Path(r"<USER_HOME>/.openclaw/workspace/MEMORY.md")
    t = p.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"sk-[A-Za-z0-9]{24,}", t)
    if m:
        return m.group(0)

    import subprocess
    import tempfile

    enc = Path(r"E:/secure/stellamem-vault/p0-workspace-20261101/MEMORY.md.enc")
    keyfile = r"<USER_HOME>/.stellamem-keys/backups-20261101.key"
    if not enc.exists():
        return ""
    tmp = Path(tempfile.mkdtemp(prefix="p0key-"))
    out = tmp / "m.md"
    try:
        subprocess.run(["node", r"./stellamem-data/tools/vault.mjs", "unseal",
                        "--key", keyfile, "--in", str(enc), "--out", str(out)],
                       capture_output=True, text=True, timeout=60)
        mm = re.search(r"sk-[A-Za-z0-9]{24,}", out.read_text(encoding="utf-8", errors="ignore"))
        return mm.group(0) if mm else ""
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def scan(root: Path, needle: str | None, want_code: bool):
    """返回 (命中文件数, 命中次数, 发现的其他密钥哈希集合)"""
    files = hits = 0
    others: set[str] = set()
    if not root.exists():
        return 0, 0, others
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR]
        for fn in filenames:
            p = Path(dirpath) / fn
            ext = p.suffix.lower()
            is_code = ext in CODE_EXT
            if want_code is not None and is_code != want_code:
                continue
            if ext not in CODE_EXT and ext not in TEXT_EXT:
                continue
            try:
                if p.stat().st_size > MAXB:
                    continue
                data = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if needle and needle in data:
                files += 1
                hits += data.count(needle)
            for m in re.finditer(r"sk-[A-Za-z0-9]{24,}", data):
                h = hashlib.sha256(m.group(0).encode()).hexdigest()
                if h != OLD_SHA:
                    others.add(h)
    return files, hits, others


def main() -> int:
    key = old_key()
    res: dict = {"old_key_recovered": bool(key),
        "old_key_still_in_memory_md": bool(re.search(r"sk-[A-Za-z0-9]{24,}", Path(r"<USER_HOME>/.openclaw/workspace/MEMORY.md").read_text(encoding="utf-8", errors="ignore")))}

    # A 活动代码（项目 + 工作区）
    a_f = a_h = 0
    others_all: set[str] = set()
    for name in ("project", "workspace"):
        if name == "workspace":
            continue
        f, h, o = scan(ROOTS[name], key or None, True)
        a_f += f; a_h += h; others_all |= o
    ws_code_f = ws_code_h = 0
    if ROOTS["workspace"].exists():
        f, h, o = scan(ROOTS["workspace"], key or None, True)
        ws_code_f += f; ws_code_h += h; others_all |= o
    a_f += ws_code_f; a_h += ws_code_h

    # B 工作区文本（非代码）
    b_f = b_h = 0
    _, _, o = scan(ROOTS["workspace"], None, False)
    others_all |= o
    if key:
        for p in ROOTS["workspace"].rglob("*"):
            if p.is_file() and p.suffix.lower() in TEXT_EXT and p.suffix.lower() not in CODE_EXT:
                try:
                    if p.stat().st_size > MAXB:
                        continue
                    t = p.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if key in t:
                    b_f += 1; b_h += t.count(key)

    # C 受限历史
    c_f = c_h = 0
    for name in ("sessions", "transcripts"):
        f, h, o = scan(ROOTS[name], key or None, None)
        c_f += f; c_h += h; others_all |= o

    # D 加密档案
    vault = Path(r"E:/secure/stellamem-vault")
    d_files = sum(1 for _ in vault.rglob("*.enc")) if vault.exists() else 0
    d_p0 = sum(1 for _ in (vault / "p0-workspace-20261101").glob("*.enc")) if (vault / "p0-workspace-20261101").exists() else 0

    res.update({
        "A_active_code": {"files": a_f, "occurrences": a_h},
        "B_active_workspace_text": {"files": b_f, "occurrences": b_h},
        "C_restricted_history": {"files": c_f, "occurrences": c_h},
        "D_encrypted_archives": {"total_enc": d_files, "p0_batch_enc": d_p0},
        "E_new_secret_hashes": {"count": len(others_all),
                                "hashes_prefix": sorted(x[:12] for x in others_all)},
    })
    (OUT / "rescan.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
