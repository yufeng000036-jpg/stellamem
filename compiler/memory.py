#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
memory.py —— 记忆系统 CLI（可解释、可回滚）

用法：
  python memory.py doctor                    # 体检（只读）
  python memory.py explain <id>              # 解释一条记忆（只读）
  python memory.py history <id>              # 查看版本/冲突历史（只读）
  python memory.py diff                      # 对比当前 vs 最近备份（只读）
  python memory.py rollback <备份名> [--yes] # 回滚（默认干跑，--yes 才真写）

安全：所有路径限于 memories 目录内；rollback 必须带 --yes 且先备份。
"""
import os, sys, json, re, glob
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config_loader import load_config

ROOT = load_config()["memories_root"]
SYSTEM = os.path.join(ROOT, "system")
CORE = ["core/identity.md", "core/user-core.md", "core/preferences.md"]
LONG = ["long-term/projects.md", "long-term/decisions.md", "long-term/lessons.md"]
SEMANTIC = ["semantic/profile.md", "semantic/data-profile.md",
            "semantic/favorites.md", "semantic/tech-index.md"]
ALL_MEMORY = CORE + LONG + SEMANTIC

def read(p):
    if not os.path.exists(p):
        return ""
    return open(p, encoding="utf-8", errors="replace").read()

def mtime(p):
    return datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y-%m-%d %H:%M") if os.path.exists(p) else "不存在"

def count_pending():
    txt = read(os.path.join(SYSTEM, "memory-inbox.md"))
    # 真实 pending 条目 = "## MI-" 开头的标题（排除模板示例 MI-20260920-001 那种带说明的）
    return len(re.findall(r"^## MI-", txt, re.M))

def count_unresolved_conflicts():
    txt = read(os.path.join(SYSTEM, "conflict-log.md"))
    return len(re.findall(r"conflict（", txt))

def count_review_log():
    txt = read(os.path.join(SYSTEM, "review-log.md"))
    return len([l for l in txt.splitlines() if " | " in l and not l.startswith("#")])


def count_legacy_false_write():
    """旧文件仅作历史保留；其中的自动挂起项不能视为已确认 False Write。"""
    txt = read(os.path.join(SYSTEM, "false-write-log.md"))
    return len([l for l in txt.splitlines() if " | " in l and not l.startswith("#")])

def count_version_chain():
    try:
        return len(json.loads(read(os.path.join(SYSTEM, "version-chain.json"))))
    except Exception:
        return -1

# ===== doctor =====
def cmd_doctor():
    print("# 记忆系统体检")
    print("=" * 40)
    checks = [
        ("INDEX.md", "INDEX.md"),
        ("core/identity.md", CORE[0]),
        ("core/user-core.md", CORE[1]),
        ("core/preferences.md", CORE[2]),
        ("long-term/projects.md", LONG[0]),
        ("long-term/decisions.md", LONG[1]),
        ("long-term/lessons.md", LONG[2]),
        ("system/schema.md", "system/schema.md"),
        ("system/conflict-log.md", "system/conflict-log.md"),
        ("system/version-chain.json", "system/version-chain.json"),
        ("system/memory-inbox.md", "system/memory-inbox.md"),
        ("system/review-log.md", "system/review-log.md"),
        ("system/extraction-cursor.md", "system/extraction-cursor.md"),
    ]
    for label, rel in checks:
        p = os.path.join(ROOT, rel)
        exists = os.path.exists(p)
        # 用 ASCII 标记，不用 ✔/✘ —— Windows GBK 控制台会 UnicodeEncodeError 崩溃
        print(("[OK]  " if exists else "[MISS]") + f" {label}: {'存在' if exists else '缺失'}")
    print("-" * 40)
    print(f"inbox pending 条数: {count_pending()}")
    print(f"conflict-log 未裁决条数: {count_unresolved_conflicts()}")
    print(f"review-log 自动挂起/拦截条数: {count_review_log()}")
    legacy = count_legacy_false_write()
    if legacy:
        print(f"legacy false-write-log 条数: {legacy}（历史名称，不等于已确认错误写入）")
    vc = count_version_chain()
    print(f"version-chain 条数: {vc if vc >= 0 else '解析失败'}")
    daily_dir_p = os.path.join(ROOT, "daily")
    daily = len([f for f in os.listdir(daily_dir_p) if f.endswith(".md")]) if os.path.isdir(daily_dir_p) else 0
    notes_dir_p = os.path.join(ROOT, "notes")
    notes = len(os.listdir(notes_dir_p)) if os.path.isdir(notes_dir_p) else 0
    print(f"daily 文件数: {daily}，notes 文件数: {notes}")
    # 异常检测：ID 重复
    inbox_txt = read(os.path.join(SYSTEM, "memory-inbox.md"))
    ids = re.findall(r"^## (MI-\S+)", inbox_txt, re.M)
    dup = [i for i in set(ids) if ids.count(i) > 1]
    print(f"异常·inbox ID 重复: {dup if dup else '无'}")
    # 异常：version-chain 字段缺失
    try:
        vcd = json.loads(read(os.path.join(SYSTEM, "version-chain.json")))
        missing = [e.get("id", "?") for e in vcd if not ("id" in e and "current" in e and "status" in e)]
        print(f"异常·version-chain 字段缺失: {missing if missing else '无'}")
    except Exception:
        pass
    print("=" * 40)
    print("汇总：体检完成")

# ===== explain =====
def cmd_explain(cid):
    # 1) inbox 里找 MI-xxx
    inbox_txt = read(os.path.join(SYSTEM, "memory-inbox.md"))
    m = re.search(r"^## " + re.escape(cid) + r"\s*$([\s\S]*?)(?=^## |\Z)", inbox_txt, re.M)
    if m:
        block = m.group(1).strip()
        print(f"# 记忆解释：{cid}")
        print(f"类型: inbox 待确认条目")
        src = re.search(r"来源[**：: ]*`?([^`\n]+)`?", block)
        print(f"来源: " + (src.group(1).strip() if src else "?"))
        claim = re.search(r"候选 Claim[**：: ]*(.+)", block)
        print(f"候选 Claim: " + (claim.group(1).strip() if claim else "?"))
        conf = re.search(r"置信度[**：: ]*(.+)", block)
        print(f"置信度: " + (conf.group(1).strip() if conf else "?"))
        st = re.search(r"状态[**：: ]*(.+)", block)
        print(f"状态: " + (st.group(1).strip() if st else "?"))
        return
    # 2) 正式文件里查四段式 claim-id；legacy 文本仍可按原字符串查询。
    for rel in ALL_MEMORY:
        txt = read(os.path.join(ROOT, rel))
        if cid in txt:
            lines = [l for l in txt.splitlines() if cid in l]
            print(f"# 记忆解释：{cid}")
            print(f"类型: 正式记忆（位于 {rel}）")
            for l in lines[:3]:
                print(f"内容: {l.strip()}")
            print(f"文件修改时间: {mtime(os.path.join(ROOT, rel))}")
            return
    print(f"未找到记忆：{cid}")

# ===== history =====
def cmd_history(cid):
    print(f"# 历史：{cid}")
    found = False
    # version-chain
    try:
        vcd = json.loads(read(os.path.join(SYSTEM, "version-chain.json")))
        for e in vcd:
            if cid in e.get("id", ""):
                found = True
                print(f"[版本链] {e.get('id')} | {e.get('supersedes','?')} → {e.get('current','?')} | {e.get('date','?')} | {e.get('status','?')}")
    except Exception:
        pass
    # conflict-log
    ct = read(os.path.join(SYSTEM, "conflict-log.md"))
    for blk in re.split(r"\n## ", ct):
        if cid in blk:
            found = True
            print(f"[冲突日志] {blk.strip()[:200]}")
    if not found:
        print("无版本记录")

# ===== diff =====
def cmd_diff():
    # 找所有 .bak 文件，对比对应原文件
    baks = sorted(glob.glob(os.path.join(ROOT, "**", "*.bak*"), recursive=True), key=os.path.getmtime, reverse=True)
    baks = [b for b in baks if ".bak" in os.path.basename(b)]
    if not baks:
        print("无可比对备份")
        return
    latest = baks[0]
    orig = re.sub(r"\.(pre-phase\d+\.)?bak(-\d+)?$", "", latest)
    print(f"# diff：对比 {os.path.basename(orig)} vs {os.path.basename(latest)}")
    if not os.path.exists(orig):
        print("对应的原文件不存在")
        return
    cur = read(orig).splitlines()
    old = read(latest).splitlines()
    cur_set = set(cur); old_set = set(old)
    added = [l for l in cur if l not in old_set]
    removed = [l for l in old if l not in cur_set]
    print(f"新增 {len(added)} 行：")
    for l in added[:10]: print("  + " + l[:80])
    print(f"删除 {len(removed)} 行：")
    for l in removed[:10]: print("  - " + l[:80])

# ===== rollback =====
def cmd_rollback(name, yes):
    # 安全：备份名必须存在于 memories 目录内（防路径穿越）
    if os.path.isabs(name) or ".." in name or "/" in name.replace("\\", ""):
        print("[错误] 备份名必须是 memories 目录内的文件名，拒绝路径穿越")
        sys.exit(1)
    # 在 memories 目录内找这个备份文件
    matches = []
    for dp, dn, fn in os.walk(ROOT):
        for f in fn:
            if f == name:
                matches.append(os.path.join(dp, f))
    if not matches:
        print(f"[错误] 备份不存在：{name}")
        sys.exit(1)
    bak = matches[0]
    # 备份对应的原文件
    orig = re.sub(r"\.bak(-\d+)?$", "", bak)
    if not os.path.exists(orig):
        print(f"[错误] 对应原文件不存在：{orig}")
        sys.exit(1)
    cur_size = os.path.getsize(orig)
    bak_size = os.path.getsize(bak)
    if not yes:
        print("# rollback 干跑预览")
        print(f"备份文件: {bak}")
        print(f"原文件: {orig}")
        print(f"当前大小: {cur_size} B → 恢复后: {bak_size} B")
        print("（未执行，加 --yes 才真恢复）")
        return
    # 真写：先备份当前
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    pre = orig + ".pre-rollback-" + stamp + ".bak"
    import shutil
    shutil.copyfile(orig, pre)
    shutil.copyfile(bak, orig)
    print(f"已恢复：{orig}")
    print(f"恢复前备份：{pre}")

def main():
    if len(sys.argv) < 2:
        print("用法：python memory.py <doctor|explain|history|diff|rollback> [参数]")
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "doctor":
        cmd_doctor()
    elif cmd == "explain":
        if len(sys.argv) < 3:
            print("用法：python memory.py explain <id>"); sys.exit(1)
        cmd_explain(sys.argv[2])
    elif cmd == "history":
        if len(sys.argv) < 3:
            print("用法：python memory.py history <id>"); sys.exit(1)
        cmd_history(sys.argv[2])
    elif cmd == "diff":
        cmd_diff()
    elif cmd == "rollback":
        if len(sys.argv) < 3:
            print("用法：python memory.py rollback <备份名> [--yes]"); sys.exit(1)
        cmd_rollback(sys.argv[2], "--yes" in sys.argv)
    else:
        print(f"未知命令：{cmd}")
        sys.exit(1)

if __name__ == "__main__":
    main()
