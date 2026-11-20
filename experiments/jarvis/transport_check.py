"""Fault injection against the loopback transport. No language model."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import jarvis


def run(home):
    mode = "wrong-port"
    posts = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                json.dumps({"data": [{"id": "other" if mode == "wrong-port" else "test"}]}).encode()
            )

        def do_POST(self):
            posts.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            if mode == "timeout":
                time.sleep(0.3)
                return
            if mode == "disconnect":
                self.close_connection = True
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"choices":[{"message":{"content":"OK"}}]}')

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        (home / "model.json").write_text(
            json.dumps({"port": server.server_port, "model": "test"}), "utf-8"
        )
        history = []

        def chat():
            return jarvis.handle(home, "聊 虚构样例", lambda _: False, history)

        try:
            assert "端口服务不匹配" in chat() and not posts and not history
            mode = "disconnect"
            assert "未执行" in chat() and not history
            mode = "timeout"
            with patch.object(jarvis, "MODEL_TIMEOUT_SECONDS", 0.12):
                assert "超时" in chat() and not history
            mode = "ok"
            assert "模型答复｜OK" == chat() and len(history) == 2
            assert "暂无待办" in jarvis.handle(home, "待办", lambda _: False, history)
        finally:
            server.shutdown()
            thread.join()
