"""Step 4 tests: bridge review GUI (loopback only + token + origin checks).

Synthetic placeholder data only. No real requests, no network beyond
127.0.0.1, no terminal path removed. Original 6 tests are kept, adapted
to the token rule; new tests cover the token and Origin hardening.
"""

import sys
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

import bridge as B
import bridge_gui as BG

TOKEN = "test-token-123"


def _store():
    store = B.BridgeStore()
    store.create(
        "user", "persona", "relationship", {"a": "测试用户A", "b": "测试人格B", "c": "朋友"}
    )
    return store


def _post(url, fields, headers=None):
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, resp.read().decode("utf-8")


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, resp.read().decode("utf-8")


def _server(store):
    return BG.BridgeReviewServer(store, token=TOKEN)


class BridgeGuiTests(unittest.TestCase):
    def test_queue_shows_full_text_and_ttl_note(self):
        store = _store()
        page = BG.render_queue_html(store)
        self.assertIn("测试用户A 是 测试人格B 的 朋友", page)
        self.assertIn("低置信猜测", page)
        self.assertIn("127.0.0.1", "127.0.0.1")  # loopback-only design, see server test

    def test_non_loopback_host_refused(self):
        with self.assertRaises(ValueError):
            BG.BridgeReviewServer(_store(), host="0.0.0.0", token=TOKEN)

    def test_http_approve_flow(self):
        store = _store()
        with _server(store) as srv:
            status, body = _post(
                srv.url + "/approve",
                {
                    "token": TOKEN,
                    "id": "bridge-0001",
                    "approver": "测试批准人",
                    "confirmed_full_text": "1",
                },
            )
            self.assertEqual(status, 200)
            self.assertIn("已批准", body)
        self.assertEqual(store.get("bridge-0001")["status"], "approved")

    def test_http_approve_without_full_text_fails(self):
        store = _store()
        with _server(store) as srv:
            try:
                _post(srv.url + "/approve", {"token": TOKEN, "id": "bridge-0001", "approver": "x"})
                self.fail("expected HTTP 400")
            except urllib.error.HTTPError as exc:
                self.assertEqual(exc.code, 400)
        self.assertEqual(store.get("bridge-0001")["status"], "pending")

    def test_http_deny_flow(self):
        store = _store()
        with _server(store) as srv:
            status, _ = _post(
                srv.url + "/deny", {"token": TOKEN, "id": "bridge-0001", "approver": "x"}
            )
            self.assertEqual(status, 200)
        self.assertEqual(store.get("bridge-0001")["status"], "denied")

    def test_empty_queue_page(self):
        page = BG.render_queue_html(B.BridgeStore())
        self.assertIn("暂无待审批请求", page)

    # --- Token / Origin hardening (new) ---

    def test_http_get_without_token_is_401_and_hides_full_text(self):
        store = _store()
        with _server(store) as srv:
            try:
                _get(srv.url + "/")
                self.fail("expected HTTP 401")
            except urllib.error.HTTPError as exc:
                self.assertEqual(exc.code, 401)
                self.assertNotIn("测试用户A 是 测试人格B 的 朋友", exc.read().decode("utf-8"))

    def test_http_get_with_token_shows_queue(self):
        store = _store()
        with _server(store) as srv:
            status, body = _get(srv.url + "/?token=" + TOKEN)
            self.assertEqual(status, 200)
            self.assertIn("测试用户A 是 测试人格B 的 朋友", body)
            self.assertIn("name='token'", body)

    def test_http_approve_without_token_is_401(self):
        store = _store()
        with _server(store) as srv:
            try:
                _post(
                    srv.url + "/approve",
                    {"id": "bridge-0001", "approver": "x", "confirmed_full_text": "1"},
                )
                self.fail("expected HTTP 401")
            except urllib.error.HTTPError as exc:
                self.assertEqual(exc.code, 401)
        self.assertEqual(store.get("bridge-0001")["status"], "pending")

    def test_http_approve_wrong_token_is_401(self):
        store = _store()
        with _server(store) as srv:
            try:
                _post(
                    srv.url + "/approve",
                    {
                        "token": "wrong",
                        "id": "bridge-0001",
                        "approver": "x",
                        "confirmed_full_text": "1",
                    },
                )
                self.fail("expected HTTP 401")
            except urllib.error.HTTPError as exc:
                self.assertEqual(exc.code, 401)
        self.assertEqual(store.get("bridge-0001")["status"], "pending")

    def test_http_approve_foreign_origin_is_403(self):
        store = _store()
        with _server(store) as srv:
            try:
                _post(
                    srv.url + "/approve",
                    {
                        "token": TOKEN,
                        "id": "bridge-0001",
                        "approver": "x",
                        "confirmed_full_text": "1",
                    },
                    headers={"Origin": "https://evil.example"},
                )
                self.fail("expected HTTP 403")
            except urllib.error.HTTPError as exc:
                self.assertEqual(exc.code, 403)
        self.assertEqual(store.get("bridge-0001")["status"], "pending")

    def test_http_approve_loopback_origin_succeeds(self):
        store = _store()
        with _server(store) as srv:
            status, _ = _post(
                srv.url + "/approve",
                {"token": TOKEN, "id": "bridge-0001", "approver": "x", "confirmed_full_text": "1"},
                headers={"Origin": "http://127.0.0.1:9999"},
            )
            self.assertEqual(status, 200)
        self.assertEqual(store.get("bridge-0001")["status"], "approved")

    def test_generated_token_is_random_and_nonempty(self):
        srv = BG.BridgeReviewServer(_store())
        self.assertTrue(srv.token)
        self.assertNotEqual(srv.token, TOKEN)
        self.assertGreaterEqual(len(srv.token), 32)


if __name__ == "__main__":
    unittest.main()
