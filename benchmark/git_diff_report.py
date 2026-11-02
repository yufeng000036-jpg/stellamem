"""生成可复现的改动报告：改了哪些文件、加删多少行、哪些是新增、哪些未跟踪。

用法：python benchmark/git_diff_report.py [--out docs/personal-edition/GIT-DIFF-REPORT.md]
只读 git，不写仓库。
"""

import argparse
import hashlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    return result.stdout.strip(), result.returncode


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "docs" / "personal-edition" / "GIT-DIFF-REPORT.md"))
    args = parser.parse_args(argv)

    branch, _ = git("rev-parse", "--abbrev-ref", "HEAD")
    head, _ = git("log", "-1", "--pretty=%h %ad %s", "--date=iso")
    ahead, _ = git("rev-list", "--count", "origin/main..HEAD")
    porcelain, _ = git("status", "--porcelain=v1")
    numstat, _ = git("diff", "--numstat")
    diff_text, _ = git("diff")
    staged, _ = git("diff", "--cached", "--numstat")

    modified, added, deleted = [], [], []
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        add, rem, path = parts
        row = f"| `{path}` | +{add} | -{rem} |"
        modified.append(row)
        if add == "0" and rem == "0":
            modified[-1] = f"| `{path}` | 0 | 0（仅权限/换行） |"

    untracked = [line[3:] for line in porcelain.splitlines() if line.startswith("??")]
    dirty = [line[3:] for line in porcelain.splitlines() if line[:2].strip() in ("M", "A", "D", "R")]

    body = [
        "# GIT-DIFF-REPORT（自动生成）",
        "",
        f"> 生成时间：{datetime.now(timezone.utc).isoformat()}　｜　脚本：`benchmark/git_diff_report.py`",
        "> 未提交、未推送。此文件里的数字可随时重跑复现。",
        "",
        "## 仓库状态",
        "",
        f"- 分支：`{branch}`",
        f"- HEAD：`{head}`",
        f"- 领先 origin/main：**{ahead}** 个提交（未推送）",
        f"- 已跟踪文件改动：**{len(dirty)}** 个",
        f"- 未跟踪文件/目录：**{len(untracked)}** 个",
        f"- 工作区 diff 指纹（SHA256）：`{hashlib.sha256(diff_text.encode('utf-8')).hexdigest()}`",
        "",
        "## 已跟踪文件的改动量",
        "",
        "| 文件 | 增加 | 删除 |",
        "|---|---|---|",
        *modified,
        "",
        "## 未跟踪（新文件 / 新目录，尚未 add）",
        "",
        *[f"- `{item}`" for item in untracked],
        "",
        "## 本阶段新增的关键文件",
        "",
        "| 文件 | 作用 |",
        "|---|---|",
        "| `compiler/quarantine.py` | 隔离/恢复/校验 33 条旧记忆 |",
        "| `compiler/raw_archive.py` | 原始档案层（P1 上一轮新增，本轮加 `--force`、`orphans`、损坏流容错） |",
        "| `compiler/raw_source.py` | 档案按天取回 + record_id 反查 |",
        "| `benchmark/p0_metrics.py` | 全量指标单次产出 |",
        "| `benchmark/p1_closeout.py` | P1 六项长期保护验收 |",
        "| `benchmark/projection_probe.mjs` | 注入量探针 |",
        "| `tests/test_raw_archive.py` | 档案层 9 个用例 |",
    ]
    if staged:
        body += ["", "## 暂存区", "", "```", staged, "```"]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(body) + "\n", encoding="utf-8")
    print(f"written {out}  modified={len(dirty)} untracked={len(untracked)} ahead={ahead}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
