"""注入门 P1 测试（compiler/injection_gate.py + MCP 召回入口）。

只用隔离临时目录与虚构数据（全部以“虚构”开头），不碰生产数据。
正例：记忆问题放行、正常召回；反例：无关问题拦住、budget 0、零注入。
建索引沿用 test_mcp_subject_binding.py 的做法：记录写进受管文件
long-term/projects.md，再由 Index 实例 build。
"""

from __future__ import annotations

import io
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "compiler"))

from compiler.injection_gate import check, gate_enabled, gated_budget  # noqa: E402
from indexer import Index  # noqa: E402
from storage import render_records  # noqa: E402
from adapters.mcp.stellamem_mcp_server import StellamemMCPServer  # noqa: E402


def make_record(claim_id, kind, subject, value, status="active"):
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
        "status": status,
        "relation": "NEW",
        "auto_write": True,
        "observed_at": "2026-11-01T00:00:00+00:00",
        "provenance": {"timestamp": "2026-11-01T09:00:00", "record_id": "rm-x"},
    }


def seed(mem_root: Path) -> None:
    rows = [
        make_record("c-gate-user-pref", "preference", "user", "虚构：只喝冰滴咖啡"),
        make_record("c-gate-user-proj", "project", "user", "虚构：星球邮差绘本"),
        make_record("c-gate-user-fact", "fact", "user", "虚构：住在海宁"),
        make_record("c-gate-user-lesson", "lesson", "user", "虚构：熬夜后第二天没精神"),
        make_record("c-gate-persona-fact", "fact", "persona", "虚构：鲸鱼娘最爱桂花乌龙"),
    ]
    target = mem_root / "long-term" / "projects.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_records("# 项目\n", rows), encoding="utf-8")


class GateDirectTest(unittest.TestCase):
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

    def tearDown(self):
        self.con.close()
        self._td.cleanup()

    def test_irrelevant_questions_blocked(self):
        # 第二轮那种无关问题样例：天气/代码/算术/电影推荐，全部拦住。
        for q in (
            "今天天气怎么样",
            "帮我写一个快速排序",
            "1+1 等于几",
            "推荐一部好看的电影",
        ):
            with self.subTest(q=q):
                result = check(self.con, q, subject="user")
                self.assertFalse(result["allow"], q)
                self.assertEqual(result["reason"], "no_signal", q)
                self.assertEqual(gated_budget(self.con, q, subject="user"), 0, q)

    def test_memory_questions_allowed(self):
        cases = {
            "冰滴咖啡多少钱": "lexical",  # 词面命中：冰滴/滴咖/咖啡
            "星球邮差项目进度怎么样": "lexical",  # 词面命中：星球/球邮/邮差
            "我有哪些偏好": "broad",  # 泛问通道
            "我有哪些决定": "broad",
            "最近在做什么": "broad",  # “最近”同时命中泛问 timeline，先按泛问放行（与 search() 顺序一致）
            "这几天发生了什么": "recent",  # 时间问句通道（不命中任何泛问词时）
            "我喜欢喝什么": "intent",  # 意图通道（喜欢 → preference）
        }
        for q, reason in cases.items():
            with self.subTest(q=q):
                result = check(self.con, q, subject="user")
                self.assertTrue(result["allow"], q)
                self.assertEqual(result["reason"], reason, q)
                self.assertGreater(gated_budget(self.con, q, subject="user"), 0, q)

    def test_technical_carve_out(self):
        # JS 原本的 carve-out：技术问题里只有意图/泛问信号、没有任何词面
        # 命中时，单凭“偏好”二字不能把记忆拖进来。
        result = check(self.con, "偏好 8081 端口", subject="user")
        self.assertFalse(result["allow"])
        self.assertEqual(result["reason"], "technical_no_lexical")
        self.assertEqual(gated_budget(self.con, "偏好 8081 端口", subject="user"), 0)
        # 对照：同一个“偏好”，话题落在真实记忆上时放行。
        control = check(self.con, "偏好 冰滴咖啡", subject="user")
        self.assertTrue(control["allow"])
        self.assertEqual(control["reason"], "lexical")

    def test_subject_isolation(self):
        # 只命中别的主体记录的问题，在本主体下拦住。
        q = "鲸鱼娘最爱喝什么"
        user_side = check(self.con, q, subject="user")
        self.assertFalse(user_side["allow"])
        self.assertEqual(user_side["reason"], "no_signal")
        persona_side = check(self.con, q, subject="persona")
        self.assertTrue(persona_side["allow"])
        self.assertEqual(persona_side["reason"], "lexical")

    def test_empty_prompt_blocked(self):
        result = check(self.con, "   ", subject="user")
        self.assertFalse(result["allow"])
        self.assertEqual(result["reason"], "empty_prompt")

    def test_zero_overlap_paraphrase_blocked_but_semantic_may_match(self):
        # 诚实记录的局限：零词面重叠的意译问句，语义离线时门拦住；
        # 语义在线时调用方传 semantic_available=True，放行。
        q = "我住哪"
        offline = check(self.con, q, subject="user")
        self.assertFalse(offline["allow"])
        self.assertEqual(offline["reason"], "no_signal")
        online = check(self.con, q, subject="user", semantic_available=True)
        self.assertTrue(online["allow"])
        self.assertEqual(online["reason"], "semantic_may_match")

    def test_gate_env_switch(self):
        self.assertTrue(gate_enabled())
        old = os.environ.get("STELLAMEM_INJECTION_GATE")
        try:
            os.environ["STELLAMEM_INJECTION_GATE"] = "0"
            self.assertFalse(gate_enabled())
        finally:
            if old is None:
                os.environ.pop("STELLAMEM_INJECTION_GATE", None)
            else:
                os.environ["STELLAMEM_INJECTION_GATE"] = old


class GateMCPTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        base = Path(self._td.name)
        self.ws = base / "ws"
        self.mem_root = base / "memories"
        seed(self.mem_root)
        self.db_path = base / "index.db"
        idx = Index(self.db_path)
        try:
            idx.build(self.mem_root)
        finally:
            idx.close()

    def tearDown(self):
        self._td.cleanup()

    def _server(self, allowed_subjects=None):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            return StellamemMCPServer(
                self.ws,
                index_db_path=self.db_path,
                allowed_subjects=allowed_subjects,
            )

    def test_mcp_irrelevant_question_gated(self):
        server = self._server(allowed_subjects={"user"})
        result = server._call_tool(
            "stellamem_recall", {"query": "今天天气怎么样", "subject": "user"}
        )
        self.assertEqual(result["status"], "gated")
        self.assertEqual(result["items"], [])
        self.assertEqual(result["budget"], 0)
        self.assertEqual(result["chars"], 0)
        self.assertEqual(result["gate"]["reason"], "no_signal")

    def test_mcp_memory_question_recalled(self):
        server = self._server(allowed_subjects={"user"})
        result = server._call_tool(
            "stellamem_recall", {"query": "冰滴咖啡多少钱", "subject": "user"}
        )
        self.assertEqual(result["status"], "ok")
        self.assertGreater(len(result["items"]), 0)
        self.assertTrue(any("冰滴咖啡" in it["text"] for it in result["items"]))

    def test_mcp_gate_off_env_falls_through_to_recall(self):
        old = os.environ.get("STELLAMEM_INJECTION_GATE")
        try:
            os.environ["STELLAMEM_INJECTION_GATE"] = "0"
            server = self._server(allowed_subjects={"user"})
            result = server._call_tool(
                "stellamem_recall", {"query": "今天天气怎么样", "subject": "user"}
            )
            # 门关了：不再是 gated，召回照常跑（无关问题本来就召回为空）。
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["items"], [])
        finally:
            if old is None:
                os.environ.pop("STELLAMEM_INJECTION_GATE", None)
            else:
                os.environ["STELLAMEM_INJECTION_GATE"] = old

    def test_gate_exceptions_block_recall_without_leaking_exception_text(self):
        server = self._server(allowed_subjects={"user"})
        for error in (RuntimeError, PermissionError, ValueError, sqlite3.DatabaseError):
            with self.subTest(error=error.__name__):
                stderr = io.StringIO()
                with (
                    patch.dict(os.environ, {"STELLAMEM_INJECTION_GATE": "1"}),
                    patch("compiler.injection_gate.check", side_effect=error("private sentinel")),
                    patch("compiler.search.recall") as recall,
                    redirect_stderr(stderr),
                ):
                    result = server._call_tool(
                        "stellamem_recall", {"query": "冰滴咖啡多少钱", "subject": "user"}
                    )
                recall.assert_not_called()
                self.assertEqual(result["status"], "fail_closed")
                self.assertEqual(result["items"], [])
                self.assertEqual(result["chars"], 0)
                self.assertEqual(result["budget"], 0)
                self.assertEqual(result["gate"]["reason"], "gate_error")
                self.assertEqual(result["gate"]["error_type"], error.__name__)
                self.assertNotIn("private sentinel", str(result) + stderr.getvalue())

    def test_invalid_gate_verdict_is_not_truthy_authorization(self):
        server = self._server(allowed_subjects={"user"})
        for verdict in ({"allow": "false"}, {"allow": 1}, {"allow": None}, {}, None):
            with self.subTest(verdict=verdict):
                with (
                    patch.dict(os.environ, {"STELLAMEM_INJECTION_GATE": "1"}),
                    patch("compiler.injection_gate.check", return_value=verdict),
                    patch("compiler.search.recall") as recall,
                    redirect_stderr(io.StringIO()),
                ):
                    result = server._call_tool(
                        "stellamem_recall", {"query": "冰滴咖啡多少钱", "subject": "user"}
                    )
                recall.assert_not_called()
                self.assertEqual(result["status"], "fail_closed")

    def test_authorization_precedes_gate_even_when_gate_is_broken(self):
        server = self._server(allowed_subjects={"user"})
        with (
            patch("compiler.injection_gate.check", side_effect=RuntimeError("broken")) as gate,
            patch("compiler.search.recall") as recall,
        ):
            result = server._call_tool(
                "stellamem_recall", {"query": "桂花乌龙", "subject": "persona"}
            )
        gate.assert_not_called()
        recall.assert_not_called()
        self.assertEqual(result["status"], "fail_closed")

    def test_gate_failure_does_not_poison_next_healthy_recall(self):
        server = self._server(allowed_subjects={"user"})
        args = {"query": "冰滴咖啡多少钱", "subject": "user"}
        with (
            patch.dict(os.environ, {"STELLAMEM_INJECTION_GATE": "1"}),
            patch("compiler.injection_gate.check", side_effect=RuntimeError("broken")),
            redirect_stderr(io.StringIO()),
        ):
            failed = server._call_tool("stellamem_recall", args)
        self.assertEqual(failed["status"], "fail_closed")
        recovered = server._call_tool("stellamem_recall", args)
        self.assertEqual(recovered["status"], "ok")
        self.assertTrue(any("冰滴咖啡" in it["text"] for it in recovered["items"]))


if __name__ == "__main__":
    unittest.main()
