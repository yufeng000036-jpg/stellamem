"""4K 桌面页测试（compiler/desktop_page.py + 夜间结算桌面字节数）。

只用隔离临时目录与虚构数据（全部以“虚构”开头），不碰生产数据。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "compiler"))

from desktop_page import (  # noqa: E402
    MAX_BYTES,
    desktop_path,
    desktop_status,
    desktop_status_text,
    list_backups,
    parse_sections,
    read_desktop,
    render_desktop,
    rewrite_desktop,
)

PEOPLE = ["虚构：宇枫是主人"]
COVENANT = ["虚构：记忆不出本地"]
EVENTS = ["虚构：正在补评审缺口"]


def page(people=PEOPLE, covenant=COVENANT, events=EVENTS, subject="user"):
    return render_desktop(subject, people, covenant, events)


class RenderTests(unittest.TestCase):
    def test_structure(self):
        text = page()
        self.assertIn("<!-- MEM:START -->", text)
        self.assertIn("<!-- MEM:END -->", text)
        self.assertIn("## 人和约", text)
        self.assertIn("## 事", text)
        sections = parse_sections(text)
        self.assertEqual(sections["covenant"], ["- 虚构：宇枫是主人", "- 虚构：记忆不出本地"])
        self.assertEqual(sections["events"], ["- 虚构：正在补评审缺口"])

    def test_alias_subject_normalized(self):
        # 旧拼写“用户”归进 user，文件不另起一张。
        text = render_desktop("用户", PEOPLE, COVENANT, EVENTS)
        self.assertIn("# 桌面页（user）", text)

    def test_render_over_budget_refused(self):
        with self.assertRaises(ValueError):
            render_desktop("user", PEOPLE, COVENANT, ["虚构事件" * 500])

    def test_parse_missing_markers_refused(self):
        with self.assertRaises(ValueError):
            parse_sections("# 桌面页\n## 人和约\n- x\n")

    def test_parse_missing_section_refused(self):
        with self.assertRaises(ValueError):
            parse_sections("<!-- MEM:START -->\n## 人和约\n- x\n<!-- MEM:END -->\n")


class RewriteTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name) / "memories"
        self.root.mkdir(parents=True)

    def tearDown(self):
        self._td.cleanup()

    def test_first_write_no_backup(self):
        receipt = rewrite_desktop(self.root, "user", page(), "虚构：初版")
        self.assertEqual(receipt["bytes"], len(page().encode("utf-8")))
        self.assertEqual(receipt["previous_bytes"], 0)
        self.assertIsNone(receipt["backed_up"])
        self.assertIn("+", receipt["diff"])
        self.assertEqual(read_desktop(self.root, "user"), page())

    def test_second_write_backs_up_old_version(self):
        rewrite_desktop(self.root, "user", page(), "虚构：初版")
        new = page(events=EVENTS + ["虚构：又多一件事"])
        receipt = rewrite_desktop(self.root, "user", new, "虚构：加一件事")
        self.assertIsNotNone(receipt["backed_up"])
        self.assertEqual(Path(receipt["backed_up"]).read_text(encoding="utf-8"), page())
        self.assertIn("+- 虚构：又多一件事", receipt["diff"])
        self.assertEqual(read_desktop(self.root, "user"), new)

    def test_over_budget_refused_and_file_unchanged(self):
        rewrite_desktop(self.root, "user", page(), "虚构：初版")
        # 直接拼超大文本（不走 render_desktop，它自己也会拦）。
        big = page().replace("<!-- MEM:END -->", "- " + "虚构事件" * 1000 + "\n\n<!-- MEM:END -->")
        self.assertGreater(len(big.encode("utf-8")), MAX_BYTES)
        with self.assertRaises(ValueError):
            rewrite_desktop(self.root, "user", big, "虚构：想塞太多")
        self.assertEqual(read_desktop(self.root, "user"), page())

    def test_covenant_deletion_refused_without_approval(self):
        rewrite_desktop(self.root, "user", page(), "虚构：初版")
        shrunk = page(covenant=[])
        with self.assertRaises(ValueError) as ctx:
            rewrite_desktop(self.root, "user", shrunk, "虚构：想删约")
        self.assertIn("人和约", str(ctx.exception))
        self.assertEqual(read_desktop(self.root, "user"), page())

    def test_covenant_deletion_allowed_with_explicit_flag(self):
        rewrite_desktop(self.root, "user", page(), "虚构：初版")
        shrunk = page(covenant=[])
        receipt = rewrite_desktop(
            self.root, "user", shrunk, "虚构：用户已批删约", allow_deletions=True
        )
        self.assertEqual(read_desktop(self.root, "user"), shrunk)
        self.assertIn("-- 虚构：记忆不出本地", receipt["diff"])

    def test_events_deletion_needs_no_approval(self):
        # “事”一节的行可以正常改写，不用批。
        rewrite_desktop(self.root, "user", page(), "虚构：初版")
        receipt = rewrite_desktop(self.root, "user", page(events=[]), "虚构：事做完了")
        self.assertEqual(read_desktop(self.root, "user"), page(events=[]))
        self.assertIn("-- 虚构：正在补评审缺口", receipt["diff"])

    def test_missing_reason_refused(self):
        with self.assertRaises(ValueError):
            rewrite_desktop(self.root, "user", page(), "  ")

    def test_missing_markers_refused(self):
        with self.assertRaises(ValueError):
            rewrite_desktop(self.root, "user", "# 桌面页\n", "虚构：格式不对")

    def test_invalid_subject_refused(self):
        with self.assertRaises(ValueError):
            rewrite_desktop(self.root, "bogus", page(), "虚构：主体不对")

    def test_backup_rotation_keeps_14(self):
        rewrite_desktop(self.root, "user", page(), "虚构：初版")
        for i in range(16):
            rewrite_desktop(self.root, "user", page(events=[f"虚构事件{i}"]), f"虚构：第{i}次改")
        backups = list_backups(self.root, "user")
        self.assertEqual(len(backups), 14)
        # 最新一份备份是倒数第二次的内容（每次覆盖前备份旧版）。
        self.assertIn("虚构事件14", backups[0].read_text(encoding="utf-8"))

    def test_subjects_are_separate_files(self):
        rewrite_desktop(self.root, "user", page(), "虚构：个人页")
        persona = render_desktop("persona", ["虚构：鲸鱼娘是主播"], [], ["虚构：今晚直播"])
        rewrite_desktop(self.root, "persona", persona, "虚构：鲸鱼页")
        self.assertEqual(read_desktop(self.root, "user"), page())
        self.assertEqual(read_desktop(self.root, "persona"), persona)
        self.assertNotEqual(desktop_path(self.root, "user"), desktop_path(self.root, "persona"))
        status = desktop_status(self.root)
        self.assertIsNotNone(status["user"])
        self.assertIsNotNone(status["persona"])
        self.assertIsNone(status["viewer"])

    def test_concurrent_rewrites_leave_complete_file(self):
        rewrite_desktop(self.root, "user", page(), "虚构：初版")
        contents = [page(events=[f"虚构并发事件{i}"]) for i in range(8)]
        errors = []

        def _write(text, i):
            try:
                rewrite_desktop(self.root, "user", text, f"虚构：并发{i}")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [
            threading.Thread(target=_write, args=(text, i)) for i, text in enumerate(contents)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        final = read_desktop(self.root, "user")
        # 最终文件必须是某一次的完整写入，不许是拼起来的半截。
        self.assertIn(final, contents)
        self.assertLessEqual(len(final.encode("utf-8")), MAX_BYTES)
        self.assertLessEqual(len(list_backups(self.root, "user")), 14)
        # 没有残留的 tmp 暂存文件。
        staging = list((self.root / "system" / "desktop").glob("*.tmp-*"))
        staging += list((self.root / "system" / "desktop").glob(".*.tmp-*"))
        self.assertEqual(staging, [])

    def test_status_text(self):
        self.assertEqual(desktop_status_text(self.root), "未启用")
        rewrite_desktop(self.root, "user", page(), "虚构：初版")
        size = len(page().encode("utf-8"))
        self.assertEqual(desktop_status_text(self.root), f"user {size} 字节")


class SettlementDesktopTests(unittest.TestCase):
    def test_report_shows_real_desktop_bytes(self):
        from nightly_settlement import build_report

        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "memories"
            root.mkdir(parents=True)
            rewrite_desktop(root, "user", page(), "虚构：初版")
            persona = render_desktop("persona", ["虚构：鲸鱼娘"], [], ["虚构：直播"])
            rewrite_desktop(root, "persona", persona, "虚构：鲸鱼页")
            report = build_report(root)
            self.assertIn(
                f"桌面：user {len(page().encode('utf-8'))} 字节；"
                f"persona {len(persona.encode('utf-8'))} 字节",
                report["text"],
            )


class CLITests(unittest.TestCase):
    def _run(self, *args):
        # 模拟 Windows 控制台：PYTHONIOENCODING=cp1252、PYTHONUTF8=0。
        # 子进程必须自己把 stdout/stderr 切到 UTF-8（main() 里做），
        # 否则印中文就炸——这就是 CI 上 Windows 两档红的原因。
        # 父进程侧按 UTF-8 严格解码，不靠 locale 猜。
        proc = subprocess.run(
            [sys.executable, str(REPO / "compiler" / "desktop_page.py"), *args],
            capture_output=True,
            env=dict(os.environ, PYTHONIOENCODING="cp1252", PYTHONUTF8="0"),
            timeout=30,
        )
        return SimpleNamespace(
            returncode=proc.returncode,
            stdout=proc.stdout.decode("utf-8", errors="strict"),
            stderr=proc.stderr.decode("utf-8", errors="strict"),
        )

    def test_main_reconfigures_stdout_to_utf8(self):
        # 进程内证明：main() 启动时把 stdout 切到 UTF-8。
        # FakeStdout 模拟 Windows 控制台：切之前印中文就炸。
        import desktop_page
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
                rc = desktop_page.main(["--root", str(root), "--subject", "user", "--status"])
            self.assertEqual(rc, 0)
            self.assertEqual(fake.encoding, "utf-8")
            self.assertIn("未启用", "".join(fake.buffer))

    def test_cli_status_show_rewrite(self):
        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memories")
            r = self._run("--root", root, "--subject", "user", "--status")
            self.assertEqual(r.returncode, 0)
            self.assertIn("未启用", r.stdout)
            new_file = Path(td) / "new.md"
            new_file.write_text(page(), encoding="utf-8")
            r = self._run(
                "--root",
                root,
                "--subject",
                "user",
                "--rewrite-file",
                str(new_file),
                "--reason",
                "虚构：CLI 改写",
            )
            self.assertEqual(r.returncode, 0)
            self.assertIn("桌面页已改写", r.stdout)
            self.assertIn("改动：", r.stdout)
            r = self._run("--root", root, "--subject", "user", "--show")
            self.assertEqual(r.returncode, 0)
            self.assertIn("## 人和约", r.stdout)

    def test_cli_over_budget_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memories")
            big_file = Path(td) / "big.md"
            big_file.write_text(
                "<!-- MEM:START -->\n## 人和约\n- x\n\n## 事\n"
                + "- 虚构事件" * 1000
                + "\n\n<!-- MEM:END -->\n",
                encoding="utf-8",
            )
            r = self._run(
                "--root",
                root,
                "--subject",
                "user",
                "--rewrite-file",
                str(big_file),
                "--reason",
                "虚构：想塞太多",
            )
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("超过", r.stderr)


if __name__ == "__main__":
    unittest.main()
