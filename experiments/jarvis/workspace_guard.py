"""Private experiment state must stay within the marked, single-user directory."""

import os
import stat
from pathlib import Path


def ordinary(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("实验目录含链接或重解析点，请改用独立本地目录")
    if not stat.S_ISDIR(info.st_mode) and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
        raise ValueError("实验目录含非常规文件")
    return info


def check_location(home):
    if home != Path(os.path.abspath(home)) or str(home).startswith(("//", "\\\\")):
        raise ValueError("请选择本地绝对路径")
    for parent in (*reversed(home.parents), home):
        if os.path.lexists(parent):
            ordinary(parent)


def check_state(home):
    check_location(home)
    for name in (
        "workspace.id",
        "tasks.db",
        "tasks.db-wal",
        "tasks.db-shm",
        "tasks.db-journal",
        "model.json",
        "control.stellamem.lock",
        "memory",
        "documents",
    ):
        path = home / name
        if os.path.lexists(path):
            ordinary(path)

    def visit(folder):
        for entry in folder.iterdir():
            info = ordinary(entry)
            if stat.S_ISDIR(info.st_mode):
                visit(entry)

    if (home / "memory").is_dir():
        visit(home / "memory")
