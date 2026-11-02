"""Actual loopback requests with synthetic queue; rejected traffic is inert."""

import concurrent.futures
import http.client
import sys
import unittest
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))
from bridge import BridgeStore
from bridge_gui import BridgeReviewServer, make_handler


class BridgeTransportTests(unittest.TestCase):
    def setUp(self):
        self.store = BridgeStore()
        self.store.create("user", "persona", "result", {"x": "fictional", "result": "fixture"})
        self.server = BridgeReviewServer(self.store, token="test-transport-token")
        self.server.__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)

    def request(self, body, headers=None, path="/approve"):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        try:
            conn.request("POST", path, body=body, headers=headers or {})
            response = conn.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            conn.close()

    def fields(self, **override):
        fields = dict(
            token=self.server.token,
            id="bridge-0001",
            approver="simulated-human",
            confirmed_full_text="1",
        )
        fields.update(override)
        return urllib.parse.urlencode(fields).encode()

    def assert_inert(self, status, body, headers=None):
        before = len(self.store.audit)
        code, response, _ = self.request(body, headers)
        self.assertEqual(code, status)
        self.assertNotIn(self.server.token.encode(), response)
        self.assertEqual(self.store.get("bridge-0001")["status"], "pending")
        self.assertEqual(len(self.store.audit), before)

    def test_malformed_content_length(self):
        self.assert_inert(400, b"", {"Content-Length": "not-a-number"})

    def test_negative_content_length(self):
        self.assert_inert(400, b"", {"Content-Length": "-1"})

    def test_oversized_body_rejected_before_read(self):
        self.assert_inert(413, b"", {"Content-Length": "99999999"})

    def test_incomplete_body_times_out_without_mutation(self):
        self.assert_inert(408, b"x", {"Content-Length": "100"})
        code, _, _ = self.request(self.fields())
        self.assertEqual(code, 200, "server must remain usable after a stalled request")

    def test_invalid_utf8(self):
        self.assert_inert(400, b"\xff")

    def test_unicode_token_is_auth_failure(self):
        self.assert_inert(401, self.fields(token="中文令牌"))

    def test_duplicate_fields_are_rejected(self):
        self.assert_inert(400, self.fields() + b"&id=bridge-0001")

    def test_different_loopback_port_is_foreign_origin(self):
        self.assert_inert(403, self.fields(), {"Origin": "http://127.0.0.1:1"})

    def test_wrong_host_rejected(self):
        self.assert_inert(403, self.fields(), {"Host": "attacker.invalid"})

    def test_parallel_replay_approves_once(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.request(self.fields())[0], range(12)))
        self.assertEqual(results.count(200), 1)
        self.assertEqual(results.count(400), 11)
        self.assertEqual(sum(e["action"] == "approved" for e in self.store.audit), 1)

    def test_response_security_headers(self):
        code, _, headers = self.request(self.fields(), {"Origin": self.server.url})
        self.assertEqual(code, 200)
        self.assertEqual(headers.get("Referrer-Policy"), "no-referrer")
        self.assertEqual(headers.get("Cache-Control"), "no-store")

    def test_factory_cannot_disable_auth(self):
        with self.assertRaises(ValueError):
            make_handler(self.store)
