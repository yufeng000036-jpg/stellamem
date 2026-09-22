#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
setup.py —— Stellamem 一键安装器（AI 可执行）

用法：
  python setup.py check                # 环境自检（只读，不改任何文件）
  python setup.py install              # 一键安装（幂等，重复跑安全）
  python setup.py install --dry-run    # 干跑，只打印将做什么
  python setup.py install --yes        # 跳过交互确认

设计原则：
  - 绝不覆盖用户已有记忆文件（存在即跳过）
  - 默认不改 openclaw.json，只生成 snippet
  - 仅当用户显式传入 --merge-openclaw 时，先备份再执行“只追加 keys”的合并
  - 更新已有 hook / adapter 前先备份旧目录（不悄悄删除）
  - 幂等：重复执行不会产生重复内容
  - 所有路径探测失败时给出明确修复建议

作者：宇枫
"""

import os
import sys
import json
import shutil
import platform
import argparse
from datetime import datetime

# ============ 常量 ============
HERE = os.path.dirname(os.path.abspath(__file__))
MEMORY_SUBDIRS = [
    "core", "long-term", "semantic", "episodic", "system", "working", "daily",
]

# ============ 输出工具 ============
def ok(msg):
    print(f"[OK]   {msg}")

def info(msg):
    print(f"[INFO] {msg}")

def warn(msg):
    print(f"[WARN] {msg}")

def err(msg):
    print(f"[ERR]  {msg}")

def section(title):
    print()
    print("=" * 60)
    print(f"  {title}")
    print("=" * 60)


# ============ 环境探测 ============
def detect_openclaw_root():
    """探测 OpenClaw 状态目录。返回 (path, 来源说明) 或 (None, None)"""
    candidates = []
    home = os.path.expanduser("~")
    # 1. 环境变量
    env = os.environ.get("OPENCLAW_HOME") or os.environ.get("OPENCLAW_STATE_DIR")
    if env and os.path.isdir(env):
        candidates.append((env, "环境变量 OPENCLAW_HOME"))
    # 2. 默认位置
    for rel in [".openclaw", ".config/openclaw", "AppData/Roaming/openclaw",
                "AppData/Local/openclaw"]:
        p = os.path.join(home, *rel.split("/"))
        if os.path.isdir(p):
            candidates.append((p, f"默认位置 ~/{rel}"))
    return candidates[0] if candidates else (None, None)


def detect_sessions_dir(oc_root):
    """探测 OpenClaw 的 sessions 目录（优先默认 agent main）"""
    if not oc_root:
        return None
    agents_dir = os.path.join(oc_root, "agents")
    if os.path.isdir(agents_dir):
        agents = os.listdir(agents_dir)
        # 1) 优先 main（OpenClaw 默认 agent id）
        if "main" in agents:
            sess = os.path.join(agents_dir, "main", "sessions")
            if os.path.isdir(sess):
                return sess
        # 2) 读 openclaw.json 的 agents.list 里 default:true 的 agent
        cfg_path = os.path.join(oc_root, "openclaw.json")
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, encoding="utf-8") as fh:
                    cfg = json.load(fh)
                for a in cfg.get("agents", {}).get("list", []):
                    if a.get("default"):
                        aid = a.get("id")
                        sess = os.path.join(agents_dir, aid, "sessions")
                        if aid and os.path.isdir(sess):
                            return sess
            except Exception:
                pass
        # 3) 兜底：第一个有 sessions 的 agent
        for agent in agents:
            sess = os.path.join(agents_dir, agent, "sessions")
            if os.path.isdir(sess):
                return sess
    # 备选：<oc_root>/sessions
    alt = os.path.join(oc_root, "sessions")
    if os.path.isdir(alt):
        return alt
    return None


def detect_node():
    """探测 node 版本"""
    try:
        import subprocess
        out = subprocess.run(["node", "--version"], capture_output=True,
                             text=True, timeout=10)
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return None


def detect_llama_bin():
    """探测 llama-server / llama.cpp 可执行文件"""
    import shutil as sh
    for name in ["llama-server", "llama-server.exe", "llama.exe", "llama"]:
        p = sh.which(name)
        if p:
            return p
    return None


def detect_ollama():
    """探测 ollama 是否在跑"""
    try:
        import urllib.request
        req = urllib.request.Request("http://127.0.0.1:11434/api/tags")
        urllib.request.urlopen(req, timeout=3)
        return True
    except Exception:
        return False


def detect_tz_offset():
    """探测本机 UTC 偏移（小时）。优先环境变量 STELLAMEM_TZ_OFFSET，
    其次用本机时区；都失败则回退 8（Asia/Shanghai）。"""
    env = os.environ.get("STELLAMEM_TZ_OFFSET")
    if env:
        try:
            return float(env)
        except ValueError:
            pass
    try:
        from datetime import datetime
        off = datetime.now().astimezone().utcoffset()
        if off is not None:
            hours = off.total_seconds() / 3600.0
            # 整数则去小数点（8.0 → 8）
            return int(hours) if hours == int(hours) else hours
    except Exception:
        pass
    return 8


def detect_model_service(url):
    """探测某个 OpenAI 兼容端点是否可用"""
    try:
        import urllib.request
        req = urllib.request.Request(url.rstrip("/") + "/v1/models", method="GET")
        urllib.request.urlopen(req, timeout=3)
        return True
    except Exception:
        return False


# ============ openclaw.json 自动合并 ============
def deep_merge(base, overlay, path="", conflicts=None):
    """深度合并 overlay 到 base（就地修改 base，返回 base）——「只追加，不覆盖」。

    规则（安全优先）：
      - 两个都是 dict → 递归合并
      - 两个都是 list → 去重追加（保留 base 原有元素，overlay 新元素追加到末尾）
      - base 里【缺】该 key → 补上 overlay 的值
      - base 里【已有】该 key 且值不同 → **保留 base 原值不动**，
        把 (路径, 原值, 拟加值) 记入 conflicts 列表，交给用户手动决定
      - base 里已有且值相同 → 什么都不做

    conflicts 为 list 时，冲突项以 dict 形式追加进去。
    调用方据此生成 diff 报告；默认（conflicts=None）静默收集，不写 base。
    """
    if conflicts is None:
        conflicts = []
    for key, val in overlay.items():
        cur_path = f"{path}.{key}" if path else key
        if key not in base:
            # 缺失 → 补上（dict/list 也直接带上，副本语义由 json 序列化保证）
            if isinstance(val, dict):
                base[key] = {}
                deep_merge(base[key], val, cur_path, conflicts)
            elif isinstance(val, list):
                base[key] = []
                deep_merge(base[key], val, cur_path, conflicts)
            else:
                base[key] = val
        elif isinstance(base[key], dict) and isinstance(val, dict):
            deep_merge(base[key], val, cur_path, conflicts)
        elif isinstance(base[key], list) and isinstance(val, list):
            for item in val:
                if item not in base[key]:
                    base[key].append(item)
        else:
            # 键已存在且类型/值不同 → 绝不覆盖，只记冲突
            if base[key] != val:
                conflicts.append({
                    "path": cur_path,
                    "existing": base[key],
                    "suggested": val,
                    "action": "kept-existing (未覆盖，请手动决定)",
                })
    return base


def merge_openclaw_config(oc_root, snippet):
    """备份 openclaw.json 后，把 snippet 「只追加不覆盖」地合并进去。

    已有字段一律保留；缺失字段补齐；冲突字段不写、记入 diff 报告。

    返回 (备份路径, 合并后的 config, conflicts) 或抛异常。
    """
    cfg_path = os.path.join(oc_root, "openclaw.json")
    if not os.path.exists(cfg_path):
        raise FileNotFoundError(f"找不到 {cfg_path}")

    # 1. 备份
    bak_path = cfg_path + ".bak-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copyfile(cfg_path, bak_path)

    # 2. 读取
    with open(cfg_path, encoding="utf-8") as fh:
        cfg = json.load(fh)

    # 3. 只追加不覆盖地合并（冲突只记录，不改动 base）
    conflicts = []
    deep_merge(cfg, snippet, "", conflicts)

    # 4. 写回
    with open(cfg_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)

    return bak_path, cfg, conflicts


# ============ check ============
def cmd_check():
    section("Stellamem 环境自检")
    py = sys.version.split()[0]
    ok(f"Python: {py}")

    node = detect_node()
    if node:
        ok(f"Node: {node}")
    else:
        warn("Node: 未找到（写入侧/注入侧需要 Node 18+）")

    oc_root, oc_src = detect_openclaw_root()
    if oc_root:
        ok(f"OpenClaw 根目录: {oc_root}  （来源：{oc_src}）")
    else:
        warn("OpenClaw 根目录: 未找到")

    sess = detect_sessions_dir(oc_root) if oc_root else None
    if sess:
        ok(f"Sessions 目录: {sess}")
    else:
        warn("Sessions 目录: 未找到（写入侧无法工作）")

    llama = detect_llama_bin()
    if llama:
        ok(f"llama.cpp: {llama}")
    else:
        warn("llama.cpp: 未找到")

    if detect_ollama():
        ok("Ollama: 运行中（127.0.0.1:11434）")
    else:
        info("Ollama: 未检测到（可选）")

    for url in ["http://127.0.0.1:8081", "http://127.0.0.1:1234", "http://127.0.0.1:11434"]:
        if detect_model_service(url):
            ok(f"模型服务可用: {url}")
            break
    else:
        warn("模型服务: 三处默认端口都没探测到（编译侧需要它）")

    exists_cfg = os.path.exists(os.path.join(HERE, "config.json"))
    if exists_cfg:
        ok("config.json: 已存在")
    else:
        warn("config.json: 不存在（install 会生成）")

    section("修复建议")
    fixes = []
    if not node:
        fixes.append("安装 Node.js 18+（写入侧 hook 和注入侧 adapter 需要）")
    if not oc_root:
        fixes.append("设置环境变量 OPENCLAW_HOME 指向你的 OpenClaw 状态目录")
    if not sess:
        fixes.append("确认 OpenClaw 至少启动过一次（才会生成 sessions 目录）")
    if not llama and not detect_ollama() and not detect_model_service("http://127.0.0.1:8081"):
        fixes.append("安装模型服务：llama.cpp / ollama / LM Studio 任选其一（见 INSTALL.md 第 4 节）")
    if not fixes:
        fixes.append("环境完备，可以执行 python setup.py install")
    for i, f in enumerate(fixes, 1):
        print(f"  {i}. {f}")
    print()


# ============ install ============
def read_text(p):
    with open(p, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def write_text(p, text):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def replace_placeholder(text, old, new):
    """只替换一次占位符；已替换过则原样返回"""
    if old not in text:
        return text, False
    return text.replace(old, new), True


def cmd_install(dry_run=False, yes=False, ai_name=None, mem_root=None, merge_openclaw=False, tz_offset=None, model_url=None, oc_root_override=None):
    section("Stellamem 一键安装" + ("（干跑）" if dry_run else ""))

    # ---- 1. 探测（--oc-root 优先级最高，专为冷启动/测试；不传则探测真实环境）----
    if oc_root_override:
        oc_root = os.path.abspath(oc_root_override)
        oc_src = "命令行 --oc-root（隔离模式，不会碰真实 OpenClaw）"
        if not os.path.isdir(oc_root):
            info(f"OpenClaw 根目录（将创建）: {oc_root}")
        else:
            info(f"OpenClaw 根目录: {oc_root}")
        info(f"          来源: {oc_src}")
    else:
        oc_root, oc_src = detect_openclaw_root()

    sess = detect_sessions_dir(oc_root) if oc_root else None

    if not oc_root:
        err("未探测到 OpenClaw 根目录，无法继续。")
        err("请设置 OPENCLAW_HOME 环境变量，或用 --oc-root <路径> 指定（测试推荐）。")
        if not yes:
            return 1
        oc_root = ""
    elif not oc_root_override:
        info(f"OpenClaw 根目录: {oc_root}")

    # ---- 2. 记忆根目录（命令行参数 > 交互询问 > 默认）----
    default_mem = os.path.join(os.path.expanduser("~"), ".stellamem")
    mem_root = mem_root or default_mem
    if not yes:
        try:
            ans = input(f"记忆根目录 [{mem_root}]: ").strip()
            if ans:
                mem_root = ans
        except EOFError:
            pass
    info(f"记忆根目录: {mem_root}")

    # ---- 3. AI 名字（命令行参数 > 交互询问 > 默认）----
    ai_name = ai_name or "我的AI"
    if not yes:
        try:
            ans = input(f"你的 AI 名字 [{ai_name}]: ").strip()
            if ans:
                ai_name = ans
        except EOFError:
            pass
    info(f"AI 名字: {ai_name}")

    # ---- 3b. 时区偏移（命令行参数 > 环境变量 TZ > 默认 8）----
    if tz_offset is None:
        tz_offset = detect_tz_offset()
    info(f"时区偏移: UTC{'+' if float(tz_offset) >= 0 else ''}{tz_offset}")

    # ---- 3c. 模型服务地址（命令行 --model-url > 探测到的服务 > 默认 8081）----
    if not model_url:
        for probe in ["http://127.0.0.1:8081", "http://127.0.0.1:1234", "http://127.0.0.1:11434"]:
            if detect_model_service(probe):
                model_url = probe
                break
    if not model_url:
        model_url = "http://127.0.0.1:8081"
    model_url = model_url.rstrip("/")
    info(f"模型服务地址: {model_url}")

    actions = []

    # ---- 4. 创建记忆目录骨架 ----
    for sub in MEMORY_SUBDIRS:
        d = os.path.join(mem_root, sub)
        actions.append(("mkdir", d))

    # ---- 5. 拷贝模板（存在即跳过）----
    tpl_root = os.path.join(HERE, "templates")
    if os.path.isdir(tpl_root):
        for dp, dn, fn in os.walk(tpl_root):
            for f in fn:
                src = os.path.join(dp, f)
                rel = os.path.relpath(src, tpl_root)
                dst = os.path.join(mem_root, rel)
                actions.append(("copy-if-absent", (src, dst)))

    # ---- 6. 生成 config.json（存在则跳过）----
    cfg_path = os.path.join(HERE, "config.json")
    cfg_data = {
        "memories_root": mem_root.replace("\\", "/"),
        "scripts_root": HERE.replace("\\", "/"),
        "model_name": "<YOUR_MODEL_GGUF_PATH>",
        "llama_bin": (detect_llama_bin() or "").replace("\\", "/"),
        "server_url": model_url,
        "daily_dir": mem_root.replace("\\", "/") + "/daily",
        "system_dir": mem_root.replace("\\", "/") + "/system",
        "core_dir": mem_root.replace("\\", "/") + "/core",
        "longterm_dir": mem_root.replace("\\", "/") + "/long-term",
        "semantic_dir": mem_root.replace("\\", "/") + "/semantic",
    }
    actions.append(("write-if-absent", (cfg_path, json.dumps(cfg_data, ensure_ascii=False, indent=2))))

    # ---- 7. 拷贝 hook / adapter 到目标，再改目标副本的占位符 ----
    # 注意：绝不改写仓库源文件（源文件保持模板状态），只改拷贝后的副本。
    hooks_src = os.path.join(HERE, "hooks", "daily-autolog")
    adapter_src = os.path.join(HERE, "adapters", "openclaw")
    if oc_root:
        hooks_dst = os.path.join(oc_root, "hooks", "daily-autolog")
        adapter_dst = os.path.join(oc_root, "extensions", "stellamem-adapter")
        actions.append(("copy-tree", (hooks_src, hooks_dst)))
        actions.append(("copy-tree", (adapter_src, adapter_dst)))
        # 改目标副本的占位符（源文件不动）
        actions.append(("replace", (os.path.join(hooks_dst, "handler.js"), [
            ('const AI_NAME = "你的AI名字";', f'const AI_NAME = "{ai_name}";'),
            ('const SESSIONS_DIR = "<YOUR_OPENCLAW_SESSIONS_DIR>";',
             f'const SESSIONS_DIR = "{sess.replace(chr(92), "/")}";' if sess else None),
            ('const DAILY_DIR = "<YOUR_DAILY_DIR>";',
             f'const DAILY_DIR = "{mem_root.replace(chr(92), "/")}/daily";'),
            ('const TIMEZONE_OFFSET = "<YOUR_TZ_OFFSET>";',
             f'const TIMEZONE_OFFSET = "{tz_offset}";'),
            ('"<YOUR_MODEL_URL>"',
             f'"{cfg_data["server_url"].rstrip(chr(47))}/v1/chat/completions"'),
        ])))
        actions.append(("replace", (os.path.join(adapter_dst, "index.js"), [
            ('const ROOT = "<YOUR_MEMORIES_PATH>";',
             f'const ROOT = "{mem_root.replace(chr(92), "/")}";'),
        ])))

    # ---- 9. 生成 openclaw snippet（结构对齐真实 openclaw.json）----
    snippet = {
        "hooks": {
            "internal": {
                "enabled": True,
                "entries": {"daily-autolog": {"enabled": True}},
            }
        },
        "plugins": {
            "allow": ["stellamem-adapter"],
            "entries": {"stellamem-adapter": {"enabled": True}},
        },
        "agents": {
            "defaults": {
                "memorySearch": {
                    "enabled": True,
                    "provider": "none",
                    "extraPaths": [
                        mem_root.replace("\\", "/") + "/long-term",
                        mem_root.replace("\\", "/") + "/semantic",
                    ],
                }
            }
        },
    }
    out_dir = os.path.join(HERE, "setup-out")
    actions.append(("write", (os.path.join(out_dir, "openclaw.snippet.json"),
                              json.dumps(snippet, ensure_ascii=False, indent=2))))
    if merge_openclaw and oc_root:
        actions.append(("merge-openclaw", (oc_root, snippet)))
    actions.append(("install-report", (out_dir, mem_root, merge_openclaw)))

    # ---- 执行 ----
    print()
    info(f"共 {len(actions)} 个动作：")
    for kind, arg in actions:
        if kind == "copy-tree":
            src, dst = arg
            note = "（若目标已存在，会先备份旧目录再替换）"
            print(f"    - [copy-tree] {src}  →  {dst}  {note}")
        elif kind == "replace":
            path, pairs = arg
            print(f"    - [replace] {path}  （改写副本占位符，源文件不动）")
        elif kind == "merge-openclaw":
            print(f"    - [merge-openclaw] 先备份 openclaw.json，再只追加 keys（冲突字段保留原值 + 记 diff）")
        else:
            print(f"    - [{kind}] {arg if isinstance(arg, str) else arg[0]}")

    if dry_run:
        print()
        info("干跑结束，未改动任何文件。去掉 --dry-run 才会真执行。")
        return 0

    print()
    created, skipped, modified = 0, 0, 0
    for kind, arg in actions:
        try:
            if kind == "mkdir":
                if not os.path.isdir(arg):
                    os.makedirs(arg, exist_ok=True)
                    created += 1
                else:
                    skipped += 1
            elif kind == "copy-if-absent":
                src, dst = arg
                if os.path.exists(dst):
                    skipped += 1
                else:
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copyfile(src, dst)
                    created += 1
            elif kind == "write-if-absent":
                path, content = arg
                if os.path.exists(path):
                    skipped += 1
                else:
                    write_text(path, content)
                    created += 1
            elif kind == "write":
                path, content = arg
                write_text(path, content)
                modified += 1
            elif kind == "replace":
                path, pairs = arg
                if not os.path.exists(path):
                    warn(f"文件不存在，跳过占位符替换: {path}")
                    continue
                txt = read_text(path)
                changed = False
                for old, new in pairs:
                    if new is None:
                        warn(f"  · 无法推断的值，请手动改: {old}")
                        continue
                    txt, did = replace_placeholder(txt, old, new)
                    if did:
                        changed = True
                        ok(f"  · 已替换: {new}")
                if changed:
                    write_text(path, txt)
                    modified += 1
            elif kind == "copy-tree":
                src, dst = arg
                if not os.path.isdir(src):
                    continue
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if os.path.isdir(dst):
                    # 更新已有 hook / adapter 前，先备份旧目录（不悄悄删除）
                    bak = dst + ".bak-" + datetime.now().strftime("%Y%m%d-%H%M%S")
                    shutil.copytree(dst, bak)
                    ok(f"已备份旧目录 → {bak}")
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
                created += 1
            elif kind == "merge-openclaw":
                oc_root2, snip = arg
                try:
                    bak, cfg, conflicts = merge_openclaw_config(oc_root2, snip)
                    ok(f"已备份 openclaw.json → {os.path.basename(bak)}")
                    ok("已合并 hooks / plugins / agents.defaults.memorySearch（只追加，未覆盖已有字段）")
                    if conflicts:
                        warn(f"发现 {len(conflicts)} 处字段冲突：已保留你的原值，未写入。请手动决定：")
                        for c in conflicts:
                            print(f"        - {c['path']}")
                            print(f"            保留: {json.dumps(c['existing'], ensure_ascii=False)[:120]}")
                            print(f"            拟加: {json.dumps(c['suggested'], ensure_ascii=False)[:120]}")
                        diff_path = os.path.join(HERE, "setup-out", "openclaw.merge-conflicts.md")
                        try:
                            os.makedirs(os.path.dirname(diff_path), exist_ok=True)
                            with open(diff_path, "w", encoding="utf-8", newline="\n") as dfh:
                                dfh.write("# openclaw.json 合并冲突（未自动写入）\n\n")
                                dfh.write("以下字段你的配置里已存在且值不同，安装器**保持原值不变**，未做任何覆盖。\n")
                                dfh.write("如需采用 Stellamem 的建议值，请手动修改并用候重启。\n\n")
                                for c in conflicts:
                                    dfh.write(f"## {c['path']}\n\n")
                                    dfh.write(f"- 你的原值（已保留）：`{json.dumps(c['existing'], ensure_ascii=False)}`\n")
                                    dfh.write(f"- Stellamem 建议值（未写入）：`{json.dumps(c['suggested'], ensure_ascii=False)}`\n\n")
                            ok(f"冲突 diff 已写入 setup-out/openclaw.merge-conflicts.md")
                        except OSError as e:
                            warn(f"写冲突 diff 失败：{e}")
                    else:
                        ok("无字段冲突（你的已有配置全数保留）")
                    modified += 1
                except Exception as e:
                    err(f"合并 openclaw.json 失败（已跳过，可手动合并 snippet）: {e}")
            elif kind == "install-report":
                out_dir2, mem, did_merge = arg
                merge_line = (
                    "✅ 已自动合并 openclaw.json（已备份）"
                    if did_merge
                    else "⚠️ 未自动合并：请手动合并 setup-out/openclaw.snippet.json 到 openclaw.json"
                )
                report = f"""# Stellamem 安装报告

- 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
- 记忆根目录: {mem}
- OpenClaw 根目录: {oc_root or '(未探测到)'}
- Sessions 目录: {sess or '(未探测到)'}
- AI 名字: {ai_name}
- openclaw.json 合并: {merge_line}

## 下一步

1. 配置模型服务（见 INSTALL.md 第 4 节）
2. 重启 OpenClaw（使 hook 和 plugin 生效）
3. 验证：python compiler/memory.py doctor
"""
                write_text(os.path.join(out_dir2, "install-report.md"), report)
                modified += 1
        except Exception as e:
            err(f"动作失败 [{kind}] {arg}: {e}")

    print()
    ok(f"安装完成：新建 {created}，跳过（已存在） {skipped}，修改 {modified}")
    print()
    info("接下来请看 setup-out/install-report.md，并按其中「下一步」操作。")
    return 0


# ============ main ============
def build_parser():
    p = argparse.ArgumentParser(
        prog="setup.py",
        description="Stellamem 一键安装器（AI 可执行）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n"
               "  python setup.py check\n"
               "  python setup.py install --dry-run\n"
               "  python setup.py install --yes --ai-name 小助手 --mem-root D:/my-memory --merge-openclaw",
    )
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("check", help="环境自检（只读，不改任何文件）")

    inst = sub.add_parser("install", help="一键安装（幂等，重复跑安全）")
    inst.add_argument("--dry-run", action="store_true", help="干跑，只打印将做什么")
    inst.add_argument("--yes", action="store_true", help="跳过交互确认，使用提供的参数或默认值")
    inst.add_argument("--ai-name", help="AI 名字（日记以谁的第一人称写）")
    inst.add_argument("--mem-root", help="记忆根目录（绝对路径）")
    inst.add_argument("--merge-openclaw", action="store_true",
                      help="自动备份并合并 openclaw.json（只追加 keys；冲突字段保留原值 + 写 diff 报告）")
    inst.add_argument("--model-url", help="OpenAI 兼容模型服务地址，如 http://127.0.0.1:11434")
    inst.add_argument("--oc-root",
                      help="OpenClaw 根目录（默认探测真实 ~/.openclaw）；"
                           "测试/冷启动请指向临时目录，如 --oc-root D:/tmp/.openclaw")

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return 0
    if args.cmd == "check":
        cmd_check()
        return 0
    if args.cmd == "install":
        return cmd_install(
            dry_run=args.dry_run,
            yes=args.yes,
            ai_name=args.ai_name,
            mem_root=args.mem_root,
            merge_openclaw=args.merge_openclaw,
            model_url=args.model_url,
            oc_root_override=getattr(args, "oc_root", None),
        )
    err(f"未知命令：{args.cmd}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
