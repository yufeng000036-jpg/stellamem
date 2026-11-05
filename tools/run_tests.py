"""跑全量 Python 测试，把输出同时写一份到 test-output.txt 供 CI 归档。

背景：CI 上 Windows/Ubuntu 偶发测试失败时，Actions 日志要仓库管理员权限
才能下载，排障看不到是哪条挂了。把输出落盘并由 workflow 以 artifact 上传，
以后任何人都能直接下载看到失败的那条测试。
"""

import subprocess
import sys
from pathlib import Path


def main() -> int:
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    Path("test-output.txt").write_text(proc.stdout + proc.stderr, encoding="utf-8")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
