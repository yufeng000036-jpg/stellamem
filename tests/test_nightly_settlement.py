"""夜间结算报数：只用隔离临时目录 + 虚构数据，绝不用生产数据。"""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

import indexer
import nightly_settlement
from storage import render_records

UTC = timezone.utc


def make_record(claim_id, kind, subject, status="active", value="虚构内容"):
    record = {
        "schema_version": 2,
        "claim_id": claim_id,
        "revision_id": f"r-{claim_id}",
        "event_id": f"e-{claim_id}",
        "kind": kind,
        "scope": "global",
        "predicate": "p",
        "value": value,
        "evidence": value,
        "source": "user",
        "confidence": 0.9,
        "status": status,
        "relation": "NEW",
        "observed_at": "2026-11-01T00:00:00+00:00",
        "provenance": {"timestamp": "2026-11-01T09:00:00", "record_id": "rm-x"},
    }
    if subject is not None:
        record["subject"] = subject
    return record


# kind → MANAGED 文件（与 claim_normalizer.ROUTE 一致）
ROUTE = {
    "preference": "core/preferences.md",
    "decision": "long-term/decisions.md",
    "lesson": "long-term/lessons.md",
    "procedure": "long-term/lessons.md",
    "project": "long-term/projects.md",
    "fact": "semantic/facts.md",
    "relationship": "semantic/profile.md",
    "identity": "core/user-core.md",
}


def seed_root(root: Path, records):
    by_file = {}
    for record in records:
        # 待审记录真实存在 system/memory-inbox.md 里，不在 kind 文件里。
        if record.get("status") == "pending":
            by_file.setdefault("system/memory-inbox.md", []).append(record)
        else:
            by_file.setdefault(ROUTE[record["kind"]], []).append(record)
    for rel, recs in by_file.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_records("# 测试\n", recs), encoding="utf-8")


def append_record(root: Path, record):
    """往 kind 对应的 MANAGED 文件尾追加一条记录（不重写已有内容）。"""
    path = root / ROUTE[record["kind"]]
    path.parent.mkdir(parents=True, exist_ok=True)
    block = "```stellamem\n" + json.dumps(record, ensure_ascii=False) + "\n```\n"
    if path.exists():
        text = path.read_text(encoding="utf-8")
        if not text.endswith("\n"):
            text += "\n"
        path.write_text(text + block, encoding="utf-8")
    else:
        path.write_text(render_records("# 测试\n", [record]), encoding="utf-8")


def write_chain(root: Path, events):
    path = root / "system" / "version-chain.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")


def event(event_id, date):
    item = {"event_id": event_id, "id": "c-x", "relation": "NEW", "status": "active"}
    if date is not None:
        item["date"] = date
    return item


class SettlementCountsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "mem"
        self.root.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_counts_by_subject_and_kind(self):
        records = [
            make_record("u-pref", "preference", "user"),
            make_record("u-pref-alias", "preference", "用户"),
            make_record("u-lesson", "lesson", "user"),
            make_record("u-decision", "decision", "user"),
            make_record("u-project", "project", "user"),
            make_record("u-fact", "fact", "user"),
            make_record("u-proc", "procedure", "user"),  # 其他
            make_record("u-ident", "identity", "user"),  # 其他（user-core.md）
            make_record("u-pending", "fact", "user", status="pending"),
            make_record("u-old", "fact", "user", status="superseded"),
            make_record("p-fact", "fact", "persona"),
            make_record("p-pref", "preference", "persona"),
            make_record("v-pref", "preference", "viewer"),
            make_record("x-fact", "fact", "某个话题"),  # 未分类主体
            make_record("n-subj", "fact", None),  # 无 subject 字段
        ]
        seed_root(self.root, records)
        counts = nightly_settlement.collect_counts(self.root)
        user = counts["user"]
        self.assertEqual(user["preference"], 2)  # 别名“用户”归入 user
        self.assertEqual(user["lesson"], 1)
        self.assertEqual(user["decision"], 1)
        self.assertEqual(user["project"], 1)
        self.assertEqual(user["fact"], 1)
        self.assertEqual(user["other"], 2)  # procedure + identity
        self.assertEqual(user["total"], 8)
        self.assertEqual(counts["persona"], 2)  # 只报总数
        self.assertEqual(counts["viewer"], 1)
        self.assertEqual(counts["unclassified"], 2)
        self.assertEqual(counts["pending"], 1)
        self.assertEqual(counts["history"], 1)
        self.assertEqual(counts["total_active"], 13)

    def test_non_managed_files_are_ignored(self):
        """非 MANAGED 文件里的记录不计数（防假阳性：写错地方就该是 0）。"""
        seed_root(self.root, [make_record("u-pref", "preference", "user")])
        stray = self.root / "persona.md"
        stray.write_text(
            render_records("# 旁路\n", [make_record("stray", "fact", "persona")]),
            encoding="utf-8",
        )
        counts = nightly_settlement.collect_counts(self.root)
        self.assertEqual(counts["total_active"], 1)
        self.assertEqual(counts["persona"], 0)

    def test_empty_root_counts_zero(self):
        counts = nightly_settlement.collect_counts(self.root)
        self.assertEqual(counts["total_active"], 0)
        self.assertEqual(counts["pending"], 0)


class SettlementBaselineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "mem"
        self.root.mkdir()
        seed_root(self.root, [make_record("u-pref", "preference", "user")])
        self.now1 = datetime(2026, 11, 5, 23, 30, tzinfo=UTC)
        self.now2 = datetime(2026, 11, 6, 23, 30, tzinfo=UTC)

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_run_reports_no_baseline_and_creates_it(self):
        report = nightly_settlement.run_settlement(self.root, now=self.now1)
        self.assertIsNone(report["changed"])
        self.assertIn("首次结算、无上次基线", report["text"])
        self.assertIn("桌面：未启用", report["text"])
        self.assertEqual(report["desktop"], "未启用")
        log = Path(report["log_path"])
        self.assertTrue(log.exists())
        self.assertEqual(log.read_text(encoding="utf-8"), report["text"])
        baseline = json.loads(Path(report["baseline_path"]).read_text(encoding="utf-8"))
        self.assertEqual(baseline["last_settlement_at"], "2026-11-05T23:30:00+00:00")
        self.assertEqual(baseline["event_count"], 0)
        # 没有 tmp 残留
        out_dir = Path(report["baseline_path"]).parent
        self.assertEqual(list(out_dir.glob("*.tmp-*")), [])

    def test_second_run_counts_only_events_after_baseline(self):
        nightly_settlement.run_settlement(self.root, now=self.now1)
        write_chain(
            self.root,
            [
                event("e-before", "2026-11-05T23:00:00+00:00"),  # 基线之前，不数
                event("e-after-1", "2026-11-05T23:45:00+00:00"),
                event("e-after-2", "2026-11-06T10:00:00+00:00"),
                event("e-undated", None),  # 无日期，不数，另计
            ],
        )
        report = nightly_settlement.run_settlement(self.root, now=self.now2)
        self.assertEqual(report["changed"], 2)
        self.assertEqual(report["total_events"], 4)
        self.assertEqual(report["undated_events"], 1)
        self.assertIn("今晚动了：2 条", report["text"])
        self.assertIn("另有 1 个事件无有效日期，未计入", report["text"])

    def test_missing_chain_means_zero_events(self):
        nightly_settlement.run_settlement(self.root, now=self.now1)
        report = nightly_settlement.run_settlement(self.root, now=self.now2)
        self.assertEqual(report["changed"], 0)
        self.assertEqual(report["total_events"], 0)

    def test_corrupt_baseline_fails_without_writing(self):
        first = nightly_settlement.run_settlement(self.root, now=self.now1)
        log_path = Path(first["log_path"])
        before = log_path.read_text(encoding="utf-8")
        Path(first["baseline_path"]).write_text("{不是 json", encoding="utf-8")
        with self.assertRaises(ValueError):
            nightly_settlement.run_settlement(self.root, now=self.now2)
        # 失败那次没写新日志、没动旧日志
        self.assertEqual(log_path.read_text(encoding="utf-8"), before)

    def test_corrupt_chain_fails(self):
        chain = self.root / "system" / "version-chain.json"
        chain.parent.mkdir(parents=True, exist_ok=True)
        chain.write_text("[1, 2", encoding="utf-8")
        with self.assertRaises(ValueError):
            nightly_settlement.run_settlement(self.root, now=self.now1)

    def test_report_is_deterministic(self):
        report1 = nightly_settlement.build_report(self.root, now=self.now1)
        report2 = nightly_settlement.build_report(self.root, now=self.now1)
        self.assertEqual(report1["text"], report2["text"])

    def test_last_write_time_uses_newest_managed_or_chain(self):
        fixed = datetime(2025, 11, 5, tzinfo=UTC).timestamp()
        nightly_settlement.run_settlement(self.root, now=self.now1)
        # 基线与日志文件在 system/nightly-settlement/ 下，不影响最后写入时间；
        # 把全部候选文件都定到同一时刻，断言取到它。
        for rel, _ in indexer.MANAGED:
            path = self.root / rel
            if path.exists():
                os.utime(path, (fixed, fixed))
        chain = self.root / "system" / "version-chain.json"
        chain.parent.mkdir(parents=True, exist_ok=True)
        chain.write_text("[]", encoding="utf-8")
        os.utime(chain, (fixed, fixed))
        report = nightly_settlement.build_report(self.root, now=self.now1)
        self.assertEqual(report["last_write"], "2025-11-05T00:00:00+00:00")


class SettlementIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "mem"
        self.root.mkdir()
        seed_root(
            self.root,
            [
                make_record("u-pref", "preference", "user"),
                make_record("u-fact", "fact", "user"),
                make_record("p-fact", "fact", "persona"),
            ],
        )
        self.db = Path(self.tmp.name) / "index.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_db_means_unknown(self):
        report = nightly_settlement.build_report(
            self.root, now=datetime(2026, 11, 5, 23, 30, tzinfo=UTC)
        )
        self.assertFalse(report["index_available"])
        self.assertIn("未知（未指定索引库）", report["text"])

    def test_missing_db_file(self):
        status = nightly_settlement.index_status(self.root, self.db)
        self.assertFalse(status["available"])
        self.assertEqual(status["reason"], "索引不存在")

    def test_fresh_index_reports_success_and_rows(self):
        idx = indexer.Index(self.db)
        try:
            meta = idx.build(self.root)
        finally:
            idx.close()
        self.assertEqual(meta["records"], 3)
        report = nightly_settlement.build_report(
            self.root, db_path=self.db, now=datetime(2026, 11, 5, 23, 30, tzinfo=UTC)
        )
        self.assertTrue(report["index_available"])
        self.assertEqual(report["index_records"], 3)
        self.assertIn("成功（索引新鲜", report["text"])
        self.assertIn("索引行数：3 条记录", report["text"])

    def test_stale_index_after_content_change(self):
        idx = indexer.Index(self.db)
        try:
            idx.build(self.root)
        finally:
            idx.close()
        # 改内容：追加一条记录 → 索引过期
        target = self.root / "semantic" / "facts.md"
        original = target.read_text(encoding="utf-8")
        blocks = [
            "```stellamem",
            json.dumps(make_record("u-new", "fact", "user"), ensure_ascii=False),
            "```",
            "",
        ]
        target.write_text(original.rstrip() + "\n\n" + "\n".join(blocks), encoding="utf-8")
        status = nightly_settlement.index_status(self.root, self.db)
        self.assertTrue(status["available"])
        self.assertFalse(status["fresh"])
        self.assertIn(
            "待重建（索引过期", nightly_settlement.rebuild_status(root=self.root, db_path=self.db)
        )

    def test_touch_only_does_not_make_index_stale(self):
        idx = indexer.Index(self.db)
        try:
            idx.build(self.root)
        finally:
            idx.close()
        # 只 touch、不改内容：指纹变了、内容没变，仍算新鲜
        target = self.root / "semantic" / "facts.md"
        target.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
        status = nightly_settlement.index_status(self.root, self.db)
        self.assertTrue(status["fresh"])

    def test_service_rebuild_error_is_reported(self):
        import api

        svc = api.MemoryService(self.root, self.db)
        try:
            svc._rebuild_error = RuntimeError("boom")
            self.assertTrue(nightly_settlement.rebuild_status(service=svc).startswith("失败：boom"))
            svc._rebuild_error = None
            svc.rebuilt_at = "2026-11-05T10:00:00+00:00"
            self.assertEqual(
                nightly_settlement.rebuild_status(service=svc),
                "成功（构建于 2026-11-05T10:00:00+00:00）",
            )
            svc.rebuilt_at = None
            self.assertEqual(nightly_settlement.rebuild_status(service=svc), "本进程内尚未重建")
        finally:
            svc.index.close()


class SettlementCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "mem"
        self.root.mkdir()
        seed_root(self.root, [make_record("u-pref", "preference", "user")])

    def tearDown(self):
        self.tmp.cleanup()

    def test_cli_prints_report_and_writes_files(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = nightly_settlement.main(["--root", str(self.root)])
        self.assertEqual(code, 0)
        self.assertIn("夜间结算", buf.getvalue())
        self.assertIn("桌面：未启用", buf.getvalue())
        out_dir = self.root / "system" / "nightly-settlement"
        self.assertTrue((out_dir / "baseline.json").exists())
        self.assertEqual(len(list(out_dir.glob("settlement-*.txt"))), 1)

    def test_cli_second_run_reports_changed(self):
        nightly_settlement.main(["--root", str(self.root)])
        write_chain(self.root, [event("e-1", "2030-01-01T00:00:00+00:00")])
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = nightly_settlement.main(["--root", str(self.root)])
        self.assertEqual(code, 0)
        self.assertIn("今晚动了：1 条", buf.getvalue())

    def test_cli_prints_report_under_cp1252_stdout(self):
        """Windows 控制台默认编码（cp1252）下结算报告的中文也不能炸。

        main() 入口先把 stdout/stderr 重配成 UTF-8（compiler/cli_output，
        best-effort）。直接把 sys.stdout 换成 cp1252 编码的
        TextIOWrapper 复现：修之前 print 报告就报 UnicodeEncodeError；
        修之后写出 UTF-8 字节，解出来仍是中文报告。
        """
        raw = io.BytesIO()
        wrapper = io.TextIOWrapper(raw, encoding="cp1252")
        old_stdout = sys.stdout
        try:
            sys.stdout = wrapper
            code = nightly_settlement.main(["--root", str(self.root)])
        finally:
            sys.stdout = old_stdout
            wrapper.flush()
        self.assertEqual(code, 0)
        self.assertIn("夜间结算", raw.getvalue().decode("utf-8"))

    def test_cli_missing_root_returns_2(self):
        code = nightly_settlement.main(["--root", str(self.root / "nope")])
        self.assertEqual(code, 2)


class LedgerArmTests(unittest.TestCase):
    """账本必红臂：漏账必红、正常不误报；臂不通则结论不对外。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "mem"
        self.root.mkdir()
        seed_root(
            self.root,
            [
                make_record("u-pref", "preference", "user"),
                make_record("u-fact", "fact", "user"),
                make_record("p-fact", "fact", "persona"),
                make_record("u-pending", "fact", "user", status="pending"),
                make_record("u-old", "fact", "user", status="superseded"),
            ],
        )
        self.now = datetime(2026, 11, 5, 23, 30, tzinfo=UTC)

    def tearDown(self):
        self.tmp.cleanup()

    def test_arm_passes_on_normal_fixture(self):
        report = nightly_settlement.build_report(self.root, now=self.now)
        self.assertEqual(
            report["ledger_arm"],
            {"active": 3, "pending": 1, "history": 1, "total": 5},
        )
        self.assertIn("账本自检：通过（漏账必红臂", report["text"])

    def test_arm_goes_red_when_canary_record_is_dropped(self):
        """哨兵记录应在账、故意不让它进账：臂必须报红，且不写日志、不建基线。"""
        append_record(self.root, make_record("u-canary", "fact", "user"))
        real_collect = nightly_settlement.collect_counts

        def dropping_collect(root):
            counts = real_collect(root)
            counts["total_active"] -= 1  # 哨兵那条被结算计数漏掉
            counts["user"]["total"] -= 1
            counts["user"]["fact"] -= 1
            return counts

        out_dir = self.root / "system" / "nightly-settlement"
        with mock.patch.object(nightly_settlement, "collect_counts", dropping_collect):
            with self.assertRaisesRegex(ValueError, "漏账必红臂"):
                nightly_settlement.run_settlement(self.root, now=self.now)
        # 臂不通则结论不对外：没有当日日志、没有基线
        self.assertEqual(list(out_dir.glob("settlement-*.txt")), [])
        self.assertFalse((out_dir / "baseline.json").exists())

    def test_arm_goes_red_when_record_added_after_counting(self):
        """不靠 monkeypatch 的漏账：计数算完后又多一条记录，臂必须报红。"""
        counts = nightly_settlement.collect_counts(self.root)
        append_record(self.root, make_record("u-late", "fact", "user"))
        with self.assertRaisesRegex(ValueError, "漏账必红臂"):
            nightly_settlement.verify_ledger_arm(self.root, counts)

    def test_verify_arm_accepts_exact_counts(self):
        counts = nightly_settlement.collect_counts(self.root)
        raw = nightly_settlement.verify_ledger_arm(self.root, counts)
        self.assertEqual(raw["total"], 5)
        tampered = dict(counts)
        tampered["pending"] = 0  # 篡改任一桶都必须报红
        with self.assertRaisesRegex(ValueError, "pending：结算数 0 ≠ 原文重数 1"):
            nightly_settlement.verify_ledger_arm(self.root, tampered)

    def test_arm_failure_via_cli_returns_1_and_writes_nothing(self):
        real_collect = nightly_settlement.collect_counts

        def dropping_collect(root):
            counts = real_collect(root)
            counts["total_active"] -= 1
            return counts

        with mock.patch.object(nightly_settlement, "collect_counts", dropping_collect):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = nightly_settlement.main(["--root", str(self.root)])
        self.assertEqual(code, 1)
        out_dir = self.root / "system" / "nightly-settlement"
        self.assertEqual(list(out_dir.glob("settlement-*.txt")), [])
        self.assertFalse((out_dir / "baseline.json").exists())


if __name__ == "__main__":
    unittest.main()
