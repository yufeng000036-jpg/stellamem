"""Step 4 tests: bridge review GUI (loopback only).

Synthetic placeholder data only. No real requests, no network beyond
127.0.0.1, no terminal path removed.
"""

import sys
import unittest
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

import bridge as B
import bridge_gui as BG


def _store():
    store = B.BridgeStore()
    store.create("user", "persona", "relationship",
                 {"a": "测试用户A", "b": "测试人格B", "c": "朋友"})
    return store


def _post(url, fields):
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, resp.read().decode("utf-8")


class BridgeGuiTests(unittest.TestCase):
    def test_queue_shows_full_text_and_ttl_note(self):
        store = _store()
        page = BG.render_queue_html(store)
        self.assertIn("测试用户A 是 测试人格B 的 朋友", page)
        self.assertIn("低置信猜测", page)
        self.assertIn("127.0.0.1", "127.0.0.1")  # loopback-only design, see server test

    def test_non_loopback_host_refused(self):
        with self.assertRaises(ValueError):
            BG.BridgeReviewServer(_store(), host="0.0.0.0")

    def test_http_approve_flow(self):
        store = _store()
        with BG.BridgeReviewServer(store) as srv:
            status, body = _post(srv.url + "/approve", {
                "id": "bridge-0001", "approver": "测试批准人",
                "confirmed_full_text": "1",
            })
            self.assertEqual(status, 200)
            self.assertIn("已批准", body)
        self.assertEqual(store.get("bridge-0001")["status"], "approved")

    def test_http_approve_without_full_text_fails(self):
        store = _store()
        with BG.BridgeReviewServer(store) as srv:
            try:
                _post(srv.url + "/approve", {"id": "bridge-0001", "approver": "x"})
                self.fail("expected HTTP 400")
            except urllib.error.HTTPError as exc:
                self.assertEqual(exc.code, 400)
        self.assertEqual(store.get("bridge-0001")["status"], "pending")

    def test_http_deny_flow(self):
        store = _store()
        with BG.BridgeReviewServer(store) as srv:
            status, _ = _post(srv.url + "/deny", {"id": "bridge-0001", "approver": "x"})
            self.assertEqual(status, 200)
        self.assertEqual(store.get("bridge-0001")["status"], "denied")

    def test_empty_queue_page(self):
        page = BG.render_queue_html(B.BridgeStore())
        self.assertIn("暂无待审批请求", page)


if __name__ == "__main__":
    unittest.main()
