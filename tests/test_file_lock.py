"""os.replace 有界重试回归测试（Windows 并发替换竞态）。

Windows 上 os.replace 不能替换正被另一进程打开着的目标文件（没有 delete
共享），并发读者/写者会瞬间触发 PermissionError（WinError 5）——预算闸门
多进程并发记账测试在 Windows CI 上就是这样间歇失败的。Linux 上替换打开着
的文件能成功，真实竞态无法在本平台复现；这里用 mock 让 os.replace 先抛
PermissionError 再成功，直接覆盖重试逻辑本身，Windows CI 同样守得住。

覆盖两个原子写入口：compiler/file_lock.py 的 atomic_write_text_locked
（cloud_client 账本落盘走这条）与 compiler/storage.py 的 atomic_write
（事务提交/恢复走这条，同样的未重试模式）。

策略断言：只重试 PermissionError；预算耗尽后最后一个错误原样抛出；
其他 OSError 第一次就抛、不重试。
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "compiler"))

import storage  # noqa: E402
from compiler import file_lock  # noqa: E402

REAL_REPLACE = os.replace


def flaky_replace(failures: int, error: BaseException):
    """前 failures 次调用抛 error，之后委托给真正的 os.replace；返回 (替身, 计数器)。"""
    calls = {"n": 0}

    def _replace(src, dst):
        calls["n"] += 1
        if calls["n"] <= failures:
            raise error
        return REAL_REPLACE(src, dst)

    return _replace, calls


class FileLockReplaceRetryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.target = Path(self._tmp.name) / "ledger.json"
        # poll 间隔压到 0：重试逻辑照走，但测试不真的睡。
        patcher = mock.patch.object(file_lock, "_REPLACE_POLL_INTERVAL", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_retries_permission_error_then_succeeds(self):
        self.target.write_text("old", encoding="utf-8")
        flaky, calls = flaky_replace(3, PermissionError(5, "Access is denied"))
        with mock.patch("os.replace", flaky):
            file_lock.atomic_write_text_locked(self.target, '{"new": true}')
        self.assertEqual(calls["n"], 4)
        self.assertEqual(self.target.read_text(encoding="utf-8"), '{"new": true}')

    def test_permission_error_propagates_after_budget_exhausted(self):
        flaky, calls = flaky_replace(999, PermissionError(5, "Access is denied"))
        with (
            mock.patch.object(file_lock, "_REPLACE_TIMEOUT", 0),
            mock.patch("os.replace", flaky),
        ):
            with self.assertRaises(PermissionError):
                file_lock.atomic_write_text_locked(self.target, "x")
        # 预算为 0：第一次失败就该抛出，不多试。
        self.assertEqual(calls["n"], 1)

    def test_other_oserror_raises_immediately_without_retry(self):
        flaky, calls = flaky_replace(999, FileNotFoundError("no such file"))
        with mock.patch("os.replace", flaky):
            with self.assertRaises(FileNotFoundError):
                file_lock.atomic_write_text_locked(self.target, "x")
        self.assertEqual(calls["n"], 1)

    def test_plain_write_and_overwrite_round_trip(self):
        file_lock.atomic_write_text(self.target, "第一版")
        self.assertEqual(self.target.read_text(encoding="utf-8"), "第一版")
        file_lock.atomic_write_text(self.target, "第二版")
        self.assertEqual(self.target.read_text(encoding="utf-8"), "第二版")


class StorageReplaceRetryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.target = Path(self._tmp.name) / "journal.json"
        patcher = mock.patch.object(storage, "_REPLACE_POLL_INTERVAL", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_retries_permission_error_then_succeeds(self):
        flaky, calls = flaky_replace(2, PermissionError(5, "Access is denied"))
        with mock.patch("os.replace", flaky):
            storage.atomic_write(self.target, "payload")
        self.assertEqual(calls["n"], 3)
        self.assertEqual(self.target.read_text(encoding="utf-8"), "payload")

    def test_permission_error_propagates_after_budget_exhausted(self):
        flaky, calls = flaky_replace(999, PermissionError(5, "Access is denied"))
        with (
            mock.patch.object(storage, "_REPLACE_TIMEOUT", 0),
            mock.patch("os.replace", flaky),
        ):
            with self.assertRaises(PermissionError):
                storage.atomic_write(self.target, "x")
        self.assertEqual(calls["n"], 1)

    def test_other_oserror_raises_immediately_without_retry(self):
        flaky, calls = flaky_replace(999, IsADirectoryError("is a directory"))
        with mock.patch("os.replace", flaky):
            with self.assertRaises(IsADirectoryError):
                storage.atomic_write(self.target, "x")
        self.assertEqual(calls["n"], 1)


if __name__ == "__main__":
    unittest.main()
