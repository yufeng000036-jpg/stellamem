"""Install Git pre-commit hook to prevent quality CI failures and SHA256SUMS drift."""

from __future__ import annotations

import os
import stat
from pathlib import Path

PRE_COMMIT_SCRIPT = """#!/usr/bin/env sh
# Stellamem Pre-Commit Quality & Hygiene Gate
set -e

echo "[stellamem pre-commit] 1/4 Running ruff check..."
python -m ruff check .

echo "[stellamem pre-commit] 2/4 Running ruff format --check..."
python -m ruff format --check .

echo "[stellamem pre-commit] 3/4 Running secret_scan.py..."
if [ -f tools/secret_scan.py ]; then
    python tools/secret_scan.py
fi

echo "[stellamem pre-commit] 4/4 Syncing SHA256SUMS.txt for tracked files..."
if [ -f tools/update_sha256.py ]; then
    python tools/update_sha256.py
    git add SHA256SUMS.txt
fi

echo "[stellamem pre-commit] All checks passed!"
"""


def install_pre_commit_hook(repo_root: Path | str) -> Path:
    root = Path(repo_root).resolve()
    hooks_dir = root / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hooks_dir / "pre-commit"
    hook_path.write_text(PRE_COMMIT_SCRIPT, encoding="utf-8", newline="\n")
    st = os.stat(hook_path)
    os.chmod(hook_path, st.st_mode | stat.S_IEXEC)
    return hook_path


if __name__ == "__main__":
    installed = install_pre_commit_hook(Path.cwd())
    print(f"Installed pre-commit hook at: {installed}")
