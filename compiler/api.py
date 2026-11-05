"""本机记忆 API（v1）。

约束
- 只绑定 127.0.0.1；Host 必须是本机名，否则 421。
- 读写接口均要求 Bearer token（存在 `system/api-token`）；仅 /v1/health 例外（只返回统计数）。
- Token 主体权限（可选）：`system/api-token-scopes.json` 可将 Token 绑定到允许访问的主体；
  未绑定或文件不存在时，Token 允许所有主体（向后兼容，现有 DSH 调用不受影响）。
  越权访问返回 403。
- 写接口校验 Origin：带跨站 Origin 一律拒绝，防 CSRF。
- 索引只读：所有写操作走 Transaction / ExternalTransaction。
"""

from __future__ import annotations

import hashlib
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
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Content-Length": str(len(body)),
    }
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
        self.token_scopes_path = self.root / "system" / "api-token-scopes.json"

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
        if not header or not header.startswith("Bearer "):
            return False
        token = header[7:].strip()
        # Master token 总是有效
        if secrets.compare_digest(token, self.ensure_token()):
            return True
        # Scoped token：查 scopes 文件（按 SHA256）
        if self.token_scopes_path.exists():
            try:
                data = json.loads(self.token_scopes_path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                return False
            token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
            return token_hash in data
        return False

    def get_token_subjects(self, header: str | None) -> set[str] | None:
        """返回 Token 允许访问的主体集合；None 表示不受限（向后兼容）。

        规则：
        - Header 无效 → None（调用方应先 check_token，返回 401）
        - Token 是 master token（api-token 文件里的）→ None（不受限）
        - scopes 文件不存在 → None（不受限，现有调用不受影响）
        - Token 在 scopes 文件里 → 返回绑定的主体集合
        - 其他情况 → None（防御性，check_token 已保证 Token 有效）
        """
        if not header or not header.startswith("Bearer "):
            return None
        token = header[7:].strip()
        # Master token 不受限
        if secrets.compare_digest(token, self.ensure_token()):
            return None
        # 查 scopes 文件（按 SHA256 存，不存明文 Token）
        if not self.token_scopes_path.exists():
            return None
        try:
            data = json.loads(self.token_scopes_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return None
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        subjects = data.get(token_hash)
        if subjects is None:
            return None
        if not isinstance(subjects, list):
            return None
        return set(str(s) for s in subjects)

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

    def _allowed_subjects(self) -> set[str] | None:
        """当前请求 Token 允许访问的主体；None 表示不受限。"""
        return self.service.get_token_subjects(self.headers.get("Authorization"))

    def _filter_subjects(self, items: list[dict], allowed: set[str] | None) -> list[dict]:
        """按主体权限过滤记录列表；allowed=None 时不过滤。"""
        if allowed is None:
            return items
        return [it for it in items if str(it.get("subject", "")) in allowed]

    def _check_subject_allowed(self, subject: str | None, allowed: set[str] | None) -> bool:
        """单条记录的主体是否在允许范围内；allowed=None 时总允许。"""
        if allowed is None:
            return True
        return str(subject or "") in allowed

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
            allowed = self._allowed_subjects()

            if path == "/v1/search":
                q = (query.get("q") or [""])[0]
                limit = min(50, int((query.get("limit") or ["10"])[0]))
                # 主体权限：越权请求直接 403（person 参数指定的主体不在允许范围内）
                req_person = (query.get("person") or [None])[0]
                if req_person and allowed is not None and req_person not in allowed:
                    return self._send(
                        *_json(HTTPStatus.FORBIDDEN, {"error": "subject not allowed"})
                    )
                # P1 修复：DB 层过滤，避免先取 10 条全是越权主体导致漏召回
                # 单主体 Token：直接传 person 给 DB；多主体：取 3 倍再过滤
                db_person = req_person
                fetch_limit = limit
                if allowed is not None and not req_person:
                    if len(allowed) == 1:
                        db_person = next(iter(allowed))
                    else:
                        fetch_limit = limit * 3
                hits = S.search(
                    svc.index.con,
                    q,
                    kinds=(query.get("kind") or [None])[0],
                    since=(query.get("since") or [None])[0],
                    until=(query.get("until") or [None])[0],
                    person=db_person,
                    limit=fetch_limit,
                )
                hits = self._filter_subjects(hits, allowed)[:limit]
                return self._send(
                    *_json(HTTPStatus.OK, {"query": q, "count": len(hits), "items": hits})
                )

            if path == "/v1/timeline":
                req_limit = min(500, int((query.get("limit") or ["200"])[0]))
                # P1 修复：DB 层过滤，单主体直接传 subject，多主体取 3 倍再过滤
                db_subject = None
                fetch_limit = req_limit
                if allowed is not None:
                    if len(allowed) == 1:
                        db_subject = next(iter(allowed))
                    else:
                        fetch_limit = req_limit * 3
                rows = S.timeline(
                    svc.index.con,
                    since=(query.get("since") or [None])[0],
                    until=(query.get("until") or [None])[0],
                    limit=fetch_limit,
                    subject=db_subject,
                )
                rows = self._filter_subjects(rows, allowed)[:req_limit]
                return self._send(*_json(HTTPStatus.OK, {"count": len(rows), "items": rows}))

            if path == "/v1/namespaces":
                return self._send(*_json(HTTPStatus.OK, {"items": sorted(svc.namespaces)}))

            if path == "/v1/export":
                ns = (query.get("ns") or ["personal"])[0]
                rows = svc.export(ns)
                rows = self._filter_subjects(rows, allowed)
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
                # 主体权限：单条记录的主体不在允许范围内 → 403（不透露是否存在）
                rec_subject = None
                if kind == "evidence":
                    # evidence 不含 subject 字段，查原记录
                    rec = svc.find_record(ident)
                    rec_subject = rec.get("subject") if rec else None
                elif kind == "history":
                    # history 返回多条，过滤掉越权的
                    if allowed is not None:
                        data["items"] = self._filter_subjects(data["items"], allowed)
                    return self._send(*_json(HTTPStatus.OK, data))
                elif kind == "related":
                    if allowed is not None:
                        data["items"] = self._filter_subjects(data["items"], allowed)
                    return self._send(*_json(HTTPStatus.OK, data))
                else:
                    rec_subject = data.get("subject")
                if not self._check_subject_allowed(rec_subject, allowed):
                    return self._send(
                        *_json(HTTPStatus.FORBIDDEN, {"error": "subject not allowed"})
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
        allowed = self._allowed_subjects()

        if path == "/v1/recall":
            payload = self._read_json()
            budget = int(payload.get("budget") or 1200)
            # 主体权限：recall 的 subject 参数若越权 → 403
            req_subject = payload.get("subject")
            if req_subject and allowed is not None and req_subject not in allowed:
                return self._send(*_json(HTTPStatus.FORBIDDEN, {"error": "subject not allowed"}))
            result = S.recall(
                svc.index.con,
                payload.get("prompt") or "",
                budget_chars=budget,
                decay=bool(payload.get("decay", True)),
                explain_decay=bool(payload.get("explain_decay", False)),
                subject=req_subject,
            )
            # P0 修复：过滤 items 后必须同步重建 text/chars，否则 text 泄露越权主体的正文
            if allowed is not None and result.get("items"):
                result["items"] = self._filter_subjects(result["items"], allowed)
                # 按 search.recall 同样的行格式重建 text
                lines = []
                for rec in result["items"]:
                    line = f"- [{S.KIND_LABEL.get(rec.get('kind'), rec.get('kind'))}] {S._trim(rec.get('value'), 90)}"
                    if rec.get("occurred_at"):
                        line += f"（{rec['occurred_at'][:10]}）"
                    lines.append(line)
                result["text"] = "\n".join(lines)
                result["chars"] = sum(len(line) + 1 for line in lines)
                result["dropped"] = max(0, result.get("dropped", 0))
            return self._send(*_json(HTTPStatus.OK, result))

        # 以下为写操作：Scoped Token（受限 Token）禁止写操作，只读
        # P0 修复：没有写权限的客户端不能审批、回滚或备份
        if allowed is not None:
            return self._send(*_json(HTTPStatus.FORBIDDEN, {"error": "scoped token is read-only"}))
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
