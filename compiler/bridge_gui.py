"""Bridge review GUI (Step 4): a local loopback-only review server.

Why: the bridge previously only had a terminal approval path. A human
approver should be able to see the FULL rendered text, the blacklist
flags, and the expiry in a readable page before approving, without
needing to read JSON in a terminal.

Safety, unchanged from bridge.py:
- Loopback only: the server binds 127.0.0.1 and refuses any other host.
  It is not a public service and must not be exposed.
- Default-deny is unchanged: approval still goes through
  ``BridgeStore.approve`` / ``deny`` / ``withdraw``, with the same
  requirements (non-empty approver, confirmed_full_text=True, and
  acknowledge_flag=True for blacklist-flagged requests).
- TTL is still 72 hours, still a LOW-CONFIDENCE GUESS pending real
  usage data. The GUI header says so explicitly; it must not be cited
  as a measured optimum.
- Terminal approval remains the fallback: nothing in this module
  removes or replaces BridgeStore's terminal path.

The server is stdlib-only (http.server). POST bodies are form-encoded:
approver, confirmed_full_text (on/true/1), acknowledge_flag (on/true/1).
"""

from __future__ import annotations

import html
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from bridge import BridgeStore, BRIDGE_TTL_HOURS
except Exception:  # pragma: no cover
    from compiler.bridge import BridgeStore, BRIDGE_TTL_HOURS  # type: ignore


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "on", "yes")


def render_queue_html(store: BridgeStore) -> str:
    """Render the pending queue as HTML (full text shown, no approval here)."""
    pending = store.list_pending()
    rows = []
    for req in pending:
        rows.append(
            "<section><h3>{rid} · {template} · {src} → {dst}</h3>"
            "<p><b>全文：</b>{text}</p>"
            "<p>黑名单标记：{flag}（{hits}）· 到期：{exp}</p>"
            "<form method='post' action='/approve'>"
            "<input type='hidden' name='id' value='{rid}'>"
            "批准人：<input name='approver' required> "
            "<label><input type='checkbox' name='confirmed_full_text' value='1'> 已看全文</label> "
            "<label><input type='checkbox' name='acknowledge_flag' value='1'> 已确认黑名单标记</label> "
            "<button>批准</button></form>"
            "<form method='post' action='/deny'>"
            "<input type='hidden' name='id' value='{rid}'>"
            "批准人：<input name='approver' required> "
            "<button>拒绝</button></form></section>".format(
                rid=html.escape(req["id"]),
                template=html.escape(req["template"]),
                src=html.escape(req["source_subject"]),
                dst=html.escape(req["target_subject"]),
                text=html.escape(req["full_text"]),
                flag="是" if req.get("blacklist_flagged") else "否",
                hits=", ".join(html.escape(str(h)) for h in req.get("blacklist_hits") or []),
                exp=html.escape(req["expires_at"]),
            )
        )
    body = "\n".join(rows) if rows else "<p>暂无待审批请求。</p>"
    return (
        "<!doctype html><html lang='zh'><head><meta charset='utf-8'>"
        "<title>桥接审批</title></head><body>"
        "<h1>桥接审批（本地环回，仅本机可访问）</h1>"
        f"<p>桥 TTL 仍为 {BRIDGE_TTL_HOURS} 小时，低置信猜测，需真实使用数据重定；"
        "真正边界是人工看全文批准，关键词黑名单只是噪声过滤。</p>"
        + body + "</body></html>"
    )


def make_handler(store: BridgeStore):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep test output quiet
            pass

        def _send(self, text: str, status: int = 200):
            data = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path in ("/", "/queue"):
                self._send(render_queue_html(store))
            else:
                self._send("<p>未找到</p>", status=404)

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            fields = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
            get = lambda k: (fields.get(k) or [""])[0]
            req_id = get("id")
            approver = get("approver")
            try:
                if self.path == "/approve":
                    store.approve(req_id, approver,
                                  confirmed_full_text=_truthy(get("confirmed_full_text")),
                                  acknowledge_flag=_truthy(get("acknowledge_flag")))
                    self._send("<p>已批准</p><p><a href='/'>回队列</a></p>")
                elif self.path == "/deny":
                    store.deny(req_id, approver)
                    self._send("<p>已拒绝</p><p><a href='/'>回队列</a></p>")
                elif self.path == "/withdraw":
                    store.withdraw(req_id, by=approver)
                    self._send("<p>已撤回</p><p><a href='/'>回队列</a></p>")
                else:
                    self._send("<p>未找到</p>", status=404)
            except (KeyError, ValueError) as exc:
                self._send(f"<p>失败：{html.escape(str(exc))}</p>", status=400)

    return Handler


class BridgeReviewServer:
    """Loopback-only review server; use as a context manager in tests."""

    def __init__(self, store: BridgeStore, host: str = "127.0.0.1", port: int = 0):
        if host != "127.0.0.1":
            raise ValueError("bridge GUI binds loopback only (127.0.0.1)")
        self.server = ThreadingHTTPServer((host, port), make_handler(store))
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        return False

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"
