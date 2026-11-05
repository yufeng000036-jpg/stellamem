"""本机记忆 API（v1）。

约束
- 只绑定 127.0.0.1；Host 必须是本机名，否则 421。
- 读写接口均要求 Bearer token（存在 `system/api-token`）；仅 /v1/health 例外（只返回统计数）。
- 写接口校验 Origin：带跨站 Origin 一律拒绝，防 CSRF。
- 索引只读：所有写操作走 Transaction / ExternalTransaction。
"""

from __future__ import annotations

import json
import re
import secrets
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import indexer
import reliability as rel
import search as S
import snapshots
from storage import Transaction, load_records

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
API_PREFIX = "/v1"
MAX_BODY = 64 * 1024


def _json(status, payload, extra_headers=None):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8", "Content-Length": str(len(body))}
    headers.update(extra_headers or {})
    return status, headers, body


class MemoryService:
    """把索引、检索、写操作包在一起，便于测试与复用。"""

    def __init__(self, root: Path, db_path: Path, namespaces=None):
        self.root = Path(root)
        self.db = Path(db_path)
        self.namespaces = namespaces or {"personal": self.root}
        self.index = indexer.Index(self.db)
        self.rebuilt_at = None
        self.lock = threading.Lock()
        self.token_path = self.root / "system" / "api-token"

    # ---------------- 内部 ----------------

    def ensure_index(self, force=False) -> dict:
        with self.lock:
            stats = self.index.stats()
            if force or not stats["records"]:
                # F1：索引重建是写操作，先做轻量快照
                snapshots.auto_snapshot(self.root, "index-rebuild")
                meta = self.index.build(self.root)
                self.rebuilt_at = meta["built_at"]
                return meta
            return {"built_at": stats["built_at"], "records": stats["records"]}

    def ensure_token(self) -> str:
        if self.token_path.exists():
            return self.token_path.read_text(encoding="utf-8").strip()
        token = secrets.token_urlsafe(32)
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(token, encoding="utf-8")
        return token

    def check_token(self, header: str | None) -> bool:
        expected = self.ensure_token()
        if not header or not header.startswith("Bearer "):
            return False
        return secrets.compare_digest(header[7:].strip(), expected)

    def find_record(self, ident: str) -> dict | None:
        row = self.index.con.execute(
            "SELECT * FROM records WHERE id = ? OR claim_id = ? OR revision_id = ? LIMIT 1",
            (ident, ident, ident),
        ).fetchone()
        return dict(row) if row else None

    def evidence(self, record_id: str) -> dict | None:
        from raw_source import get_record

        raw_root = self.root / "_raw"
        if not raw_root.exists():
            return None
        record = get_record(raw_root, record_id)
        if not record:
            return None
        return {
            "record_id": record_id,
            "text": record.get("text"),
            "role": record.get("role"),
            "timestamp": record.get("timestamp"),
            "session": record.get("session_file"),
            "sha256": record.get("text_sha256"),
        }

    def audit(self, action: str, **fields) -> dict:
        return rel.AuditLog(self.root).append(action, self.last_tx(), **fields)

    def write_status(self, ident: str, status: str) -> dict:
        """把一条待审记忆改成 active/rejected，落事务 + 审计。"""
        # F1：写前快照
        snapshots.auto_snapshot(self.root, f"write-status-{status}")
        changes = {}
        for rel_path, _layer in indexer.MANAGED:
            path = self.root / rel_path
            if not path.exists():
                continue
            try:
                prefix, records = load_records(path)
            except ValueError:
                # inbox 里可能有非 canonical 记录，跳过而不是整条链路失败
                continue
            hit = False
            for rec in records:
                if (rec.get("claim_id") == ident or rec.get("revision_id") == ident) and rec.get(
                    "status"
                ) == "pending":
                    rec["status"] = status
                    rec["reviewed_at"] = rel.now_iso()
                    hit = True
            if hit:
                from storage import render_records

                changes[rel_path] = render_records(prefix, records)
        if not changes:
            raise LookupError("pending record not found")
        txid = Transaction(self.root).commit(changes)
        rel.AuditLog(self.root).append("review", txid, claim_id=ident, status=status)
        self.ensure_index(force=True)
        return {"ok": True, "txid": txid, "claim_id": ident, "status": status}

    def backup(self, dest: Path) -> dict:
        manifest = rel.snapshot(self.root, dest)
        return {
            "ok": True,
            "archive": str(dest),
            "files": len(manifest["files"]),
            "digest": manifest["digest"],
        }

    def export(self, ns: str = "personal") -> list[dict]:
        rows = []
        for row in self.index.con.execute("SELECT * FROM records ORDER BY occurred_at"):
            rec = dict(row)
            rec.pop("grams", None)
            rec["namespace"] = ns
            rows.append(rec)
        return rows


class Handler(BaseHTTPRequestHandler):
    service: MemoryService = None  # 由 make_server 注入
    server_version = "StellamemAPI/1.0"

    def log_message(self, fmt, *args):  # 静默：避免把查询写进 stdout 日志
        return

    # ---------------- 通用 ----------------

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0].strip("[]")
        return host in LOCAL_HOSTS

    def _send(self, status, headers, body):
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8") or "{}")

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True  # curl / 本机脚本
        host = urlparse(origin).hostname or ""
        return host in LOCAL_HOSTS

    def _require_token(self) -> bool:
        if not self.service.check_token(self.headers.get("Authorization")):
            self._send(*_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"}))
            return False
        return True

    # ---------------- 路由 ----------------

    def do_GET(self):
        if not self._host_ok():
            return self._send(*_json(HTTPStatus.MISDIRECTED_REQUEST, {"error": "bad host"}))
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)
        svc = self.service

        try:
            if path in {"/v1/health", "/health"}:
                stats = svc.index.stats()
                return self._send(
                    *_json(
                        HTTPStatus.OK,
                        {
                            "ok": True,
                            "version": "v1",
                            "root": str(svc.root),
                            "records": stats["records"],
                            "docs": stats["docs"],
                            "built_at": stats["built_at"],
                            "by_layer": stats["by_layer"],
                            "model_required": False,
                        },
                    )
                )

            # 读接口也要 Token：search/timeline/export/memory/history 等都返回记忆内容，
            # 只靠本机 Host 限制不够（同机其他程序可调）。health 例外：只返回统计数。
            if not self._require_token():
                return

            if path == "/v1/search":
                q = (query.get("q") or [""])[0]
                limit = min(50, int((query.get("limit") or ["10"])[0]))
                hits = S.search(
                    svc.index.con,
                    q,
                    kinds=(query.get("kind") or [None])[0],
                    since=(query.get("since") or [None])[0],
                    until=(query.get("until") or [None])[0],
                    person=(query.get("person") or [None])[0],
                    limit=limit,
                )
                return self._send(
                    *_json(HTTPStatus.OK, {"query": q, "count": len(hits), "items": hits})
                )

            if path == "/v1/timeline":
                rows = S.timeline(
                    svc.index.con,
                    since=(query.get("since") or [None])[0],
                    until=(query.get("until") or [None])[0],
                    limit=min(500, int((query.get("limit") or ["200"])[0])),
                )
                return self._send(*_json(HTTPStatus.OK, {"count": len(rows), "items": rows}))

            if path == "/v1/namespaces":
                return self._send(*_json(HTTPStatus.OK, {"items": sorted(svc.namespaces)}))

            if path == "/v1/export":
                ns = (query.get("ns") or ["personal"])[0]
                rows = svc.export(ns)
                body = ("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n").encode()
                return self._send(
                    *(
                        HTTPStatus.OK,
                        {
                            "Content-Type": "application/x-ndjson; charset=utf-8",
                            "Content-Length": str(len(body)),
                            "Content-Disposition": f'attachment; filename="stellamem-{ns}.jsonl"',
                        },
                        body,
                    )
                )

            match = re.fullmatch(r"/v1/(memory|history|related|evidence)/(.+)", path)
            if match:
                kind, ident = match.group(1), match.group(2)
                if kind == "evidence":
                    data = svc.evidence(ident)
                elif kind == "history":
                    data = {"claim_id": ident, "items": S.history(svc.index.con, ident)}
                elif kind == "related":
                    data = {"id": ident, "items": S.related(svc.index.con, ident)}
                else:
                    data = svc.find_record(ident)
                if not data:
                    return self._send(
                        *_json(HTTPStatus.NOT_FOUND, {"error": "not found", "id": ident})
                    )
                return self._send(*_json(HTTPStatus.OK, data))

            return self._send(
                *_json(HTTPStatus.NOT_FOUND, {"error": "unknown endpoint", "path": path})
            )
        except ValueError as exc:
            return self._send(*_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)}))

    def do_POST(self):
        if not self._host_ok():
            return self._send(*_json(HTTPStatus.MISDIRECTED_REQUEST, {"error": "bad host"}))
        if not self._origin_ok():
            return self._send(*_json(HTTPStatus.FORBIDDEN, {"error": "cross-origin blocked"}))
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        svc = self.service

        # recall 也是读接口，同样要 Token（之前在 Token 检查之前，无鉴权）。
        if not self._require_token():
            return

        if path == "/v1/recall":
            payload = self._read_json()
            budget = int(payload.get("budget") or 1200)
            result = S.recall(
                svc.index.con,
                payload.get("prompt") or "",
                budget_chars=budget,
                decay=bool(payload.get("decay", True)),
                explain_decay=bool(payload.get("explain_decay", False)),
            )
            return self._send(*_json(HTTPStatus.OK, result))

        # 以下为写操作
        try:
            payload = self._read_json()
            if path in {"/v1/approve", "/v1/reject"}:
                status = "active" if path.endswith("approve") else "rejected"
                ident = payload.get("claim_id") or payload.get("id")
                if not ident:
                    return self._send(
                        *_json(HTTPStatus.BAD_REQUEST, {"error": "claim_id required"})
                    )
                return self._send(*_json(HTTPStatus.OK, svc.write_status(ident, status)))

            if path == "/v1/rebuild":
                meta = svc.ensure_index(force=True)
                return self._send(*_json(HTTPStatus.OK, {"ok": True, **meta}))

            if path == "/v1/backup":
                dest = Path(
                    payload.get("dest")
                    or (svc.root.parent / "backups" / f"snap-{int(time.time())}.zip")
                )
                return self._send(*_json(HTTPStatus.OK, svc.backup(dest)))

            if path == "/v1/undo":
                txid = rel.undo(svc.root, payload.get("txid"))
                svc.ensure_index(force=True)
                return self._send(*_json(HTTPStatus.OK, {"ok": True, "undone": txid}))

            return self._send(
                *_json(HTTPStatus.NOT_FOUND, {"error": "unknown endpoint", "path": path})
            )
        except LookupError as exc:
            return self._send(*_json(HTTPStatus.NOT_FOUND, {"error": str(exc)}))
        except (ValueError, RuntimeError) as exc:
            return self._send(*_json(HTTPStatus.CONFLICT, {"error": str(exc)}))

    def do_PUT(self):
        return self._send(*_json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "read-only surface"}))

    do_DELETE = do_PUT
    do_PATCH = do_PUT


def make_server(root, db_path, host="127.0.0.1", port=8791, namespaces=None):
    if host not in LOCAL_HOSTS:
        raise ValueError("only loopback binding is allowed")
    service = MemoryService(root, db_path, namespaces)
    handler = type("BoundHandler", (Handler,), {"service": service})
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    return httpd, service


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="Stellamem local memory API")
    parser.add_argument("--root", default=str(Path.home() / ".dsh" / "memories"))
    parser.add_argument("--db", default=str(Path("./stellamem-data/index.db")))
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args(argv)

    httpd, service = make_server(Path(args.root), Path(args.db), args.host, args.port)
    meta = service.ensure_index()
    _ = service.ensure_token()
    print(f"[api] http://{args.host}:{args.port}/v1  records={meta.get('records')}")
    print(f"[api] token file: {service.token_path}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
