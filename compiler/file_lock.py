"""Cross-platform atomic file locking and UTF-8 safe file operations for Stellamem."""

from __future__ import annotations

import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


class LockTimeoutError(TimeoutError):
    """Raised when acquiring a cross-platform file lock exceeds the timeout."""


def fsync_file_safe(path: Path) -> None:
    """Flush file data to disk safely across Windows and POSIX."""
    flags = os.O_RDWR | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(str(path), flags)
    except PermissionError:
        # Fallback if file is read-only
        fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_BINARY", 0))
    try:
        os.fsync(fd)
    except OSError:
        if sys.platform != "win32":
            raise
    finally:
        os.close(fd)


def fsync_dir_safe(path: Path) -> None:
    """Flush directory entry metadata on POSIX; safe no-op on Windows."""
    if sys.platform == "win32":
        return
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def cross_platform_lock(
    target_path: Path | str,
    timeout: float = 10.0,
    poll_interval: float = 0.02,
) -> Iterator[Path]:
    """Acquire an exclusive cross-platform lock for a target file or resource.

    Uses OS-level file locking (`msvcrt.locking` on Windows, `fcntl.flock` on POSIX)
    on a dedicated `.lock` sidecar file so the target file itself can be freely
    replaced via `os.replace()` on Windows without triggering PermissionError.
    """
    target = Path(target_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_name(f".{target.name}.stellamem.lock")

    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0)
    deadline = time.monotonic() + timeout
    # Windows：多线程同时首次创建 .lock 边车文件时会 EACCES（PermissionError），
    # 不是锁冲突，是创建竞态——和 msvcrt.locking 用同一个 deadline 重试。
    # POSIX 上同时 O_CREAT 不会失败，重试分支走不到，不影响行为。
    while True:
        try:
            fd = os.open(str(lock_path), flags, 0o600)
            break
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(poll_interval)
    locked = False

    try:
        while True:
            try:
                if sys.platform == "win32":
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise LockTimeoutError(
                        f"Timed out waiting {timeout}s for lock: {lock_path}"
                    ) from None
                time.sleep(poll_interval)
        yield lock_path
    finally:
        if locked:
            try:
                if sys.platform == "win32":
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)


def atomic_append_jsonl(path: Path | str, payload: dict[str, Any]) -> None:
    """Thread-safe and process-safe atomic JSONL append with torn-tail healing."""
    import json

    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    line_bytes = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")

    with cross_platform_lock(target):
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0)
        fd = os.open(str(target), flags, 0o600)
        try:
            size = os.lseek(fd, 0, os.SEEK_END)
            if size > 0:
                os.lseek(fd, size - 1, os.SEEK_SET)
                last_byte = os.read(fd, 1)
                if last_byte != b"\n":
                    os.lseek(fd, 0, os.SEEK_END)
                    os.write(fd, b"\n")
            os.lseek(fd, 0, os.SEEK_END)
            written = 0
            while written < len(line_bytes):
                n = os.write(fd, line_bytes[written:])
                if n <= 0:
                    raise OSError("Short write while appending JSONL")
                written += n
            os.fsync(fd)
        finally:
            os.close(fd)
        fsync_dir_safe(target.parent)


def atomic_write_text(path: Path | str, content: str) -> None:
    """Crash-safe atomic UTF-8 file write with guaranteed staging cleanup on error."""
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(f".{target.name}.tmp-{os.getpid()}-{time.time_ns()}")

    with cross_platform_lock(target):
        try:
            with open(staging, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(str(staging), str(target))
            fsync_dir_safe(target.parent)
        finally:
            if staging.exists():
                try:
                    staging.unlink()
                except OSError:
                    pass
