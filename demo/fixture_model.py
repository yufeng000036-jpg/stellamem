"""Deterministic HTTP fixture for engineering tests; NOT an AI quality benchmark."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        body = json.dumps({"data": [{"id": "synthetic-fixture"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        messages = json.loads(body["messages"][-1]["content"])
        if isinstance(messages, dict):
            claims = [{"relation": "UNCERTAIN", "confidence": 1}]
        else:
            claims = [{"kind": "project", "subject": "fictional-demo", "scope": "global",
                       "predicate": "note-" + str(m["timestamp"]),
                       "value": m["text"].strip(), "evidence": m["text"].strip(),
                       "source": m["speaker"], "message_index": i, "confidence": 0.99}
                      for i, m in enumerate(messages)]
        data = json.dumps({"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(claims, ensure_ascii=False)}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)


class FixtureServer:
    def __enter__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:" + str(self.server.server_port)
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
