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


class BridgeGuiBoundaryTests(unittest.TestCase):
    """Second reliability round: forged / malformed requests must be
    rejected AND must leave the original request untouched. No test
    here removes an old test; these are additions only."""

    def _post_error(self, srv, path, fields, headers=None):
        try:
            _post(srv.url + path, fields, headers=headers)
            self.fail("expected HTTP error for " + path)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8")

    def test_deny_and_withdraw_without_token_are_401_and_unchanged(self):
        store = _store()
        with _server(store) as srv:
            for path in ("/deny", "/withdraw"):
                code, body = self._post_error(
                    srv, path, {"id": "bridge-0001", "approver": "x"}
                )
                self.assertEqual(code, 401)
                self.assertNotIn(TOKEN, body)
        self.assertEqual(store.get("bridge-0001")["status"], "pending")

    def test_empty_token_is_401(self):
        store = _store()
        with _server(store) as srv:
            code, body = self._post_error(
                srv,
                "/approve",
                {
                    "token": "",
                    "id": "bridge-0001",
                    "approver": "x",
                    "confirmed_full_text": "1",
                },
            )
            self.assertEqual(code, 401)
            self.assertNotIn(TOKEN, body)
        self.assertEqual(store.get("bridge-0001")["status"], "pending")

    def test_wrong_token_on_deny_is_401_and_unchanged(self):
        store = _store()
        with _server(store) as srv:
            code, _ = self._post_error(
                srv,
                "/deny",
                {"token": "wrong", "id": "bridge-0001", "approver": "x"},
            )
            self.assertEqual(code, 401)
        self.assertEqual(store.get("bridge-0001")["status"], "pending")

    def test_foreign_referer_is_403_even_without_origin(self):
        store = _store()
        with _server(store) as srv:
            code, body = self._post_error(
                srv,
                "/approve",
                {
                    "token": TOKEN,
                    "id": "bridge-0001",
                    "approver": "x",
                    "confirmed_full_text": "1",
                },
                headers={"Referer": "https://evil.example/"},
            )
            self.assertEqual(code, 403)
            self.assertNotIn(TOKEN, body)
        self.assertEqual(store.get("bridge-0001")["status"], "pending")

    def test_foreign_origin_on_deny_is_403_and_unchanged(self):
        store = _store()
        with _server(store) as srv:
            code, _ = self._post_error(
                srv,
                "/deny",
                {"token": TOKEN, "id": "bridge-0001", "approver": "x"},
                headers={"Origin": "https://evil.example"},
            )
            self.assertEqual(code, 403)
        self.assertEqual(store.get("bridge-0001")["status"], "pending")

    def test_repeat_approval_is_400_and_keeps_first_approver(self):
        store = _store()
        with _server(store) as srv:
            status, _ = _post(
                srv.url + "/approve",
                {
                    "token": TOKEN,
                    "id": "bridge-0001",
                    "approver": "first",
                    "confirmed_full_text": "1",
                },
            )
            self.assertEqual(status, 200)
            code, _ = self._post_error(
                srv,
                "/approve",
                {
                    "token": TOKEN,
                    "id": "bridge-0001",
                    "approver": "second",
                    "confirmed_full_text": "1",
                },
            )
            self.assertEqual(code, 400)
        record = store.get("bridge-0001")
        self.assertEqual(record["status"], "approved")
        self.assertEqual(record["approver"], "first")

    def test_repeat_deny_after_approve_is_400(self):
        store = _store()
        with _server(store) as srv:
            _post(
                srv.url + "/approve",
                {
                    "token": TOKEN,
                    "id": "bridge-0001",
                    "approver": "first",
                    "confirmed_full_text": "1",
                },
            )
            code, _ = self._post_error(
                srv,
                "/deny",
                {"token": TOKEN, "id": "bridge-0001", "approver": "second"},
            )
            self.assertEqual(code, 400)
        self.assertEqual(store.get("bridge-0001")["status"], "approved")

    def test_missing_id_and_missing_approver_are_400_and_unchanged(self):
        store = _store()
        with _server(store) as srv:
            for fields in (
                {"token": TOKEN, "approver": "x", "confirmed_full_text": "1"},
                {
                    "token": TOKEN,
                    "id": "bridge-0001",
                    "confirmed_full_text": "1",
                },
                {
                    "token": TOKEN,
                    "id": "bridge-0001",
                    "approver": "   ",
                    "confirmed_full_text": "1",
                },
                {
                    "token": TOKEN,
                    "id": "does-not-exist",
                    "approver": "x",
                    "confirmed_full_text": "1",
                },
            ):
                code, body = self._post_error(srv, "/approve", fields)
                self.assertEqual(code, 400)
                self.assertNotIn(TOKEN, body)
        self.assertEqual(store.get("bridge-0001")["status"], "pending")

    def test_error_responses_never_carry_the_token(self):
        store = _store()
        with _server(store) as srv:
            for path, fields in (
                ("/approve", {"id": "bridge-0001", "approver": "x"}),
                (
                    "/approve",
                    {
                        "token": "wrong",
                        "id": "bridge-0001",
                        "approver": "x",
                        "confirmed_full_text": "1",
                    },
                ),
                (
                    "/approve",
                    {
                        "token": TOKEN,
                        "id": "bridge-0001",
                        "approver": "x",
                        "confirmed_full_text": "1",
                    },
                ),
            ):
                headers = None
                if fields.get("token") == TOKEN:
                    headers = {"Origin": "https://evil.example"}
                code, body = self._post_error(srv, path, fields, headers=headers)
                self.assertIn(code, (401, 403))
                self.assertNotIn(TOKEN, body)
        self.assertEqual(store.get("bridge-0001")["status"], "pending")

    def test_unknown_path_with_valid_token_is_404_and_unchanged(self):
        store = _store()
        with _server(store) as srv:
            code, body = self._post_error(
                srv,
                "/approve-other",
                {"token": TOKEN, "id": "bridge-0001", "approver": "x"},
            )
            self.assertEqual(code, 404)
            self.assertNotIn(TOKEN, body)
        self.assertEqual(store.get("bridge-0001")["status"], "pending")


if __name__ == "__main__":
    unittest.main()
