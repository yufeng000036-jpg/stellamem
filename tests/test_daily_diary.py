"""日档五段 + 长期“我变了什么”。

虚构数据、隔离临时目录，不碰生产记忆。
验收点：按主体隔离；合成行有来源标记；无日记那天 no-op；
日档与长期文件不进索引（源指纹不变）。
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import indexer
from daily_diary import (
    SEGMENTS,
    append_diary,
    diary_path,
    diary_status_text,
    long_term_path,
    read_diary,
    read_long_term,
    synthesize_long_term,
    validate_date,
)

REPO = Path(__file__).resolve().parents[1]
DAY = "2026-11-06"


class DiaryWriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "memories"
        self.addCleanup(self.tmp.cleanup)

    def test_append_creates_skeleton_with_five_segments_in_order(self):
        receipt = append_diary(self.root, "user", DAY, "变", "决定以后开播先唱一首再聊")
        self.assertEqual(receipt["segment"], "变")
        text = diary_path(self.root, "user", DAY).read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# 日档（user · 2026-11-06）"))
        heads = [line for line in text.splitlines() if line.startswith("## ")]
        self.assertEqual(heads, [f"## {seg}" for seg in SEGMENTS])
        self.assertIn("- 决定以后开播先唱一首再聊", text)

    def test_lines_land_under_the_right_segment(self):
        append_diary(self.root, "user", DAY, "触点", "钟丽潭点了《枫叶》")
        append_diary(self.root, "user", DAY, "变", "决定先唱一首再聊")
        append_diary(self.root, "user", DAY, "触点", "弹幕说歌单很稳")
        parsed = read_diary(self.root, "user", DAY)
        self.assertEqual(parsed["触点"], ["钟丽潭点了《枫叶》", "弹幕说歌单很稳"])
        self.assertEqual(parsed["变"], ["决定先唱一首再聊"])
        self.assertEqual(parsed["我的想"], [])

    def test_subject_alias_accepted_and_canonicalized(self):
        receipt = append_diary(self.root, "用户", DAY, "变", "虚构变化一行")
        self.assertEqual(receipt["subject"], "user")
        self.assertTrue(diary_path(self.root, "user", DAY).exists())

    def test_unknown_subject_rejected(self):
        with self.assertRaises(ValueError):
            append_diary(self.root, "nobody", DAY, "变", "x")

    def test_unknown_segment_rejected(self):
        with self.assertRaises(ValueError):
            append_diary(self.root, "user", DAY, "心情", "x")

    def test_empty_and_multiline_text_rejected(self):
        with self.assertRaises(ValueError):
            append_diary(self.root, "user", DAY, "变", "   ")
        with self.assertRaises(ValueError):
            append_diary(self.root, "user", DAY, "变", "一行\n另一行")
        self.assertFalse(diary_path(self.root, "user", DAY).exists())

    def test_bad_dates_rejected(self):
        for bad in ("2026-13-01", "2026-02-30", "2026/11/06", "../2026-11-06", "2026-11-6"):
            with self.assertRaises(ValueError, msg=bad):
                validate_date(bad)
        self.assertEqual(validate_date("2026-11-06"), "2026-11-06")

    def test_missing_diary_reads_none_and_status_says_so(self):
        self.assertIsNone(read_diary(self.root, "user", DAY))
        self.assertIn("无日档", diary_status_text(self.root, "user", DAY))

    def test_status_counts_lines(self):
        append_diary(self.root, "user", DAY, "触点", "a")
        append_diary(self.root, "user", DAY, "变", "b")
        status = diary_status_text(self.root, "user", DAY)
        self.assertIn("共 2 行", status)
        self.assertIn("触点1", status)
        self.assertIn("变1", status)


class SubjectIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "memories"
        self.addCleanup(self.tmp.cleanup)

    def test_subjects_have_separate_diaries_and_long_term_files(self):
        append_diary(self.root, "user", DAY, "变", "用户的变化")
        append_diary(self.root, "persona", DAY, "变", "鲸鱼娘的变化")
        self.assertNotEqual(
            diary_path(self.root, "user", DAY),
            diary_path(self.root, "persona", DAY),
        )
        self.assertEqual(read_diary(self.root, "user", DAY)["变"], ["用户的变化"])
        self.assertEqual(read_diary(self.root, "persona", DAY)["变"], ["鲸鱼娘的变化"])
        synthesize_long_term(self.root, "user", DAY)
        synthesize_long_term(self.root, "persona", DAY)
        user_lt = read_long_term(self.root, "user")
        persona_lt = read_long_term(self.root, "persona")
        self.assertIn("用户的变化", user_lt)
        self.assertNotIn("鲸鱼娘的变化", user_lt)
        self.assertIn("鲸鱼娘的变化", persona_lt)
        self.assertNotIn("用户的变化", persona_lt)
        self.assertNotEqual(long_term_path(self.root, "user"), long_term_path(self.root, "persona"))


class SynthesizeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "memories"
        self.addCleanup(self.tmp.cleanup)

    def _seed(self, subject="user"):
        append_diary(self.root, subject, DAY, "触点", "今晚直播了")
        append_diary(self.root, subject, DAY, "变", "决定以后开播先唱一首再聊")
        append_diary(self.root, subject, DAY, "变", "把点歌流程写进流程单")
        append_diary(self.root, subject, DAY, "我的回", "跟观众说明了新流程")

    def test_no_diary_is_noop_and_creates_nothing(self):
        result = synthesize_long_term(self.root, "user", DAY)
        self.assertEqual(result["status"], "noop")
        self.assertEqual(result["reason"], "no-diary")
        self.assertFalse(long_term_path(self.root, "user").exists())
        self.assertFalse(diary_path(self.root, "user", DAY).exists())

    def test_empty_change_segment_is_noop(self):
        append_diary(self.root, "user", DAY, "触点", "今晚直播了")
        result = synthesize_long_term(self.root, "user", DAY)
        self.assertEqual(result["status"], "noop")
        self.assertEqual(result["reason"], "no-changes")
        self.assertFalse(long_term_path(self.root, "user").exists())

    def test_only_change_segment_is_synthesized_with_source_mark(self):
        self._seed()
        result = synthesize_long_term(self.root, "user", DAY)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["added"], 2)
        self.assertEqual(result["skipped"], 0)
        text = read_long_term(self.root, "user")
        self.assertIn("# 我变了什么（user）", text)
        self.assertIn("- 决定以后开播先唱一首再聊（规则合成·来源：2026-11-06 日档“变”）", text)
        self.assertIn("- 把点歌流程写进流程单（规则合成·来源：2026-11-06 日档“变”）", text)
        # 别的段不进长期。
        self.assertNotIn("今晚直播了", text)
        self.assertNotIn("跟观众说明了新流程", text)

    def test_rerun_is_idempotent(self):
        self._seed()
        first = synthesize_long_term(self.root, "user", DAY)
        second = synthesize_long_term(self.root, "user", DAY)
        self.assertEqual((first["added"], first["skipped"]), (2, 0))
        self.assertEqual((second["added"], second["skipped"]), (0, 2))
        text = read_long_term(self.root, "user")
        self.assertEqual(text.count("决定以后开播先唱一首再聊"), 1)

    def test_new_change_next_day_appends_without_rewriting_old(self):
        self._seed()
        synthesize_long_term(self.root, "user", DAY)
        append_diary(self.root, "user", "2026-11-07", "变", "开始固定每周复盘")
        result = synthesize_long_term(self.root, "user", "2026-11-07")
        self.assertEqual(result["added"], 1)
        text = read_long_term(self.root, "user")
        self.assertIn("决定以后开播先唱一首再聊", text)
        self.assertIn("- 开始固定每周复盘（规则合成·来源：2026-11-07 日档“变”）", text)

    def test_synthesize_never_modifies_the_diary(self):
        self._seed()
        before = diary_path(self.root, "user", DAY).read_bytes()
        synthesize_long_term(self.root, "user", DAY)
        self.assertEqual(diary_path(self.root, "user", DAY).read_bytes(), before)


class IndexExclusionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "memories"
        self.addCleanup(self.tmp.cleanup)

    def test_diary_files_do_not_change_index_fingerprint(self):
        # 日档与长期文件写前后，源指纹一模一样——索引器根本看不见它们。
        before = indexer.source_fingerprint(self.root)
        append_diary(self.root, "user", DAY, "变", "指纹里绝不会出现的虚构变化")
        append_diary(self.root, "persona", DAY, "触点", "另一主体的一行")
        synthesize_long_term(self.root, "user", DAY)
        after = indexer.source_fingerprint(self.root)
        self.assertEqual(before, after)
        flat = sorted((self.root / "daily").glob("*.md"))
        self.assertEqual(flat, [])


class CLITests(unittest.TestCase):
    def _run(self, *args):
        # 模拟 Windows 控制台：PYTHONIOENCODING=cp1252、PYTHONUTF8=0。
        # 子进程必须自己把 stdout/stderr 切到 UTF-8（main() 里做）。
        # 父进程侧按 UTF-8 严格解码，不靠 locale 猜。
        proc = subprocess.run(
            [sys.executable, str(REPO / "compiler" / "daily_diary.py"), *args],
            capture_output=True,
            env=dict(os.environ, PYTHONIOENCODING="cp1252", PYTHONUTF8="0"),
            timeout=30,
        )
        return SimpleNamespace(
            returncode=proc.returncode,
            stdout=proc.stdout.decode("utf-8", errors="strict"),
            stderr=proc.stderr.decode("utf-8", errors="strict"),
        )

    def test_cli_append_show_synthesize(self):
        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memories")
            r = self._run(
                "--root",
                root,
                "--subject",
                "user",
                "--date",
                DAY,
                "--append",
                "--segment",
                "变",
                "--text",
                "决定以后开播先唱一首再聊",
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("已写入", r.stdout)
            r = self._run("--root", root, "--subject", "user", "--date", DAY, "--show")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("决定以后开播先唱一首再聊", r.stdout)
            r = self._run("--root", root, "--subject", "user", "--date", DAY, "--synthesize")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("新增 1 行", r.stdout)
            r = self._run("--root", root, "--subject", "user", "--long-term")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("规则合成·来源：2026-11-06", r.stdout)

    def test_cli_synthesize_without_diary_is_noop_exit_zero(self):
        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memories")
            r = self._run("--root", root, "--subject", "user", "--date", DAY, "--synthesize")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("无日档，什么都不做", r.stdout)
            self.assertFalse((Path(root) / "long-term").exists())

    def test_cli_bad_subject_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memories")
            r = self._run(
                "--root",
                root,
                "--subject",
                "nobody",
                "--date",
                DAY,
                "--append",
                "--segment",
                "变",
                "--text",
                "x",
            )
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("日档失败", r.stderr)

    def test_main_reconfigures_stdout_to_utf8(self):
        # 进程内证明：main() 启动时把 stdout 切到 UTF-8。
        import daily_diary
        from unittest.mock import patch

        class FakeStdout:
            def __init__(self):
                self.encoding = "cp936"
                self.buffer = []

            def reconfigure(self, encoding=None, errors=None):
                self.encoding = encoding

            def write(self, text):
                if self.encoding != "utf-8" and any(ord(c) > 127 for c in text):
                    raise UnicodeEncodeError("cp936", text, 0, 1, "not encodable")
                self.buffer.append(text)
                return len(text)

            def flush(self):
                pass

        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "memories"
            fake = FakeStdout()
            with patch.object(sys, "stdout", fake):
                rc = daily_diary.main(
                    ["--root", str(root), "--subject", "user", "--date", DAY, "--synthesize"]
                )
            self.assertEqual(rc, 0)
            self.assertEqual(fake.encoding, "utf-8")
            self.assertIn("无日档，什么都不做", "".join(fake.buffer))

    def test_cli_output_helper_reconfigures_stderr(self):
        # cli_output 新增的 stderr guard：同样 best-effort、永不抛错。
        from unittest.mock import patch

        from cli_output import ensure_utf8_stderr

        class FakeStderr:
            def __init__(self):
                self.encoding = "cp936"
                self.reconfigured = None

            def reconfigure(self, encoding=None, errors=None):
                self.reconfigured = encoding
                self.encoding = encoding

        fake = FakeStderr()
        with patch.object(sys, "stderr", fake):
            self.assertTrue(ensure_utf8_stderr())
        self.assertEqual(fake.reconfigured, "utf-8")
        with patch.object(sys, "stderr", None):
            self.assertFalse(ensure_utf8_stderr())


if __name__ == "__main__":
    unittest.main()
