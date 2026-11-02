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

Reliability hardening (this round, loopback alone is NOT enough):
- Per-server random token: ``BridgeReviewServer`` generates a
  ``secrets.token_urlsafe(32)`` token at startup (or accepts an
  explicit token, used by tests). Every HTTP request must present it:
  GET queue pages take ``?token=...``; POST forms carry a hidden
  ``token`` field. Without the correct token the server answers 401
  and, for GET, does NOT render the pending queue's full text.
- Origin/Referer check on POST: if an Origin or Referer header is
  present, its host must be 127.0.0.1 or localhost, otherwise the
  request is rejected with 403. This blocks a malicious web page on
  another origin from forging an approval POST in the approver's
  browser. A header-less request (curl, urllib, a same-origin form
  that sends no Origin) is still token-gated, so the token remains
  the primary check; the Origin check is a second layer, not a
  replacement for the token.
- The token is never logged (handler logging stays silent) and is
  never embedded in any page except the forms/queue links served to
  a request that already presented it.
- Approval requirements themselves are NOT weakened: approver,
  confirmed_full_text, and blacklist acknowledge_flag still apply.

The server is stdlib-only (http.server). POST bodies are form-encoded:
token, approver, confirmed_full_text (on/true/1),
acknowledge_flag (on/true/1).
"""

from __future__ import annotations

import hmac
import html
import secrets
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from bridge import BridgeStore, BRIDGE_TTL_HOURS
except Exception:  # pragma: no cover
    from compiler.bridge import BridgeStore, BRIDGE_TTL_HOURS  # type: ignore


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "on", "yes")


def render_queue_html(store: BridgeStore, token: str | None = None) -> str:
    """Render the pending queue as HTML (full text shown, no approval here).

    When ``token`` is given, every approval/deny form carries it as a
    hidden field. Rendering itself does not check the token: the HTTP
    handler gates GET before calling this function, so a direct
    in-process call is not an HTTP exposure.
    """
    pending = store.list_pending()
    token_field = (
        f"<input type='hidden' name='token' value='{html.escape(token)}'>" if token else ""
    )
    rows = []
    for req in pending:
        rows.append(
            "<section><h3>{rid} · {template} · {src} → {dst}</h3>"
            "<p><b>全文：</b>{text}</p>"
            "<p>黑名单标记：{flag}（{hits}）· 到期：{exp}</p>"
            "<form method='post' action='/approve'>"
            "<input type='hidden' name='id' value='{rid}'>{token_field}"
            "批准人：<input name='approver' required> "
            "<label><input type='checkbox' name='confirmed_full_text' value='1'> 已看全文</label> "
            "<label><input type='checkbox' name='acknowledge_flag' value='1'> 已确认黑名单标记</label> "
            "<button>批准</button></form>"
            "<form method='post' action='/deny'>"
            "<input type='hidden' name='id' value='{rid}'>{token_field}"
            "批准人：<input name='approver' required> "
            "<button>拒绝</button></form></section>".format(
                token_field=token_field,
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
        "真正边界是人工看全文批准，关键词黑名单只是噪声过滤。</p>" + body + "</body></html>"
    )


def _loopback_origin(value: str | None) -> bool:
    """True if an Origin/Referer header, when present, is loopback-only."""
    if not value:
        return True
    try:
        host = urllib.parse.urlparse(value).hostname
    except Exception:
        return False
    return host in ("127.0.0.1", "localhost", "::1")


def make_handler(store: BridgeStore, token: str | None = None):
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

        def _token_ok(self, presented: str | None) -> bool:
            if token is None:
                return True
            if not presented:
                return False
            return hmac.compare_digest(presented, token)

        def _queue_link(self) -> str:
            if token:
                return f"<p><a href='/?token={html.escape(token)}'>回队列</a></p>"
            return "<p><a href='/'>回队列</a></p>"

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path in ("/", "/queue"):
                query = urllib.parse.parse_qs(parsed.query)
                presented = (query.get("token") or [""])[0]
                if not self._token_ok(presented):
                    # Never render the pending queue's full text unauthenticated.
                    self._send("<p>需要令牌</p>", status=401)
                    return
                self._send(render_queue_html(store, token=token))
            else:
                self._send("<p>未找到</p>", status=404)

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            fields = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))

            def get(k):
                return (fields.get(k) or [""])[0]

            if not self._token_ok(get("token")):
                self._send("<p>需要令牌</p>", status=401)
                return
            if not _loopback_origin(self.headers.get("Origin")) or not _loopback_origin(
                self.headers.get("Referer")
            ):
                self._send("<p>来源不是环回地址，已拒绝</p>", status=403)
                return
            req_id = get("id")
            approver = get("approver")
            try:
                if self.path == "/approve":
                    store.approve(
                        req_id,
                        approver,
                        confirmed_full_text=_truthy(get("confirmed_full_text")),
                        acknowledge_flag=_truthy(get("acknowledge_flag")),
                    )
                    self._send("<p>已批准</p>" + self._queue_link())
                elif self.path == "/deny":
                    store.deny(req_id, approver)
                    self._send("<p>已拒绝</p>" + self._queue_link())
                elif self.path == "/withdraw":
                    store.withdraw(req_id, by=approver)
                    self._send("<p>已撤回</p>" + self._queue_link())
                else:
                    self._send("<p>未找到</p>", status=404)
            except (KeyError, ValueError) as exc:
                self._send(f"<p>失败：{html.escape(str(exc))}</p>", status=400)

    return Handler


class BridgeReviewServer:
    """Loopback-only review server; use as a context manager in tests.

    A random per-server token is generated unless ``token`` is given
    explicitly (tests pass an explicit token). The token is exposed as
    ``self.token`` so a legitimate local caller can present it; it is
    never logged.
    """

    def __init__(
        self, store: BridgeStore, host: str = "127.0.0.1", port: int = 0, token: str | None = None
    ):
        if host != "127.0.0.1":
            raise ValueError("bridge GUI binds loopback only (127.0.0.1)")
        self.token = token if token is not None else secrets.token_urlsafe(32)
        self.server = ThreadingHTTPServer((host, port), make_handler(store, self.token))
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
