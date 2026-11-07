"""Fault injection for stale/revoked reads, incomplete builds and pending exposure."""

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import urllib.parse
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import api
import indexer
import test_phase3_api as fixtures
from adapters.mcp.stellamem_mcp_server import StellamemMCPServer
from storage import load_records, render_records


class ReadSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "memory"
        fixtures.seed(self.root)
        self.db = Path(self.tmp.name) / "index.db"
        self.httpd, self.svc = api.make_server(self.root, self.db, port=0)
        self.svc.ensure_index()
        self.addCleanup(self.svc.index.close)
        self.addCleanup(self.httpd.server_close)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.shutdown)
        self.token = self.svc.ensure_token()
        self.target = self.root / "long-term/projects.md"

    def request(self, path, payload=None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.httpd.server_port}{path}",
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"},
        )
        try:
            response = urllib.request.urlopen(req, timeout=10)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, json.loads(response.read())

    def revoke(self):
        prefix, records = load_records(self.target)
        for record in records:
            record["status"] = "rejected"
        self.target.write_text(render_records(prefix, records), encoding="utf-8")

    def test_http_search_excludes_pending(self):
        prefix, records = load_records(self.target)
        records[-1]["value"] = records[0]["value"]
        self.target.write_text(render_records(prefix, records), encoding="utf-8")
        status, body = self.request("/v1/search?q=" + urllib.parse.quote("记忆系统"))
        self.assertEqual(status, 200)
        self.assertTrue(body["items"])
        self.assertTrue(all(r["status"] == "active" for r in body["items"]))

    def test_http_revocation_refresh_failure_returns_503_without_content(self):
        self.revoke()
        with patch.object(indexer.Index, "build", side_effect=OSError("injected")):
            for path, payload in [
                ("/v1/search?q=", None),
                ("/v1/recall", {"query": "记忆系统"}),
                ("/health", None),
            ]:
                status, body = self.request(path, payload)
                self.assertEqual(status, 503)
                self.assertNotIn("星忆", json.dumps(body, ensure_ascii=False))
        # The failed background attempt cannot poison recovery.
        self.svc.ensure_index(force=True)
        status, body = self.request("/v1/search?q=")
        self.assertEqual(status, 200)
        self.assertEqual(body["items"], [])

    def test_reader_waits_for_inflight_rebuild(self):
        self.revoke()
        entered, release, returned = threading.Event(), threading.Event(), threading.Event()
        original = indexer.Index.build
        errors = []

        def slow_build(idx, *args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test rebuild timed out")
            return original(idx, *args, **kwargs)

        def read():
            try:
                self.svc.ensure_readable_index()
            except Exception as exc:
                errors.append(exc)
            finally:
                returned.set()

        with patch.object(indexer.Index, "build", slow_build):
            self.svc.ensure_index()
            self.assertTrue(entered.wait(5))
            reader = threading.Thread(target=read)
            reader.start()
            try:
                self.assertFalse(returned.wait(0.1))
            finally:
                release.set()
                reader.join(5)
        self.assertTrue(returned.is_set())
        self.assertEqual(errors, [])
        self.assertFalse(self.svc.index.is_stale(self.root))

    def test_mcp_revocation_refresh_failure_is_closed(self):
        from compiler.indexer import Index

        server = StellamemMCPServer(self.root, self.db, allowed_subjects={"user"})
        self.revoke()
        with patch.object(Index, "build", side_effect=OSError("injected")):
            result = server._call_tool("stellamem_recall", {"subject": "user", "query": "记忆系统"})
        self.assertEqual(result["status"], "fail_closed")
        self.assertEqual(result["items"], [])

    def test_mcp_missing_root_metadata_is_closed(self):
        self.svc.index.con.execute("DELETE FROM meta WHERE key='root'")
        self.svc.index.con.commit()
        server = StellamemMCPServer(self.root, self.db, allowed_subjects={"user"})
        result = server._call_tool("stellamem_recall", {"subject": "user", "query": "记忆系统"})
        self.assertEqual(result["status"], "fail_closed")

    def test_corrupt_managed_file_fails_and_rolls_back(self):
        before = self.svc.index.stats()
        original = self.target.read_text(encoding="utf-8")
        self.target.write_text(original + "\n```stellamem\n{broken}\n```\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "index incomplete"):
            self.svc.index.build(self.root)
        self.assertEqual(self.svc.index.stats()["records"], before["records"])
        self.assertTrue(self.svc.index.is_stale(self.root))
        status, _ = self.request("/health")
        self.assertEqual(status, 503)

    def test_source_changes_during_build_are_not_marked_fresh(self):
        before = self.svc.index.stats()["records"]
        real_load = load_records

        def mutate(path):
            result = real_load(path)
            if Path(path) == self.target:
                self.revoke()
            return result

        with patch("storage.load_records", side_effect=mutate):
            with self.assertRaisesRegex(RuntimeError, "sources changed"):
                self.svc.index.build(self.root)
        self.assertEqual(self.svc.index.stats()["records"], before)
        self.assertTrue(self.svc.index.is_stale(self.root))
