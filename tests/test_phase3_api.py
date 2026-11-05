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


def append_to_projects(root: Path, svc, records):
    """把记录追加到 long-term/projects.md（MANAGED 文件，索引会读），返回原内容以便恢复。

    注意：索引只读 MANAGED 清单里的 8 个文件，写 persona.md 这类新文件不会被索引，
    测试会空跑通过（假阳性）。必须追加到已有 MANAGED 文件。
    """
    target = root / "long-term" / "projects.md"
    original = target.read_text(encoding="utf-8")
    blocks = []
    for rec in records:
        blocks.extend(
            ["```stellamem", json.dumps(rec, ensure_ascii=False, sort_keys=True), "```", ""]
        )
    target.write_text(original.rstrip() + "\n\n" + "\n".join(blocks), encoding="utf-8")
    svc.ensure_index(force=True)
    return original


def restore_projects(root: Path, svc, original):
    target = root / "long-term" / "projects.md"
    target.write_text(original, encoding="utf-8")
    svc.ensure_index(force=True)


def make_record(claim_id, subject, value):
    """合成一条标准测试记录。"""
    return {
        "schema_version": 2,
        "claim_id": claim_id,
        "revision_id": f"r-{claim_id}",
        "event_id": f"e-{claim_id}",
        "kind": "preference",
        "scope": "global",
        "subject": subject,
        "predicate": f"p-{claim_id}",
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
        "observed_at": "2026-11-04T00:00:00+00:00",
        "provenance": {
            "timestamp": "2026-11-04T09:00:00",
            "record_id": f"rm-{claim_id}",
        },
    }


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
        # 评审 P2：setUpClass 必须建好索引，否则每个测试依赖前面测试
        # 偶然触发的重建才能读到数据，单独跑就全空（test_health / history）。
        cls.svc.ensure_index()

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

    def test_dual_subject_no_leak(self):
        """评审反例：双主体数据下，scoped token 绝不泄露越权主体（items + text + search）。"""
        import hashlib

        # 往记忆文件里加一条 persona 主体的记录，再重建索引（FTS 也会同步）
        persona_rec = {
            "schema_version": 2,
            "claim_id": "c-persona-1",
            "revision_id": "r-persona-1",
            "event_id": "e-persona-1",
            "kind": "preference",
            "scope": "global",
            "subject": "persona",
            "predicate": "p-persona",
            "value": "鲸鱼娘喜欢唱歌跳舞",
            "evidence": "鲸鱼娘喜欢唱歌跳舞",
            "source": "persona",
            "confidence": 0.9,
            "importance": 0.5,
            "sensitivity": "PRIVATE",
            "write_policy": "allow",
            "injection_policy": "recall",
            "evidence_verified": True,
            "status": "active",
            "relation": "NEW",
            "auto_write": True,
            "observed_at": "2026-11-04T00:00:00+00:00",
            "provenance": {
                "timestamp": "2026-11-04T09:00:00",
                "record_id": "rm-persona",
            },
        }
        # 必须追加到 MANAGED 文件 projects.md，写新文件不会被索引（假阳性）
        # 同时加一条 用户 记录含"喜欢"，否则 search 空跑通过（seed 里没有"喜欢"的用户记录）
        user_rec = make_record("c-user-like", "用户", "用户喜欢喝茶看书")
        original = append_to_projects(self.root, self.svc, [persona_rec, user_rec])

        scoped_token = "scoped-dual-test"
        scoped_hash = hashlib.sha256(scoped_token.encode()).hexdigest()
        scopes_file = self.root / "system" / "api-token-scopes.json"
        scopes_file.write_text(json.dumps({scoped_hash: ["用户"]}), encoding="utf-8")
        headers = {"Authorization": f"Bearer {scoped_token}"}

        # 1. search：结果里绝不能有 persona，且必须非空（否则是空跑通过）
        status, data = self.call(
            "GET",
            "/v1/search?q=" + urllib.parse.quote("喜欢"),
            headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertGreater(len(data.get("items", [])), 0)
        for item in data.get("items", []):
            self.assertEqual(item.get("subject"), "用户")
            self.assertNotIn("鲸鱼娘", item.get("value", ""))

        # 2. recall：items 和 text 里绝不能有 persona，且必须非空
        status, data = self.call(
            "POST",
            "/v1/recall",
            {"prompt": "喜欢", "budget": 1200},
            headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertGreater(len(data.get("items", [])), 0)
        for item in data.get("items", []):
            self.assertEqual(item.get("subject"), "用户")
        self.assertNotIn("鲸鱼娘", data.get("text", ""))
        self.assertNotIn("persona", data.get("text", ""))

        # 清理：恢复 projects.md 并重建索引，避免影响其他测试
        restore_projects(self.root, self.svc, original)
        scopes_file.unlink()

    def test_empty_scope_returns_nothing(self):
        """评审 P0：空权限列表 [] 的 Token 必须返回零结果，绝不能查到全部主体。"""
        import hashlib

        scoped_token = "scoped-empty-test"
        scoped_hash = hashlib.sha256(scoped_token.encode()).hexdigest()
        scopes_file = self.root / "system" / "api-token-scopes.json"
        scopes_file.write_text(json.dumps({scoped_hash: []}), encoding="utf-8")
        headers = {"Authorization": f"Bearer {scoped_token}"}
        # search：必须返回 0 条（fail-closed），不是全部记录
        status, data = self.call(
            "GET",
            "/v1/search?q=" + urllib.parse.quote("记忆系统"),
            headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(data.get("count"), 0)
        self.assertEqual(data.get("items"), [])
        # recall：必须返回 0 条
        status, data = self.call(
            "POST",
            "/v1/recall",
            {"prompt": "记忆系统", "budget": 1200},
            headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(data.get("items"), [])
        # timeline：必须返回 0 条
        status, data = self.call("GET", "/v1/timeline", headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(data.get("items"), [])
        scopes_file.unlink()

    def test_malformed_scope_config_rejected(self):
        """评审 P0：Scoped Token 配置值是 null / 字符串 / 数字时，必须拒绝认证（401），
        绝不能拿到 Master 权限（之前 get_token_subjects 返回 None = 不受限）。"""
        import hashlib

        scopes_file = self.root / "system" / "api-token-scopes.json"
        for idx, bad_value in enumerate((None, "用户", 123, {"x": 1})):
            # Token 名只用 ASCII（Authorization 头是 latin-1，中文会编码失败）
            token = f"scoped-bad-{idx}"
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            scopes_file.write_text(json.dumps({token_hash: bad_value}), encoding="utf-8")
            headers = {"Authorization": f"Bearer {token}"}
            # 读接口必须 401（认证失败），不是 200 更不是 Master 权限
            status, _ = self.call(
                "GET",
                "/v1/search?q=" + urllib.parse.quote("记忆系统"),
                headers=headers,
            )
            self.assertEqual(status, 401, f"bad config {bad_value!r} should be 401")
            # 写接口也必须 401，不能审批
            status, _ = self.call("POST", "/v1/approve", {"claim_id": "c-api0"}, headers=headers)
            self.assertEqual(status, 401, f"bad config {bad_value!r} write should be 401")
        scopes_file.unlink()

    def test_explicit_subject_intersects_scope(self):
        """评审 P1：Scoped Token 允许 user+persona，客户端明确只查 user 时，
        SQL 必须只返回 user，不能让授权范围覆盖显式指定。"""
        import hashlib

        # 加一条 persona 记录（与 seed 的 用户 并存）
        persona_rec = {
            "schema_version": 2,
            "claim_id": "c-persona-2",
            "revision_id": "r-persona-2",
            "event_id": "e-persona-2",
            "kind": "preference",
            "scope": "global",
            "subject": "persona",
            "predicate": "p-persona-2",
            "value": "鲸鱼娘喜欢唱歌跳舞",
            "evidence": "鲸鱼娘喜欢唱歌跳舞",
            "source": "persona",
            "confidence": 0.9,
            "importance": 0.5,
            "sensitivity": "PRIVATE",
            "write_policy": "allow",
            "injection_policy": "recall",
            "evidence_verified": True,
            "status": "active",
            "relation": "NEW",
            "auto_write": True,
            "observed_at": "2026-11-04T00:00:00+00:00",
            "provenance": {
                "timestamp": "2026-11-04T09:00:00",
                "record_id": "rm-persona-2",
            },
        }
        # 必须追加到 MANAGED 文件 projects.md，写新文件不会被索引（假阳性）
        # 两边各加一条含"喜欢"的记录，否则一侧空跑通过
        user_rec = make_record("c-both-user", "用户", "用户喜欢喝茶")
        original = append_to_projects(self.root, self.svc, [persona_rec, user_rec])

        scoped_token = "scoped-both-test"
        scoped_hash = hashlib.sha256(scoped_token.encode()).hexdigest()
        scopes_file = self.root / "system" / "api-token-scopes.json"
        scopes_file.write_text(json.dumps({scoped_hash: ["用户", "persona"]}), encoding="utf-8")
        headers = {"Authorization": f"Bearer {scoped_token}"}

        # 明确只查 user：结果只能有 用户，且不能为空（否则是空跑通过）
        status, data = self.call(
            "GET",
            "/v1/search?q=" + urllib.parse.quote("喜欢") + "&person=" + urllib.parse.quote("用户"),
            headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertGreater(len(data.get("items", [])), 0)
        for item in data.get("items", []):
            self.assertEqual(item.get("subject"), "用户")
        # 明确只查 persona：结果只能有 persona，且不能为空（否则是空跑通过）
        status, data = self.call(
            "GET",
            "/v1/search?q="
            + urllib.parse.quote("喜欢")
            + "&person="
            + urllib.parse.quote("persona"),
            headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertGreater(len(data.get("items", [])), 0)
        for item in data.get("items", []):
            self.assertEqual(item.get("subject"), "persona")
        # 不指定：两个主体都出现
        status, data = self.call(
            "GET",
            "/v1/search?q=" + urllib.parse.quote("喜欢"),
            headers=headers,
        )
        self.assertEqual(status, 200)
        subjects = {item.get("subject") for item in data.get("items", [])}
        self.assertEqual(subjects, {"用户", "persona"})

        restore_projects(self.root, self.svc, original)
        scopes_file.unlink()

    def test_subject_alias_recall(self):
        """评审最终验收 P1：旧版 subject="用户" 与标准 "user" 是同一命名空间。
        查 "user" 必须命中 "用户" 记录；查 persona/viewer 绝不能串话。
        索引里保留原始拼写，不批量改写生产记忆。"""
        import hashlib

        recs = [
            make_record("c-alias-user-cn", "用户", "别名测试用户旧拼写喜欢喝茶"),
            make_record("c-alias-user-en", "user", "别名测试用户标准拼写喜欢喝茶"),
            make_record("c-alias-persona", "persona", "别名测试鲸鱼娘喜欢唱歌"),
            make_record("c-alias-viewer", "viewer", "别名测试观众喜欢看直播"),
        ]
        # 必须追加到 MANAGED 文件 projects.md，写新文件不会被索引（假阳性）
        original = append_to_projects(self.root, self.svc, recs)

        # 1. master 查 user：必须命中 用户 + user 两条，绝不能有 persona/viewer
        status, data = self.call(
            "GET",
            "/v1/search?q=" + urllib.parse.quote("别名测试") + "&person=user",
        )
        self.assertEqual(status, 200)
        found = {item.get("claim_id") for item in data.get("items", [])}
        self.assertIn("c-alias-user-cn", found)
        self.assertIn("c-alias-user-en", found)
        self.assertNotIn("c-alias-persona", found)
        self.assertNotIn("c-alias-viewer", found)
        # 2. master 用旧拼写查 用户：同样命中两条
        status, data = self.call(
            "GET",
            "/v1/search?q="
            + urllib.parse.quote("别名测试")
            + "&person="
            + urllib.parse.quote("用户"),
        )
        self.assertEqual(status, 200)
        found = {item.get("claim_id") for item in data.get("items", [])}
        self.assertIn("c-alias-user-cn", found)
        self.assertIn("c-alias-user-en", found)
        # 3. master 查 persona：只命中 persona
        status, data = self.call(
            "GET",
            "/v1/search?q=" + urllib.parse.quote("别名测试") + "&person=persona",
        )
        self.assertEqual(status, 200)
        found = {item.get("claim_id") for item in data.get("items", [])}
        self.assertEqual(found, {"c-alias-persona"})
        # 4. scoped token 只允许 user：recall 必须命中 用户 记录（不漏），且不泄露 persona
        scoped_token = "scoped-alias-test"
        scoped_hash = hashlib.sha256(scoped_token.encode()).hexdigest()
        scopes_file = self.root / "system" / "api-token-scopes.json"
        scopes_file.write_text(json.dumps({scoped_hash: ["user"]}), encoding="utf-8")
        headers = {"Authorization": f"Bearer {scoped_token}"}
        status, data = self.call(
            "POST",
            "/v1/recall",
            {"prompt": "别名测试喜欢喝茶", "budget": 1200},
            headers=headers,
        )
        self.assertEqual(status, 200)
        found = {item.get("claim_id") for item in data.get("items", [])}
        self.assertIn("c-alias-user-cn", found)
        self.assertNotIn("c-alias-persona", found)
        self.assertNotIn("别名测试鲸鱼娘", data.get("text", ""))

        restore_projects(self.root, self.svc, original)
        scopes_file.unlink()

    def test_backup_restore_roundtrip(self):
        """评审最终验收第二阶段：隔离副本备份 → 删除 → 恢复 → 记录一致；
        服务重启后记忆恢复。"""
        import shutil
        import sqlite3

        # 用独立临时目录建索引，不碰共享测试服务
        tmp2 = tempfile.TemporaryDirectory()
        base2 = Path(tmp2.name)
        root2 = base2 / "memories"
        seed(root2)
        db2 = base2 / "index.db"
        svc2 = api.MemoryService(root2, db2)
        meta = svc2.ensure_index()
        self.assertGreater(meta.get("records", 0), 0)
        n_before = meta["records"]
        # 备份
        backup = base2 / "index.db.bak"
        svc2.index.close()
        shutil.copyfile(db2, backup)
        # 模拟损坏：删掉原库
        db2.unlink()
        self.assertFalse(db2.exists())
        # 恢复
        shutil.copyfile(backup, db2)
        # 重启：重新打开服务，记录必须一致
        svc3 = api.MemoryService(root2, db2)
        meta3 = svc3.ensure_index()
        self.assertEqual(meta3.get("records"), n_before)
        # 直接查库验证内容
        con = sqlite3.connect(str(db2))
        row = con.execute("SELECT COUNT(*) FROM records WHERE claim_id = 'c-api0'").fetchone()
        self.assertEqual(row[0], 1)
        con.close()
        svc3.index.close()
        tmp2.cleanup()

    def test_recall_budget_boundaries(self):
        """评审 P1：budget 边界——0 真正关闭召回，负数钳到 0，极大值不崩。"""
        # budget=0：items 和 text 都为空
        status, data = self.call("POST", "/v1/recall", {"prompt": "记忆系统", "budget": 0})
        self.assertEqual(status, 200)
        self.assertEqual(data.get("items"), [])
        self.assertEqual(data.get("text"), "")
        # budget=-5：同样为空（不崩、不注入）
        status, data = self.call("POST", "/v1/recall", {"prompt": "记忆系统", "budget": -5})
        self.assertEqual(status, 200)
        self.assertEqual(data.get("items"), [])
        self.assertEqual(data.get("text"), "")
        # budget=10**9：正常返回，不崩
        status, data = self.call("POST", "/v1/recall", {"prompt": "记忆系统", "budget": 10**9})
        self.assertEqual(status, 200)
        self.assertGreater(len(data.get("items", [])), 0)
        # 不传 budget：默认 1200，正常返回
        status, data = self.call("POST", "/v1/recall", {"prompt": "记忆系统"})
        self.assertEqual(status, 200)
        self.assertGreater(len(data.get("items", [])), 0)

    def test_history_returns_subject(self):
        """评审 P1：history 必须返回 subject 字段，受限 Token 才能看到授权主体的历史。"""
        import hashlib

        # history 不带 Token（master）：应含 subject 字段
        status, data = self.call("GET", "/v1/history/c-api0")
        self.assertEqual(status, 200)
        items = data.get("items", [])
        self.assertGreater(len(items), 0)
        for item in items:
            self.assertIn("subject", item)
            self.assertEqual(item.get("subject"), "用户")
        # scoped token（只允许 用户）：应能看到 用户 的历史
        scoped_token = "scoped-history-test"
        scoped_hash = hashlib.sha256(scoped_token.encode()).hexdigest()
        scopes_file = self.root / "system" / "api-token-scopes.json"
        scopes_file.write_text(json.dumps({scoped_hash: ["用户"]}), encoding="utf-8")
        headers = {"Authorization": f"Bearer {scoped_token}"}
        status, data = self.call("GET", "/v1/history/c-api0", headers=headers)
        self.assertEqual(status, 200)
        items = data.get("items", [])
        self.assertGreater(len(items), 0)
        for item in items:
            self.assertEqual(item.get("subject"), "用户")
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
