"""冻结文件核对：哈希与物理 byte_count 都对才放行（3b）。

哈希抓编码转换/等长篡改，字节数抓未落盘截断文件；两者缺一不可。
冻结内容口径本身不动：清单、REVISION、DATA_SHA、evaluation-lock.json
里的 4 个哈希值，以及 freeze() 的采样逻辑，测试里逐一钉死。
"""

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1] / "benchmark" / "longmemeval"
sys.path.insert(0, str(BENCH))
spec = importlib.util.spec_from_file_location("longmemeval_freeze", BENCH / "freeze.py")
freeze = importlib.util.module_from_spec(spec)
spec.loader.exec_module(freeze)

EXPECTED_LOCK = {
    "worker.py": "2ee87a9072e8e9c0a53520f6f9ba109d2c4dfc941a5dabfeadb05850312e99bf",
    "evaluate.py": "03c89774222239e9a453e18005702e7e0b5487a32ed981ce70bad8484e349204",
    "PROTOCOL.md": "4f429d20358d3778340ef1b67ca164189e523eff5c4ec758250eaa51cdacd650",
    "manifest.json": "2727ffec9bc2ded3a43fd1686e1410533ceb6bfb010358ecaec6b74de99d933f",
}


class FreezeVerificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        for name in list(freeze.FROZEN_FILES) + [freeze.EVALUATION_LOCK_FILENAME]:
            shutil.copy2(BENCH / name, self.dir / name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_intact_files_verify(self):
        checked = freeze.verify_frozen_files(self.dir)
        self.assertEqual(set(checked), set(freeze.FROZEN_FILES))
        for name, info in checked.items():
            self.assertEqual(info["byte_count"], freeze.FROZEN_FILE_BYTE_COUNTS[name])
        # 真实目录同样通过
        self.assertEqual(len(freeze.verify_frozen_files()), 5)

    def test_truncated_file_is_blocked_by_byte_count(self):
        """截断文件：size 小于预期，字节数一关直接拦住（不等哈希）。"""
        path = self.dir / "worker.py"
        raw = path.read_bytes()
        path.write_bytes(raw[: len(raw) // 2])
        with self.assertRaisesRegex(ValueError, "字节数不符"):
            freeze.verify_frozen_files(self.dir)

    def test_equal_length_tamper_is_blocked_by_hash(self):
        """等长篡改：size 对、内容不对，哈希一关拦住。"""
        path = self.dir / "evaluate.py"
        raw = bytearray(path.read_bytes())
        raw[100] = (raw[100] + 1) % 256  # 改一个字节，长度不变
        path.write_bytes(bytes(raw))
        self.assertEqual(len(path.read_bytes()), freeze.FROZEN_FILE_BYTE_COUNTS["evaluate.py"])
        with self.assertRaisesRegex(ValueError, "哈希不符"):
            freeze.verify_frozen_files(self.dir)

    def test_missing_file_is_blocked(self):
        (self.dir / "manifest.json").unlink()
        with self.assertRaisesRegex(ValueError, "冻结文件缺失"):
            freeze.verify_frozen_files(self.dir)

    def test_freeze_py_byte_count_constant_matches_disk(self):
        """freeze.py 自身字节数期望值与落盘文件一致；改 freeze.py 不更新
        常量，核对就失败——这是有意的冻结语义，不是漂移。"""
        actual = (BENCH / "freeze.py").stat().st_size
        self.assertEqual(freeze.FROZEN_FILE_BYTE_COUNTS["freeze.py"], actual)

    def test_frozen_content_untouched(self):
        """冻结内容口径不动：清单、REVISION、DATA_SHA、锁内哈希值。"""
        self.assertEqual(
            freeze.FROZEN_FILES,
            ("worker.py", "evaluate.py", "PROTOCOL.md", "manifest.json", "freeze.py"),
        )
        self.assertEqual(freeze.REVISION, "98d7416c24c778c2fee6e6f3006e7a073259d48f")
        self.assertEqual(
            freeze.DATA_SHA, "821a2034d219ab45846873dd14c14f12cfe7776e73527a483f9dac095d38620c"
        )
        lock = json.loads((BENCH / "evaluation-lock.json").read_text(encoding="utf-8"))
        self.assertEqual(lock, EXPECTED_LOCK)

    def test_verify_cli_passes_and_fails(self):
        # 子进程按 UTF-8 解码输出：freeze.py 入口已把 stdout/stderr
        # 重配成 UTF-8（Windows 控制台默认编码下中文报错会被转义成
        # \\uXXXX，断言不到原文）；这边按 UTF-8 接才对得上。
        ok = subprocess.run(
            [sys.executable, str(BENCH / "freeze.py"), "--verify"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(ok.returncode, 0)
        self.assertIn("frozen files verified", ok.stdout)
        path = self.dir / "worker.py"
        path.write_bytes(path.read_bytes()[:100])
        bad = subprocess.run(
            [sys.executable, str(BENCH / "freeze.py"), "--verify", "--directory", str(self.dir)],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("字节数不符", bad.stderr)


if __name__ == "__main__":
    unittest.main()
