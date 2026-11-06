"""Hit 投票测试（compiler/hit_votes.py + recall() 接线 + MCP/HTTP/结算）。

只用隔离临时目录与虚构数据（全部以“虚构”开头），不碰生产数据。
语义通道在测试里强制离线（STELLAMEM_SEMANTIC=0），与生产现状一致。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "compiler"))

from hit_votes import HitVotes, validate_vote  # noqa: E402
from indexer import Index  # noqa: E402
from search import recall  # noqa: E402
from storage import render_records  # noqa: E402


def make_record(claim_id, kind, subject, value, observed="2026-11-05T09:00:00"):
    return {
        "schema_version": 2,
        "claim_id": claim_id,
        "revision_id": f"r-{claim_id}",
        "event_id": f"e-{claim_id}",
        "kind": kind,
        "scope": "global",
        "subject": subject,
        "predicate": "p",
        "value": value,
        "evidence": value,
        "source": "user",
        "confidence": 0.9,
        "importance": 0.5,
        "sensitivity": "PRIVATE",
        "write_policy": "allow",
        "injection_policy": "recall",
        "evidence_verified": True,
        "status": "active",
        "relation": "NEW",
        "auto_write": True,
        "observed_at": observed,
        "provenance": {"timestamp": observed, "record_id": "rm-x"},
    }


def seed(mem_root: Path) -> None:
    rows = [
        # 两条同分同长的 user 事实：没投票时顺序由候选池定，投票后应翻转。
        make_record("c-vote-a", "fact", "user", "虚构：冰滴咖啡很好喝"),
        make_record("c-vote-b", "fact", "user", "虚构：冰滴咖啡有点苦"),
        # 与查询零重叠的 user 记录：投了票也不得进召回。
        make_record("c-vote-unrelated", "fact", "user", "虚构：今天天气晴朗"),
        make_record("c-vote-persona", "fact", "persona", "虚构：鲸鱼娘最爱桂花乌龙"),
        # 意图兜底用的 project 记录（意图问题零词面命中时兜底拿它）。
        make_record("c-vote-proj", "project", "user", "虚构：深海灯塔电台"),
    ]
    target = mem_root / "long-term" / "projects.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_records("# 项目\n", rows), encoding="utf-8")


class HitVotesStoreTests(unittest.TestCase):
    def test_record_hit_and_get_per_subject(self):
        hv = HitVotes()
        snap = hv.record_hit("user", "c-a:r-c-a", kind="fact")
        self.assertEqual(snap["hits"], 1)
        self.assertEqual(snap["manual"], 0)
        self.assertEqual(snap["subject"], "user")
        # 同一记录 id 在另一主体下是另一条计数，绝不串。
        hv.record_hit("persona", "c-a:r-c-a", kind="fact")
        self.assertEqual(hv.get("user", "c-a:r-c-a")["hits"], 1)
        self.assertEqual(hv.get("persona", "c-a:r-c-a")["hits"], 1)
        self.assertIsNone(hv.get("viewer", "c-a:r-c-a"))

    def test_vote_is_manual_only(self):
        hv = HitVotes()
        snap = hv.vote("user", "c-a:r-c-a", kind="fact")
        self.assertEqual(snap["hits"], 0)
        self.assertEqual(snap["manual"], 1)

    def test_boost_cap_and_manual_half_weight(self):
        hv = HitVotes()
        self.assertEqual(hv.boost("user", "c-x"), 0.0)
        hv.record_hit("user", "c-x")
        self.assertEqual(hv.boost("user", "c-x"), 0.02)
        # 手动票半权：2 张手动 = 1 个有效命中。
        hv2 = HitVotes()
        hv2.vote("user", "c-y")
        hv2.vote("user", "c-y")
        self.assertEqual(hv2.boost("user", "c-y"), 0.02)
        # 封顶 0.10：10 次自动命中也不超过。
        hv3 = HitVotes()
        for _ in range(20):
            hv3.record_hit("user", "c-z")
        self.assertEqual(hv3.boost("user", "c-z"), 0.10)

    def test_boost_never_raises(self):
        hv = HitVotes()
        self.assertEqual(hv.boost("bogus-subject", "c-x"), 0.0)
        self.assertEqual(hv.boost("user", ""), 0.0)

    def test_record_hit_invalid_subject_raises(self):
        hv = HitVotes()
        with self.assertRaises(ValueError):
            hv.record_hit("bogus-subject", "c-x")

    def test_persistence_roundtrip(self):
        tmp = Path(tempfile.mkdtemp(prefix="stellamem-hitvotes-"))
        p = tmp / "votes.json"
        hv = HitVotes(path=p)
        hv.record_hit("user", "c-a:r-c-a", kind="fact")
        hv.vote("user", "c-a:r-c-a")
        hv2 = HitVotes(path=p)
        snap = hv2.get("user", "c-a:r-c-a")
        self.assertEqual(snap["hits"], 1)
        self.assertEqual(snap["manual"], 1)
        self.assertEqual(snap["kind"], "fact")

    def test_corrupt_file_non_strict_starts_empty(self):
        tmp = Path(tempfile.mkdtemp(prefix="stellamem-hitvotes-"))
        p = tmp / "votes.json"
        p.write_text("not json{{{", encoding="utf-8")
        hv = HitVotes(path=p)
        self.assertEqual(hv.report(), [])

    def test_corrupt_file_strict_raises(self):
        tmp = Path(tempfile.mkdtemp(prefix="stellamem-hitvotes-"))
        p = tmp / "votes.json"
        p.write_text("not json{{{", encoding="utf-8")
        with self.assertRaises(ValueError):
            HitVotes(path=p, strict=True)

    def test_no_delete_api(self):
        # 只增信号：模块不提供任何删除 / 清理方法
        hv = HitVotes()
        hv.record_hit("user", "c-a")
        self.assertFalse(hasattr(hv, "delete"))
        self.assertFalse(hasattr(hv, "clear"))
        self.assertFalse(hasattr(hv, "reset"))

    def test_report_has_no_content(self):
        hv = HitVotes()
        hv.record_hit("user", "c-a:r-c-a", kind="fact")
        rows = hv.report()
        self.assertEqual(len(rows), 1)
        self.assertNotIn("value", rows[0])
        self.assertEqual(rows[0]["item_id"], "record:user:c-a:r-c-a")

    def test_daily_summary_and_summary(self):
        hv = HitVotes()
        hv.record_hit("user", "c-a:r-c-a", kind="fact")
        hv.record_hit("user", "c-a:r-c-a", kind="fact")
        hv.vote("user", "c-a:r-c-a", kind="fact")
        hv.record_hit("persona", "c-b:r-c-b", kind="fact")
        today = datetime.now().astimezone().date().isoformat()
        daily = hv.daily_summary(today)
        self.assertEqual(daily["user"], {"auto": 2, "manual": 1})
        self.assertEqual(daily["persona"], {"auto": 1, "manual": 0})
        summary = hv.summary(today)
        self.assertEqual(summary["total"]["user"], {"auto": 2, "manual": 1})
        self.assertEqual(summary["total"]["persona"], {"auto": 1, "manual": 0})
        self.assertEqual(summary["top_kinds"][0]["subject"], "user")
        self.assertEqual(summary["top_kinds"][0]["kind"], "fact")
        self.assertEqual(summary["top_kinds"][0]["hits"], 3)
        self.assertNotIn("record_id", summary["top_kinds"][0])


class ValidateVoteTests(unittest.TestCase):
    def test_same_subject_ok(self):
        self.assertTrue(validate_vote("user", "user"))
        # 旧拼写“用户”与“user”是同一命名空间。
        self.assertTrue(validate_vote("user", "用户"))
        self.assertTrue(validate_vote("用户", "user"))

    def test_cross_subject_refused(self):
        self.assertFalse(validate_vote("user", "persona"))
        self.assertFalse(validate_vote("persona", "user"))

    def test_unclassified_refused(self):
        self.assertFalse(validate_vote("user", "钟丽潭"))
        self.assertFalse(validate_vote("bogus", "user"))


class RecallIntegrationTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        base = Path(self._td.name)
        self.mem_root = base / "memories"
        seed(self.mem_root)
        self.db_path = base / "index.db"
        idx = Index(self.db_path)
        try:
            idx.build(self.mem_root)
        finally:
            idx.close()
        self.con = sqlite3.connect(str(self.db_path))
        self.con.row_factory = sqlite3.Row
        self._env = mock.patch.dict(os.environ, {"STELLAMEM_SEMANTIC": "0"})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self.con.close()
        self._td.cleanup()

    def _record_id(self, claim_id):
        row = self.con.execute("SELECT id FROM records WHERE claim_id = ?", (claim_id,)).fetchone()
        return row["id"]

    def test_voted_record_rises_within_topk(self):
        hv = HitVotes()
        first = recall(self.con, "冰滴咖啡", subject="user", hit_votes=hv)
        ids_before = [it["id"] for it in first["items"]]
        self.assertEqual(len(ids_before), 2)
        second_id = ids_before[1]
        # 第一次召回已给两条各记 1 次自动命中；再给第二条 5 张手动票：
        # 有效命中 = 1 + 0.5×5 = 3.5，加分 0.07，对面只有 0.02，应翻转。
        for _ in range(5):
            hv.vote("user", second_id, kind="fact")
        second = recall(self.con, "冰滴咖啡", subject="user", hit_votes=hv)
        ids_after = [it["id"] for it in second["items"]]
        self.assertEqual(ids_after[0], second_id)
        self.assertEqual(second["items"][0].get("vote_boost"), 0.07)

    def test_votes_do_not_admit_below_threshold(self):
        # 零重叠的记录投 10 票也不得进召回：投票只排序，不改变准入。
        hv = HitVotes()
        unrelated_id = self._record_id("c-vote-unrelated")
        for _ in range(10):
            hv.vote("user", unrelated_id, kind="fact")
        result = recall(self.con, "冰滴咖啡", subject="user", hit_votes=hv)
        ids = [it["id"] for it in result["items"]]
        self.assertNotIn(unrelated_id, ids)

    def test_votes_do_not_cross_subjects(self):
        # persona 的票抬不动 user 的记录，反之亦然。
        hv = HitVotes()
        persona_id = self._record_id("c-vote-persona")
        for _ in range(10):
            hv.vote("persona", persona_id, kind="fact")
        result = recall(self.con, "冰滴咖啡", subject="user", hit_votes=hv)
        self.assertEqual(len(result["items"]), 2)
        self.assertTrue(all(it.get("subject") == "user" for it in result["items"]))
        self.assertTrue(all(it.get("vote_boost", 0.0) == 0.0 for it in result["items"]))

    def test_empty_votes_leave_order_unchanged(self):
        # Recall@5 不下跌的底线：没有票时，加了 hit_votes 参数的召回
        # 与不加的结果完全一致。
        plain = recall(self.con, "冰滴咖啡", subject="user")
        voted = recall(self.con, "冰滴咖啡", subject="user", hit_votes=HitVotes())
        self.assertEqual([it["id"] for it in plain["items"]], [it["id"] for it in voted["items"]])
        self.assertEqual(
            [it["score"] for it in plain["items"]], [it["score"] for it in voted["items"]]
        )

    def test_auto_hit_only_for_injected_items(self):
        hv = HitVotes()
        first = recall(self.con, "冰滴咖啡", subject="user", hit_votes=hv)
        self.assertEqual(len(first["items"]), 2)
        for it in first["items"]:
            self.assertEqual(hv.get("user", it["id"])["hits"], 1)
        # 只放得下一行时，只记第一条的命中。
        first_line = first["text"].split("\n")[0]
        second = recall(
            self.con,
            "冰滴咖啡",
            subject="user",
            hit_votes=hv,
            budget_chars=len(first_line) + 1,
        )
        self.assertEqual(len(second["items"]), 1)
        first_id = first["items"][0]["id"]
        second_id = first["items"][1]["id"]
        self.assertEqual(hv.get("user", first_id)["hits"], 2)
        self.assertEqual(hv.get("user", second_id)["hits"], 1)

    def test_fallback_record_can_be_hit(self):
        # 意图兜底进来的记录被注入后也记命中。
        hv = HitVotes()
        q = "请问那个播客项目现在做到哪一步了"
        result = recall(self.con, q, subject="user", hit_votes=hv)
        self.assertEqual(len(result["items"]), 1)
        item = result["items"][0]
        self.assertTrue(item.get("fallback"))
        self.assertEqual(hv.get("user", item["id"])["hits"], 1)

    def test_extras_still_counted_by_usage_tracker_not_hit_votes(self):
        from usage_signals import UsageTracker

        hv = HitVotes()
        tracker = UsageTracker()
        cards = [
            {
                "subject": "user",
                "text": "虚构偏好：只喝冰滴咖啡",
                "evidence": ["e1"],
                "status": "active",
            }
        ]
        result = recall(
            self.con,
            "冰滴咖啡",
            subject="user",
            hit_votes=hv,
            usage_tracker=tracker,
            preference_cards=cards,
            pref_subject="user",
        )
        self.assertTrue(any(it.get("kind") == "preference_summary" for it in result["items"]))
        self.assertEqual(tracker.get("pref:user")["hits"], 1)
        # HitVotes 里只有 record: 开头的 key，没有偏好卡的 key。
        self.assertTrue(all(r["item_id"].startswith("record:") for r in hv.report()))


class MCPVoteTests(unittest.TestCase):
    def setUp(self):
        from adapters.mcp.stellamem_mcp_server import StellamemMCPServer

        self._td = tempfile.TemporaryDirectory()
        base = Path(self._td.name)
        self.ws = base / "ws"
        self.mem_root = base / "memories"
        seed(self.mem_root)
        self.index_db = base / "index.db"
        idx = Index(self.index_db)
        try:
            idx.build(self.mem_root)
        finally:
            idx.close()
        self.server = StellamemMCPServer(
            self.ws, index_db_path=self.index_db, allowed_subjects={"user", "persona"}
        )

    def tearDown(self):
        self._td.cleanup()

    def _call(self, name, arguments):
        resp = self.server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        content = resp["result"]["content"][0]["text"]
        return resp["result"]["isError"], json.loads(content) if content.startswith(
            "{"
        ) else content

    def test_vote_success_and_accumulates(self):
        is_err, out = self._call("stellamem_vote", {"subject": "user", "record_id": "c-vote-a"})
        self.assertFalse(is_err)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["manual"], 1)
        self.assertEqual(out["hits"], 0)
        is_err, out = self._call("stellamem_vote", {"subject": "user", "record_id": "c-vote-a"})
        self.assertFalse(is_err)
        self.assertEqual(out["manual"], 2)

    def test_vote_cross_subject_refused(self):
        is_err, out = self._call(
            "stellamem_vote", {"subject": "user", "record_id": "c-vote-persona"}
        )
        self.assertTrue(is_err)
        self.assertIn("another subject", str(out))

    def test_vote_missing_record_refused(self):
        is_err, _out = self._call("stellamem_vote", {"subject": "user", "record_id": "c-no-such"})
        self.assertTrue(is_err)

    def test_vote_binding_refused(self):
        from adapters.mcp.stellamem_mcp_server import StellamemMCPServer

        bound = StellamemMCPServer(
            self.ws / "ws2", index_db_path=self.index_db, allowed_subjects={"user"}
        )
        resp = bound.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "stellamem_vote",
                    "arguments": {"subject": "persona", "record_id": "c-vote-persona"},
                },
            }
        )
        self.assertTrue(resp["result"]["isError"])

    def test_vote_without_index_refused(self):
        from adapters.mcp.stellamem_mcp_server import StellamemMCPServer

        no_index = StellamemMCPServer(self.ws / "ws3", allowed_subjects={"user"})
        resp = no_index.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "stellamem_vote",
                    "arguments": {"subject": "user", "record_id": "c-vote-a"},
                },
            }
        )
        self.assertTrue(resp["result"]["isError"])

    def test_mcp_recall_records_auto_hit(self):
        is_err, out = self._call("stellamem_recall", {"subject": "user", "query": "冰滴咖啡"})
        self.assertFalse(is_err)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(len(out["items"]), 2)
        votes_file = self.ws / "system" / "hit-votes" / "votes.json"
        self.assertTrue(votes_file.exists())
        data = json.loads(votes_file.read_text(encoding="utf-8"))
        self.assertEqual(len(data["entries"]), 2)
        self.assertTrue(all(k.startswith("record:user:") for k in data["entries"]))

    def test_vote_tool_listed(self):
        names = [t["name"] for t in self.server.list_tools()]
        self.assertIn("stellamem_vote", names)


class SettlementHitsTests(unittest.TestCase):
    def setUp(self):
        from nightly_settlement import build_report, run_settlement

        self._build_report = build_report
        self._run_settlement = run_settlement
        self._td = tempfile.TemporaryDirectory()
        base = Path(self._td.name)
        self.mem_root = base / "memories"
        seed(self.mem_root)
        self.votes_path = base / "votes.json"
        hv = HitVotes(path=self.votes_path)
        hv.record_hit("user", "c-vote-a:r-c-vote-a", kind="fact")
        hv.record_hit("user", "c-vote-a:r-c-vote-a", kind="fact")
        hv.record_hit("user", "c-vote-a:r-c-vote-a", kind="fact")
        hv.vote("user", "c-vote-a:r-c-vote-a", kind="fact")
        hv.record_hit("persona", "c-vote-persona:r-c-vote-persona", kind="fact")
        hv.record_hit("persona", "c-vote-persona:r-c-vote-persona", kind="fact")

    def tearDown(self):
        self._td.cleanup()

    def test_hits_section_present_with_counts(self):
        now = datetime.now().astimezone()
        report = self._build_report(self.mem_root, now=now, votes_path=self.votes_path)
        text = report["text"]
        self.assertIn("命中投票", text)
        self.assertIn("user：今日 4 次（自动 3 / 手动 1），累计 4 次（自动 3 / 手动 1）", text)
        self.assertIn("persona：今日 2 次（自动 2 / 手动 0），累计 2 次（自动 2 / 手动 0）", text)
        self.assertIn("命中最多：user fact ×4、persona fact ×2", text)
        # 只报数，不报内容：虚构正文不得出现在报告里。
        self.assertNotIn("虚构", text)
        self.assertNotIn("冰滴咖啡", text)

    def test_missing_votes_file_reports_not_enabled(self):
        report = self._build_report(self.mem_root, votes_path=self.mem_root / "nope.json")
        self.assertIn("命中投票：未启用", report["text"])

    def test_no_votes_arg_reports_not_enabled(self):
        report = self._build_report(self.mem_root)
        self.assertIn("命中投票：未启用", report["text"])

    def test_corrupt_votes_file_fails_settlement(self):
        bad = Path(self._td.name) / "bad-votes.json"
        bad.write_text("not json{{{", encoding="utf-8")
        with self.assertRaises(ValueError):
            self._build_report(self.mem_root, votes_path=bad)

    def test_run_settlement_writes_hits_into_log(self):
        out_dir = Path(self._td.name) / "out"
        report = self._run_settlement(self.mem_root, out_dir=out_dir, votes_path=self.votes_path)
        log = Path(report["log_path"]).read_text(encoding="utf-8")
        self.assertIn("命中投票", log)


if __name__ == "__main__":
    unittest.main()
