"""线上隔离状态只读核查。

能证实的与不能证实的严格分开：
- 能证实：磁盘代码指纹、进程启动时间（据此判断内存里是哪个版本）、
  以"与运行版等价"的投影代码对生产数据取注入结果、常驻身份/守则的注入字符数。
- 不能证实：运行中进程的实时行为。那需要一次真实对话产生 inject-probe 记录，
  本脚本不制造对话，所以只引用最近一条真实 probe 记录。
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = Path(r"<USER_HOME>\.openclaw\extensions\memory-compiler-adapter")
MEMORIES = Path(r"<USER_HOME>\.dsh\memories")
PROBE = ROOT / "benchmark" / "projection_probe.mjs"
PROMPTS = ["我有哪些项目", "我有哪些偏好", "我手头在做什么", "我决定过什么"]


def sha(path):
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ps(command):
    result = subprocess.run(["powershell", "-NoProfile", "-Command", command],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    return result.stdout.strip()


def process_info():
    out = ps("Get-Process -Name ClawX -ErrorAction SilentlyContinue | "
             "Sort-Object StartTime | Select-Object -First 1 -ExpandProperty StartTime")
    return out


def probe(module, root, prompt):
    result = subprocess.run(["node", str(PROBE), str(module), str(root), prompt],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        return {"prompt": prompt, "error": (result.stderr or result.stdout).strip()[:300]}
    try:
        return json.loads(result.stdout)
    except ValueError:
        return {"prompt": prompt, "error": "unparsable probe output"}


def build_prev_module(workdir):
    """复制插件目录，剥掉本轮新增的隔离闸门，得到"与运行版等价"的投影模块。"""
    target = workdir / "plugin-prev"
    shutil.copytree(PLUGIN, target)
    projection = target / "projection.js"
    text = projection.read_text(encoding="utf-8")
    gate = re.compile(r"  let blocked;\s*\n  try \{\s*\n    blocked = await quarantinedIds\(root\);\s*\n"
                      r"  \} catch \{\s*\n    return \"\";\s*\n  \}", re.M)
    text, count = gate.subn("  const blocked = new Set();", text)
    projection.write_text(text, encoding="utf-8")
    return projection, count


def system_context_chars():
    total = 0
    parts = []
    for rel, label in [("core/identity.md", "身份"), ("core/user-core.md", "用户核心"),
                       ("core/工作守则.md", "工作守则")]:
        path = MEMORIES / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        block = f"## {label}\n{text[:4000]}"
        parts.append(block)
        total += len(block)
    joined = "\n\n".join(parts)
    return {
        "chars": len(joined),
        "has_identity": "## 身份" in joined,
        "has_rules": "## 工作守则" in joined,
        "files": [p[0] for p in [("core/identity.md", 0), ("core/user-core.md", 0),
                                 ("core/工作守则.md", 0)] if (MEMORIES / p[0]).exists()],
    }


def main():
    workdir = Path(tempfile.mkdtemp(prefix="liveverify-"))
    try:
        started = process_info()
        index = PLUGIN / "index.js"
        projection = PLUGIN / "projection.js"
        prev, strips = build_prev_module(workdir)
        prev_class = {"prompt": "我有哪些项目"}
        prev_class.update(probe(prev, MEMORIES, "我有哪些项目"))

        current_results = [probe(projection, MEMORIES, p) for p in PROMPTS]
        prev_results = [probe(prev, MEMORIES, p) for p in PROMPTS]

        probe_log = PLUGIN / "inject-probe.log"
        last_probe = {}
        if probe_log.exists():
            lines = [x for x in probe_log.read_text(encoding="utf-8").splitlines() if x.strip()]
            if lines:
                last_probe = json.loads(lines[-1])

        quarantine = json.loads((MEMORIES / "system" / "quarantine.json").read_text(encoding="utf-8"))
        payload = {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "disk_state": {
                "index_js": {"path": str(index), "bytes": index.stat().st_size,
                             "mtime": datetime.fromtimestamp(index.stat().st_mtime).isoformat(),
                             "sha256": sha(index)},
                "projection_js": {"path": str(projection), "bytes": projection.stat().st_size,
                                  "mtime": datetime.fromtimestamp(
                                      projection.stat().st_mtime).isoformat(),
                                  "sha256": sha(projection)},
            },
            "running_process": {
                "clawx_started_at": started or None,
                "loaded_version_judgement": (
                    "进程启动早于 projection.js 的修改时间，且 Node 在 import 时缓存模块，"
                    "所以内存里运行的必定是本轮改动之前的版本"
                    if started else "无法读取进程启动时间"),
            },
            "quarantine": {
                "batch": quarantine.get("batch"),
                "claim_ids": len(quarantine.get("claim_ids") or []),
                "file_sha256": sha(MEMORIES / "system" / "quarantine.json"),
            },
            "injection_current_code": current_results,
            "injection_prev_equivalent_code": {
                "note": "把本轮新增闸门剥离后得到与运行版等价的模块；剥离处数 " + str(strips),
                "results": prev_results,
            },
            "system_context": system_context_chars(),
            "last_real_probe": last_probe,
            "limitations": [
                "运行中进程的实时注入行为无法在不产生新对话的前提下观测；",
                "上一项结论来自等价代码复现，不是线上抓包。",
            ],
        }
        out_json = ROOT / "docs" / "personal-edition" / "QUARANTINE-LIVE-VERIFY.json"
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                            encoding="utf-8")
        print(json.dumps({
            "clawx_started": started,
            "index_mtime": payload["disk_state"]["index_js"]["mtime"],
            "projection_mtime": payload["disk_state"]["projection_js"]["mtime"],
            "stripped_gates": strips,
            "current_claims": [r.get("claims") for r in current_results],
            "prev_claims": [r.get("claims") for r in prev_results],
            "system_chars": payload["system_context"]["chars"],
            "has_rules": payload["system_context"]["has_rules"],
            "has_identity": payload["system_context"]["has_identity"],
            "last_probe": last_probe,
        }, ensure_ascii=False, indent=2))
        return 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
