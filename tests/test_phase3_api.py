"""第三阶段：本机 API 验收（真实 HTTP，不是 mock）。"""

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

import api
from storage import render_records


def seed(root: Path):
    records = []
    for idx, (kind, value, status) in enumerate(
        [
            ("project", "星忆记忆系统第一阶段已完成", "active"),
            ("preference", "偏好用大白话讲解技术", "active"),
            ("decision", "决定用 SQLite 做本地索引", "active"),
            ("lesson", "教训：不要用无 BOM 的 PowerShell 脚本", "active"),
            ("fact", "待审：用户在天津住了两年", "pending"),
        ]
    ):
        records.append(
            {
                "schema_version": 2,
                "claim_id": f"c-api{idx}",
                "revision_id": f"r-api{idx}",
                "event_id": f"e-api{idx}",
                "kind": kind,
                "scope": "global",
                "subject": "用户",
                "predicate": f"p{idx}",
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
                "provenance": {
                    "timestamp": "2026-11-01T09:00:00",
                    "record_id": f"rm-{idx}",
                },
            }
        )
    target = root / "long-term" / "projects.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_records("# 项目\n", records), encoding="utf-8")


class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        cls.root = base / "memories"
        seed(cls.root)
        cls.httpd, cls.svc = api.make_server(cls.root, base / "index.db", port=0)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.token = cls.svc.ensure_token()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.svc.index.close()
        cls.tmp.cleanup()

    # ---------------- helpers ----------------

    def call(self, method, path, body=None, headers=None, host="127.0.0.1", auth=True):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Host", f"{host}:{self.port}")
        if data:
            req.add_header("Content-Type", "application/json")
        # 默认带 Token（读接口现在也要 Token）；测 401 的用例传 auth=False
        if auth and "Authorization" not in (headers or {}):
            req.add_header("Authorization", f"Bearer {self.token}")
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Type", "").startswith("application/x-ndjson"):
                    return resp.status, raw.decode("utf-8")
                return resp.status, json.loads(raw or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    # ---------------- 读接口 ----------------

    def test_read_requires_token(self):
        status, data = self.call("GET", "/v1/search?q=test", auth=False)
        self.assertEqual(status, 401)
        self.assertEqual(data["error"], "unauthorized")

    def test_token_subject_scope(self):
        """Token 主体权限：scoped token 只能读绑定的主体，越权 403；master 不受限。"""
        import hashlib

        # 建 scoped token：只允许 subject="用户"（seed 数据的主体）
        scoped_token = "scoped-test-token-xyz"
        scoped_hash = hashlib.sha256(scoped_token.encode()).hexdigest()
        scopes_file = self.root / "system" / "api-token-scopes.json"
        scopes_file.write_text(json.dumps({scoped_hash: ["用户"]}), encoding="utf-8")
        # master token 读 search：应返回（不受限）
        status, data = self.call("GET", "/v1/search?q=" + urllib.parse.quote("记忆系统"))
        self.assertEqual(status, 200)
        # scoped token 读 search：应返回（只含 用户 主体）
        status, data = self.call(
            "GET",
            "/v1/search?q=" + urllib.parse.quote("记忆系统"),
            headers={"Authorization": f"Bearer {scoped_token}"},
        )
        self.assertEqual(status, 200)
        for item in data.get("items", []):
            self.assertEqual(item.get("subject"), "用户")
        # scoped token 越权查 person=persona：应 403
        status, data = self.call(
            "GET",
            "/v1/search?q=" + urllib.parse.quote("记忆系统") + "&person=persona",
            headers={"Authorization": f"Bearer {scoped_token}"},
        )
        self.assertEqual(status, 403)
        # 清理 scopes 文件，避免影响其他测试
        scopes_file.unlink()

    def test_recall_text_filtered(self):
        """P0：recall 的 text 必须与过滤后的 items 一致，不泄露越权主体正文。"""
        import hashlib

        scoped_token = "scoped-recall-test"
        scoped_hash = hashlib.sha256(scoped_token.encode()).hexdigest()
        scopes_file = self.root / "system" / "api-token-scopes.json"
        scopes_file.write_text(json.dumps({scoped_hash: ["用户"]}), encoding="utf-8")
        # recall 不指定 subject，应只返回 用户 主体的 items 和 text
        status, data = self.call(
            "POST",
            "/v1/recall",
            {"prompt": "记忆系统", "budget": 1200},
            headers={"Authorization": f"Bearer {scoped_token}"},
        )
        self.assertEqual(status, 200)
        for item in data.get("items", []):
            self.assertEqual(item.get("subject"), "用户")
        # text 里不应出现其他主体的内容（seed 数据只有 用户 主体，此处验证 text 与 items 一致）
        self.assertEqual(
            data.get("text", "").count("\n") + 1 if data.get("text") else 0,
            len(data.get("items", [])),
        )
        scopes_file.unlink()

    def test_scoped_token_readonly(self):
        """P0：Scoped Token 禁止写操作（approve/reject/undo/backup/rebuild）。"""
        import hashlib

        scoped_token = "scoped-write-test"
        scoped_hash = hashlib.sha256(scoped_token.encode()).hexdigest()
        scopes_file = self.root / "system" / "api-token-scopes.json"
        scopes_file.write_text(json.dumps({scoped_hash: ["用户"]}), encoding="utf-8")
        headers = {"Authorization": f"Bearer {scoped_token}"}
        # approve 应 403
        status, _ = self.call("POST", "/v1/approve", {"claim_id": "c-api0"}, headers=headers)
        self.assertEqual(status, 403)
        # reject 应 403
        status, _ = self.call("POST", "/v1/reject", {"claim_id": "c-api0"}, headers=headers)
        self.assertEqual(status, 403)
        # backup 应 403
        status, _ = self.call("POST", "/v1/backup", {}, headers=headers)
        self.assertEqual(status, 403)
        # rebuild 应 403
        status, _ = self.call("POST", "/v1/rebuild", {}, headers=headers)
        self.assertEqual(status, 403)
        scopes_file.unlink()

    def test_health(self):
        status, data = self.call("GET", "/v1/health")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertGreaterEqual(data["records"], 5)
        self.assertFalse(data["model_required"])

    def test_search_chinese(self):
        status, data = self.call("GET", "/v1/search?q=" + urllib.parse.quote("记忆系统第一阶段"))
        self.assertEqual(status, 200)
        self.assertGreaterEqual(data["count"], 1)
        self.assertIn("星忆", data["items"][0]["value"])

    def test_search_fragmented_query(self):
        status, data = self.call("GET", "/v1/search?q=" + urllib.parse.quote("第一阶段 记忆 系统"))
        self.assertEqual(status, 200)
        self.assertGreaterEqual(data["count"], 1)

    def test_timeline(self):
        status, data = self.call("GET", "/v1/timeline")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(data["count"], 5)

    def test_memory_lookup_and_404(self):
        status, data = self.call("GET", "/v1/memory/c-api0")
        self.assertEqual(status, 200)
        self.assertEqual(data["claim_id"], "c-api0")
        status, data = self.call("GET", "/v1/memory/c-missing")
        self.assertEqual(status, 404)

    def test_history_and_related(self):
        status, data = self.call("GET", "/v1/history/c-api0")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(data["items"]), 1)
        status, data = self.call("GET", "/v1/related/c-api0:r-api0")
        self.assertEqual(status, 200)

    def test_evidence_missing_raw_returns_404(self):
        status, data = self.call("GET", "/v1/evidence/rm-0")
        self.assertEqual(status, 404)

    def test_recall_budget(self):
        status, data = self.call(
            "POST", "/v1/recall", {"prompt": "我的记忆系统做到哪了", "budget": 120}
        )
        self.assertEqual(status, 200)
        self.assertLessEqual(data["chars"], 120)
        self.assertGreaterEqual(len(data["items"]), 1)

    def test_export_ndjson(self):
        status, body = self.call("GET", "/v1/export?ns=personal")
        self.assertEqual(status, 200)
        lines = [line for line in body.strip().split("\n") if line]
        self.assertGreaterEqual(len(lines), 5)
        self.assertEqual(json.loads(lines[0])["namespace"], "personal")

    # ---------------- 安全边界 ----------------

    def test_bad_host_rejected(self):
        status, data = self.call("GET", "/v1/health", host="evil.example.com")
        self.assertEqual(status, 421)

    def test_write_requires_token(self):
        status, data = self.call("POST", "/v1/approve", {"claim_id": "c-api4"}, auth=False)
        self.assertEqual(status, 401)
        self.assertEqual(data["error"], "unauthorized")

    def test_cross_origin_blocked(self):
        status, data = self.call(
            "POST",
            "/v1/approve",
            {"claim_id": "c-api4"},
            headers={
                "Authorization": f"Bearer {self.token}",
                "Origin": "http://evil.example.com",
            },
        )
        self.assertEqual(status, 403)

    def test_delete_not_allowed(self):
        status, _ = self.call("DELETE", "/v1/memory/c-api0")
        self.assertEqual(status, 405)

    def test_binds_loopback_only(self):
        try:
            api.make_server(self.root, Path(self.tmp.name) / "x.db", host="0.0.0.0", port=0)
            self.fail("binding 0.0.0.0 must be refused")
        except ValueError:
            pass

    # ---------------- 写路径 ----------------

    def test_approve_with_token_then_undo(self):
        status, data = self.call(
            "POST",
            "/v1/approve",
            {"claim_id": "c-api4"},
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(status, 200, data)
        row = self.svc.index.con.execute(
            "SELECT status FROM records WHERE claim_id='c-api4'"
        ).fetchone()
        self.assertEqual(row["status"], "active")

        status, data = self.call(
            "POST",
            "/v1/undo",
            {"txid": data["txid"]},
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(status, 200, data)
        row = self.svc.index.con.execute(
            "SELECT status FROM records WHERE claim_id='c-api4'"
        ).fetchone()
        self.assertEqual(row["status"], "pending")

    def test_reject_then_rebuild(self):
        status, data = self.call(
            "POST",
            "/v1/reject",
            {"claim_id": "c-api4"},
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(status, 200, data)
        status, data = self.call(
            "POST", "/v1/rebuild", {}, headers={"Authorization": f"Bearer {self.token}"}
        )
        self.assertEqual(status, 200)
        self.assertGreater(data["records"], 0)

    def test_backup_creates_verified_archive(self):
        status, data = self.call(
            "POST", "/v1/backup", {}, headers={"Authorization": f"Bearer {self.token}"}
        )
        self.assertEqual(status, 200, data)
        self.assertTrue(Path(data["archive"]).exists())
        self.assertGreater(data["files"], 0)


if __name__ == "__main__":
    unittest.main()
