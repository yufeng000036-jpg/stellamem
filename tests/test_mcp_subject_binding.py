"""MCP 主体切换宿主授权绑定：只用隔离临时目录 + 虚构数据，绝不用生产数据。

覆盖：无绑定时行为不变且启动警告出现；绑定 user 后切 persona 报错、
查 persona 空结果、查 user 正常（真实索引）、往 persona 写偏好被拒绝；
别名输入（用户）归进允许集；非法绑定值启动即失败；CLI 与环境变量
两种入口都生效、CLI 优先；真实入口（子进程）接线正确。
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "compiler"))

from adapters.mcp.stellamem_mcp_server import (  # noqa: E402
    StellamemMCPServer,
    build_server,
    parse_allowed_subjects,
)
from indexer import Index  # noqa: E402
from storage import render_records  # noqa: E402


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


def seed(root: Path):
    """虚构记忆各一条：user 记冰滴咖啡，persona 记桂花乌龙。"""
    rows = [
        make_record("c-bind-user", "preference", "user", "虚构偏好：只喝冰滴咖啡"),
        make_record("c-bind-persona", "fact", "persona", "虚构事实：鲸鱼娘最爱桂花乌龙"),
    ]
    target = root / "long-term" / "projects.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_records("# 项目\n", rows), encoding="utf-8")


class BindingParseTests(unittest.TestCase):
    def test_none_means_unbound(self):
        self.assertIsNone(parse_allowed_subjects(None))

    def test_comma_separated_and_whitespace_tolerant(self):
        self.assertEqual(parse_allowed_subjects("user, persona"), {"user", "persona"})

    def test_alias_normalized_into_allowed_set(self):
        # 别名输入（用户）能正确归进允许集。
        self.assertEqual(parse_allowed_subjects("用户"), {"user"})
        self.assertEqual(parse_allowed_subjects("用户, 人格"), {"user", "persona"})

    def test_unknown_subject_fails(self):
        with self.assertRaises(ValueError):
            parse_allowed_subjects("bogus")
        with self.assertRaises(ValueError):
            parse_allowed_subjects("user, bogus")

    def test_empty_value_fails_not_silently_unbound(self):
        with self.assertRaises(ValueError):
            parse_allowed_subjects("")
        with self.assertRaises(ValueError):
            parse_allowed_subjects(" , ")


class ServerBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.ws = base / "ws"
        self.mem_root = base / "memories"
        seed(self.mem_root)
        self.idx = Index(base / "index.db")
        self.idx.build(self.mem_root)
        self.index_db = base / "index.db"
        self.servers = []

    def tearDown(self):
        self.idx.close()
        self.tmp.cleanup()

    def _server(self, allowed, with_index=True):
        buf = io.StringIO()
        with redirect_stderr(buf):
            srv = StellamemMCPServer(
                self.ws,
                index_db_path=self.index_db if with_index else None,
                allowed_subjects=allowed,
            )
        self.servers.append(srv)
        return srv, buf.getvalue()

    def _call(self, srv, name, args):
        return srv._call_tool(name, args)

    def test_unbound_keeps_legacy_behavior_and_warns(self):
        srv, warned = self._server(None, with_index=False)
        self.assertIn("not configured", warned)
        self.assertIn("single-person trusted use only", warned)
        # 无绑定时行为不变：切 persona、查 persona、写 persona 都放行。
        out = self._call(srv, "stellamem_switch_subject", {"subject": "persona"})
        self.assertEqual(out["active_subject"], "persona")
        out = self._call(srv, "stellamem_recall", {"subject": "persona", "query": "桂花乌龙"})
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["subject"], "persona")
        out = self._call(
            srv,
            "stellamem_propose_preference",
            {
                "subject": "persona",
                "topic": "虚构话题",
                "statement": "虚构陈述",
                "evidence": ["虚构证据"],
            },
        )
        self.assertTrue(out["persisted"])

    def test_bound_switch_to_disallowed_subject_raises(self):
        srv, _ = self._server({"user"})
        with self.assertRaises(ValueError) as ctx:
            self._call(srv, "stellamem_switch_subject", {"subject": "persona"})
        self.assertIn("not in this server's allowed subjects", str(ctx.exception))
        self.assertIsNone(srv.active_subject)

    def test_bound_switch_to_allowed_subject_ok(self):
        srv, _ = self._server({"user"})
        out = self._call(srv, "stellamem_switch_subject", {"subject": "user"})
        self.assertEqual(out["active_subject"], "user")

    def test_bound_recall_disallowed_subject_fail_closed(self):
        srv, _ = self._server({"user"})
        out = self._call(srv, "stellamem_recall", {"subject": "persona", "query": "桂花乌龙"})
        self.assertEqual(out["status"], "fail_closed")
        self.assertEqual(out["items"], [])
        self.assertIn("not allowed", out["reason"])

    def test_bound_recall_allowed_subject_live(self):
        srv, _ = self._server({"user"})
        out = self._call(srv, "stellamem_recall", {"subject": "user", "query": "冰滴咖啡"})
        self.assertEqual(out["status"], "ok")
        self.assertTrue(out["wired"])
        self.assertTrue(
            any("冰滴咖啡" in it.get("text", "") for it in out["items"]),
            f"绑定内 user 召回应命中虚构记录：{out}",
        )
        # 同一台绑定 server 上，查 persona 记录的词也不应漏出来。
        out = self._call(srv, "stellamem_recall", {"subject": "user", "query": "桂花乌龙"})
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["items"], [])

    def test_bound_recall_via_active_subject_blocked(self):
        # 防御性：即使 active_subject 事先已被设为集外主体（例如绑定
        # 之前设的），recall 不带 subject 时也要拦住，不能只拦显式参数。
        srv, _ = self._server({"user"})
        srv.active_subject = "persona"
        out = self._call(srv, "stellamem_recall", {"query": "桂花乌龙"})
        self.assertEqual(out["status"], "fail_closed")
        self.assertEqual(out["items"], [])

    def test_bound_recall_via_active_subject_allowed(self):
        srv, _ = self._server({"user"})
        self._call(srv, "stellamem_switch_subject", {"subject": "user"})
        out = self._call(srv, "stellamem_recall", {"query": "冰滴咖啡"})
        self.assertEqual(out["status"], "ok")
        self.assertTrue(any("冰滴咖啡" in it.get("text", "") for it in out["items"]))

    def test_bound_write_to_disallowed_subject_raises(self):
        srv, _ = self._server({"user"})
        with self.assertRaises(ValueError) as ctx:
            self._call(
                srv,
                "stellamem_propose_preference",
                {
                    "subject": "persona",
                    "topic": "虚构话题",
                    "statement": "虚构陈述",
                    "evidence": ["虚构证据"],
                },
            )
        self.assertIn("refuses the write", str(ctx.exception))
        history = self.ws / "preference_history.jsonl"
        if history.exists():
            self.assertNotIn('"persona"', history.read_text(encoding="utf-8"))

    def test_bound_write_to_allowed_subject_ok(self):
        srv, _ = self._server({"user"})
        out = self._call(
            srv,
            "stellamem_propose_preference",
            {
                "subject": "user",
                "topic": "虚构话题",
                "statement": "虚构陈述",
                "evidence": ["虚构证据"],
            },
        )
        self.assertEqual(out["status"], "pending")
        self.assertTrue(out["persisted"])

    def test_constructor_rejects_invalid_allowed_subject(self):
        with self.assertRaises(ValueError):
            self._server({"bogus"})
        # 别名在构造层同样归一化。
        srv, _ = self._server({"用户"})
        self.assertEqual(srv.allowed_subjects, frozenset({"user"}))


class BuildServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = str(Path(self.tmp.name) / "ws")

    def tearDown(self):
        self.tmp.cleanup()

    def test_env_var_binding(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            srv = build_server([self.ws], environ={"STELLAMEM_ALLOWED_SUBJECTS": "user"})
        self.assertEqual(srv.allowed_subjects, frozenset({"user"}))
        self.assertNotIn("WARNING", buf.getvalue())

    def test_env_var_alias_normalized(self):
        srv = build_server([self.ws], environ={"STELLAMEM_ALLOWED_SUBJECTS": "用户, persona"})
        self.assertEqual(srv.allowed_subjects, frozenset({"user", "persona"}))

    def test_cli_wins_over_env(self):
        srv = build_server(
            [self.ws, "--allowed-subjects", "persona"],
            environ={"STELLAMEM_ALLOWED_SUBJECTS": "user"},
        )
        self.assertEqual(srv.allowed_subjects, frozenset({"persona"}))

    def test_neither_set_means_unbound_with_warning(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            srv = build_server([self.ws], environ={})
        self.assertIsNone(srv.allowed_subjects)
        self.assertIn("not configured", buf.getvalue())

    def test_invalid_env_binding_fails_startup(self):
        with self.assertRaises(ValueError):
            build_server([self.ws], environ={"STELLAMEM_ALLOWED_SUBJECTS": "bogus"})

    def test_invalid_cli_binding_fails_startup(self):
        with self.assertRaises(ValueError):
            build_server([self.ws, "--allowed-subjects", "user, bogus"], environ={})


class SubprocessEntrypointTests(unittest.TestCase):
    def _run(self, argv, stdin_text=""):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO)
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env.pop("STELLAMEM_ALLOWED_SUBJECTS", None)
        env.pop("STELLAMEM_INDEX_DB", None)
        return subprocess.run(
            [sys.executable, str(REPO / "adapters" / "mcp" / "stellamem_mcp_server.py")] + argv,
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(REPO),
            env=env,
        )

    def test_subprocess_valid_binding_answers_initialize(self):
        req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        with tempfile.TemporaryDirectory() as tmp:
            proc = self._run([tmp, "--allowed-subjects", "user"], req + "\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("WARNING", proc.stderr)
        resp = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(resp["result"]["protocolVersion"], "2024-11-05")

    def test_subprocess_invalid_binding_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = self._run([tmp, "--allowed-subjects", "bogus"])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("bogus", proc.stderr)


if __name__ == "__main__":
    unittest.main()
