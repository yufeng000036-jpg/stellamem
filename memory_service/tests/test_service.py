import concurrent.futures
import json
import pathlib
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from store import Store, Principal, Denied, Conflict, Missing, Invalid
from server import Service


class MemoryTests(unittest.TestCase):
    def setUp(self):
        # A fresh checkout has no ignored work/ directory. Let tempfile use
        # the OS temp root so CI and read-only source checkouts work too.
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.s = Store(self.root / "test.sqlite3")
        self.p = Principal()

    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()

    def add(self, **kw):
        d = {
            "title": "Ocean project",
            "content": "Durable ocean research memory",
            "source": {"reference": "synthetic:test"},
        }
        d.update(kw)
        return self.s.create(self.p, d)

    def active(self, **kw):
        r = self.add(**kw)
        return self.s.review(self.p, r["id"], "active", r["version"])

    def test_pending_not_recalled(self):
        self.add()
        self.assertEqual(self.s.context(self.p, "ocean")["memories"], [])

    def test_create_reload_durable(self):
        r = self.active()
        self.s.close()
        self.s = Store(self.root / "test.sqlite3")
        self.assertEqual(self.s.get(self.p, r["id"])["content"], r["content"])

    def test_public_cannot_read_owner(self):
        r = self.active()
        p = Principal(role="public")
        self.assertEqual(self.s.search(p, "ocean")["items"], [])
        with self.assertRaises(Missing):
            self.s.get(p, r["id"])
        self.assertEqual(self.s.stats(p)["total"], 0)

    def test_participants_do_not_share(self):
        a = Principal(actor="a", role="participant", conversation="qq:a", channel="qq")
        b = Principal(actor="b", role="participant", conversation="qq:b", channel="qq")
        r = self.s.create(
            a, {"title": "Secret", "content": "private ocean", "source": {"reference": "a:1"}}
        )
        r = self.s.review(self.p, r["id"], "active", 1)
        self.assertEqual(len(self.s.context(a, "ocean")["memories"]), 1)
        self.assertEqual(self.s.context(b, "ocean")["memories"], [])

    def test_same_person_wrong_conversation(self):
        a = Principal(actor="a", role="participant", conversation="qq:a")
        b = Principal(actor="a", role="participant", conversation="bili:a")
        r = self.s.create(
            a, {"title": "Ocean", "content": "private ocean", "source": {"reference": "a"}}
        )
        self.s.review(self.p, r["id"], "active", 1)
        self.assertEqual(self.s.search(b, "ocean")["items"], [])

    def test_tenant_agent_isolation(self):
        r = self.active(scope="public")
        for p in [
            Principal(tenant="other"),
            Principal(agent="other"),
            Principal(role="public", tenant="other"),
        ]:
            with self.assertRaises(Missing):
                self.s.get(p, r["id"])

    def test_public_can_read_approved_public(self):
        _ = self.active(scope="public")
        self.assertEqual(len(self.s.context(Principal(role="public"), "ocean")["memories"]), 1)

    def test_history_owner_only_even_after_publish(self):
        r = self.active()
        r = self.s.update(self.p, r["id"], {"scope": "public"}, r["version"])
        self.s.review(self.p, r["id"], "active", r["version"])
        with self.assertRaises(Denied):
            self.s.history(Principal(role="public"), r["id"])

    def test_quarantine_every_read_path(self):
        r = self.active(scope="public")
        self.s.review(self.p, r["id"], "quarantined", r["version"])
        p = Principal(role="public")
        self.assertEqual(self.s.context(p, "ocean")["memories"], [])
        self.assertEqual(self.s.stats(p)["total"], 0)
        with self.assertRaises(Missing):
            self.s.get(p, r["id"])

    def test_update_invalidates_approval(self):
        r = self.active(scope="public")
        r = self.s.update(self.p, r["id"], {"content": "new value"}, r["version"])
        self.assertEqual(r["status"], "pending")
        self.assertEqual(self.s.search(Principal(role="public"))["items"], [])

    def test_version_conflict(self):
        r = self.add()
        self.s.update(self.p, r["id"], {"title": "changed"}, 1)
        with self.assertRaises(Conflict):
            self.s.update(self.p, r["id"], {"title": "overwrite"}, 1)

    def test_restore_creates_version_and_pending(self):
        r = self.add()
        self.s.update(self.p, r["id"], {"content": "changed"}, 1)
        r = self.s.restore_version(self.p, r["id"], 1, 2)
        self.assertEqual(r["version"], 3)
        self.assertEqual(r["status"], "pending")
        self.assertEqual(r["content"], "Durable ocean research memory")

    def test_delete_removes_history_and_index(self):
        r = self.active()
        self.s.delete(self.p, r["id"], r["version"])
        self.assertEqual(self.s.search(self.p, "ocean")["items"], [])
        self.assertEqual(self.s.db.execute("SELECT count(*) FROM revisions").fetchone()[0], 0)
        self.assertEqual(self.s.db.execute("SELECT count(*) FROM memory_fts").fetchone()[0], 0)

    def test_expiry_and_future_not_recalled(self):
        self.active(expires_at="2000-01-01T00:00:00Z")
        self.active(valid_from="2099-01-01T00:00:00Z")
        self.assertEqual(self.s.context(self.p, "ocean")["memories"], [])

    def test_default_does_not_expire(self):
        self.active(occurred_at="2000-01-01T00:00:00Z", valid_from="2000-01-01T00:00:00Z")
        self.assertEqual(len(self.s.context(self.p, "ocean")["memories"]), 1)

    def test_budget_bound(self):
        self.active()
        self.assertEqual(self.s.context(self.p, "ocean", 10)["memories"], [])
        self.assertLessEqual(self.s.context(self.p, "ocean", 500)["characters"], 500)

    def test_chinese_search(self):
        r = self.active(
            title="\u9879\u76ee\u8ba1\u5212",
            content="\u6211\u559c\u6b22\u6df1\u8272\u754c\u9762\uff0c\u957f\u671f\u7528SQLite\u4fdd\u5b58\u8bb0\u5fc6",
        )
        self.assertEqual(self.s.search(self.p, "\u6df1\u8272")["items"][0]["id"], r["id"])

    def test_unknown_search_empty(self):
        self.active()
        self.assertEqual(self.s.context(self.p, "zzzzunrelated")["memories"], [])

    def test_credential_gate(self):
        with self.assertRaises(Invalid):
            self.add(content="sk-" + "x" * 30)

    def test_clients_cannot_publish_or_change_identity(self):
        p = Principal(role="participant", actor="a", conversation="a")
        for extra in [{"scope": "public"}, {"kind": "identity"}]:
            with self.assertRaises(Denied):
                self.s.create(p, dict(title="a", content="b", **extra))

    def test_receipt_not_model_assertion(self):
        p = Principal(role="participant", actor="a", conversation="a")
        with self.assertRaises(Denied):
            self.s.event(
                p,
                {
                    "event_id": "1",
                    "event_type": "confirmed",
                    "action_id": "x",
                    "receipt_id": "r",
                    "receipt_reference": "real",
                },
            )
        with self.assertRaises(Denied):
            self.s.event(self.p, {"event_id": "1", "event_type": "confirmed", "action_id": "x"})

    def test_event_idempotency_and_state(self):
        d = {"event_id": "1", "event_type": "attempted", "action_id": "x"}
        self.assertFalse(self.s.event(self.p, d)["duplicate"])
        self.assertTrue(self.s.event(self.p, d)["duplicate"])
        self.s.event(
            self.p,
            {
                "event_id": "2",
                "event_type": "confirmed",
                "action_id": "x",
                "receipt_id": "r",
                "receipt_reference": "platform:1",
            },
        )
        with self.assertRaises(Conflict):
            self.s.event(self.p, {"event_id": "3", "event_type": "attempted", "action_id": "x"})
        self.assertEqual(len(self.s.events(self.p)), 2)

    def test_payload_reuse_conflict(self):
        self.s.event(self.p, {"event_id": "a", "event_type": "observed"})
        with self.assertRaises(Conflict):
            self.s.event(self.p, {"event_id": "a", "event_type": "corrected"})

    def test_action_route_cannot_change(self):
        self.s.event(self.p, {"event_id": "1", "event_type": "attempted", "action_id": "x"})
        q = Principal(actor="q", role="participant", conversation="q", can_confirm=True)
        with self.assertRaises(Denied):
            self.s.event(
                q,
                {
                    "event_id": "2",
                    "event_type": "confirmed",
                    "action_id": "x",
                    "receipt_id": "r",
                    "receipt_reference": "p",
                },
            )

    def test_client_revocation(self):
        r = self.s.issue_client(self.p, "qq", "participant", "a", "qq:a", "qq")
        self.assertEqual(self.s.authenticate(r["token"]).conversation, "qq:a")
        self.s.revoke(self.p, "qq")
        with self.assertRaises(Denied):
            self.s.authenticate(r["token"])

    def test_backup_restore(self):
        r = self.active()
        b = self.s.backup(self.p, self.root / "backup")
        s = Store(b["path"])
        self.assertEqual(s.get(self.p, r["id"])["content"], r["content"])
        self.assertEqual(s.db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        s.close()

    def test_transaction_failure_is_atomic(self):
        with self.assertRaises(Invalid):
            with self.s.transaction():
                self.s._insert(self.p, {"title": "a", "content": "b"})
                self.s._insert(self.p, {"title": "", "content": "b"})
        self.assertEqual(self.s.stats(self.p)["total"], 0)
        self.assertEqual(self.s.db.execute("SELECT count(*) FROM memory_fts").fetchone()[0], 0)

    def test_concurrent_no_lost_writes(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(lambda i: self.add(title=str(i)), range(80)))
        self.assertEqual(self.s.stats(self.p)["total"], 80)

    def test_cursor_pagination(self):
        for i in range(25):
            self.add(title=str(i))
        first = self.s.search(self.p, limit=10)
        second = self.s.search(self.p, limit=10, before=first["next_cursor"])
        self.assertEqual(len({r["id"] for r in first["items"] + second["items"]}), 20)

    def test_export_stream_valid(self):
        self.add()
        r = self.s.export(self.p, self.root / "exports")
        self.assertEqual(len(pathlib.Path(r["path"]).read_text(encoding="utf-8").splitlines()), 1)


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Service(self.tmp.name, 0)
        self.thread = threading.Thread(target=self.s.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:" + str(self.s.server_port)
        self.op = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self.s.shutdown()
        self.s.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def req(self, path, body=None, headers=None):
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers or {},
        )
        try:
            r = self.op.open(req, timeout=5)
            return r.status, json.load(r), r.headers
        except urllib.error.HTTPError as e:
            return e.code, json.load(e), e.headers

    def session(self):
        _, d, h = self.req("/session")
        return {
            "Cookie": h["Set-Cookie"].split(";")[0],
            "X-CSRF-Token": d["csrf"],
            "Content-Type": "application/json",
        }

    def test_unauthenticated_reads_denied(self):
        self.assertEqual(self.req("/v2/memories")[0], 403)

    def test_host_and_origin_denied(self):
        self.assertEqual(self.req("/session", headers={"Host": "evil.test"})[0], 403)
        self.assertEqual(self.req("/session", headers={"Origin": "http://evil.test"})[0], 403)
        self.assertEqual(self.req("/session", headers={"Sec-Fetch-Site": "cross-site"})[0], 403)

    def test_csrf_required(self):
        h = self.session()
        h.pop("X-CSRF-Token")
        self.assertEqual(self.req("/v2/memories", {"title": "a", "content": "b"}, h)[0], 403)

    def test_authenticated_crud(self):
        h = self.session()
        code, r, _ = self.req(
            "/v2/memories", {"title": "a", "content": "b", "source": {"reference": "manual"}}, h
        )
        self.assertEqual(code, 201)
        self.assertEqual(self.req("/v2/memories/" + r["id"], headers=h)[0], 200)
        self.assertEqual(
            self.req("/v2/memories/" + r["id"] + "/review", {"status": "active", "version": 1}, h)[
                0
            ],
            200,
        )

    def test_no_path_traversal(self):
        self.assertEqual(self.req("/../store.py")[0], 404)

    def test_invalid_body_no_success(self):
        h = self.session()
        self.assertEqual(self.req("/v2/memories", {"title": "a"}, h)[0], 400)


class SqliteRuntimeTests(unittest.TestCase):
    """The store must run on SQLite >= 3.42 (FTS5 secure-delete) on every
    platform, including CPython 3.10 bundles that ship SQLite 3.40.1."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.s = Store(self.root / "test.sqlite3")
        self.p = Principal()

    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()

    def test_store_uses_sqlite_with_fts5_secure_delete(self):
        import store

        self.assertGreaterEqual(store.sqlite3.sqlite_version_info, (3, 42, 0))
        r = self.s.create(
            self.p,
            {
                "title": "Ocean project",
                "content": "Durable ocean research memory",
                "source": {"reference": "synthetic:test"},
            },
        )
        r = self.s.review(self.p, r["id"], "active", r["version"])
        self.assertEqual(len(self.s.search(self.p, "ocean")["items"]), 1)
        self.s.delete(self.p, r["id"], r["version"])
        self.assertEqual(self.s.search(self.p, "ocean")["items"], [])
        self.assertEqual(self.s.db.execute("SELECT count(*) FROM memory_fts").fetchone()[0], 0)

    def test_select_sqlite_prefers_new_stdlib(self):
        import store

        self.assertIs(store._select_sqlite(), store.sqlite3)

    def test_select_sqlite_falls_back_to_pysqlite3(self):
        import sqlite3
        import types
        from unittest import mock

        import store

        fake = types.ModuleType("pysqlite3")
        fake.sqlite_version_info = (3, 45, 0)
        with (
            mock.patch.object(sqlite3, "sqlite_version_info", (3, 40, 1)),
            mock.patch.dict(sys.modules, {"pysqlite3": fake}),
        ):
            self.assertIs(store._select_sqlite(), fake)

    def test_select_sqlite_keeps_stdlib_without_newer_module(self):
        import sqlite3
        from unittest import mock

        import store

        with (
            mock.patch.object(sqlite3, "sqlite_version_info", (3, 40, 1)),
            mock.patch.dict(sys.modules, {"pysqlite3": None}),
        ):
            self.assertIs(store._select_sqlite(), sqlite3)

    def test_select_sqlite_ignores_too_old_pysqlite3(self):
        import sqlite3
        import types
        from unittest import mock

        import store

        fake = types.ModuleType("pysqlite3")
        fake.sqlite_version_info = (3, 40, 1)
        with (
            mock.patch.object(sqlite3, "sqlite_version_info", (3, 40, 1)),
            mock.patch.dict(sys.modules, {"pysqlite3": fake}),
        ):
            self.assertIs(store._select_sqlite(), sqlite3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
