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
import sys
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
import subjects
from hit_votes import HitVotes, validate_vote
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
        # 后台重建状态：同一时间只跑一个后台重建线程。
        self._rebuild_thread = None
        # 本进程第一次检查是否已过：冷启动（刚重启、库还是旧的）必须同步重建，
        # 保证重启后第一次召回就是新鲜的（验收第三轮的要求）；运行中变旧才走后台。
        self._warmed_up = False
        # 后台重建最近一次失败的时间（monotonic 秒），5 秒内不再重试，
        # 避免重建一直失败时每个读请求都起一个线程刷日志。
        # 60s 太长：一次瞬时失败（慢跑道上的锁冲突）会让索引旧一整分钟，
        # 期间的测试/读请求都拿不到新记录；5s 足够防刷日志，又能快速恢复。
        self._last_rebuild_failed_at = 0.0
        # 后台重建最近一次失败的异常（供测试断言时直接看到原因，
        # 而不是只看到“查不到新记录”这种间接失败）。
        self._rebuild_error = None
        self.token_path = self.root / "system" / "api-token"
        self.token_scopes_path = self.root / "system" / "api-token-scopes.json"
        # Hit 投票：持久化在记忆根的 system/hit-votes/ 下（不在索引指纹范围里，
        # 记票本身不会让索引变旧）。/v1/recall 与 /v1/vote 共用这一个实例。
        self.hit_votes = HitVotes(self.root / "system" / "hit-votes" / "votes.json")

    # ---------------- 内部 ----------------

    def ensure_index(self, force=False) -> dict:
        # 索引过期时：冷启动或显式要求 → 同步重建（卡住本次请求，但保证新鲜）；
        # 运行中变旧 → 后台线程重建，本次先返回旧数据，不卡请求。
        # 后台重建用独立的 Index 连接（不和服务共用一个 sqlite3 连接），
        # WAL 模式下读请求继续读旧快照，提交后才看到新数据。
        while True:
            with self.lock:
                thread = self._rebuild_thread
                if thread is not None and thread.is_alive():
                    if not force:
                        stats = self.index.stats()
                        return {"built_at": stats["built_at"], "records": stats["records"]}
                    # force：不能持锁 join（后台线程结束时要拿锁清标志），
                    # 放锁去等，循环回来重新检查。
                else:
                    stats = self.index.stats()
                    # 验收 P1：只看记录数不够——写记忆不经过 API（如直接写 markdown
                    # 或写完后重启），库里有记录但内容已旧。指纹变了就重建。
                    if force or not stats["records"] or self.index.is_stale(self.root):
                        if force or not self._warmed_up:
                            # F1：索引重建是写操作，先做轻量快照
                            snapshots.auto_snapshot(self.root, "index-rebuild")
                            meta = self.index.build(self.root)
                            self.rebuilt_at = meta["built_at"]
                            self._warmed_up = True
                            return meta
                        if time.monotonic() - self._last_rebuild_failed_at < 5:
                            return {"built_at": stats["built_at"], "records": stats["records"]}
                        t = threading.Thread(target=self._rebuild_in_background, daemon=True)
                        self._rebuild_thread = t
                        t.start()
                        return {"built_at": stats["built_at"], "records": stats["records"]}
                    self._warmed_up = True
                    return {"built_at": stats["built_at"], "records": stats["records"]}
            thread.join()

    def ensure_readable_index(self):
        """Wait for a fresh index; never serve a stale snapshot after revocation."""
        try:
            self.ensure_index()
            with self.lock:
                thread = self._rebuild_thread
            if thread is not None:
                thread.join()
            with self.lock:
                if self.index.is_stale(self.root):
                    raise RuntimeError("index is stale")
        except Exception as exc:
            raise RuntimeError("index freshness unavailable; retry later") from exc

    def _rebuild_in_background(self):
        """后台重建：独立连接，快照 + 重建，失败打 stderr 日志，不静默。"""
        try:
            idx = indexer.Index(self.db)
            try:
                snapshots.auto_snapshot(self.root, "index-rebuild")
                meta = idx.build(self.root)
                self.rebuilt_at = meta["built_at"]
                self._rebuild_error = None
            finally:
                idx.close()
        except Exception as exc:  # noqa: BLE001
            self._last_rebuild_failed_at = time.monotonic()
            self._rebuild_error = exc
            print(f"stellamem: background index rebuild failed: {exc}", file=sys.stderr)
        finally:
            with self.lock:
                if self._rebuild_thread is threading.current_thread():
                    self._rebuild_thread = None

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
            # 评审 P0：配置值必须是合法列表，否则直接拒绝认证。
            # null / 字符串 / 数字等非法配置若被当成有效 Token，
            # get_token_subjects 会返回 None（不受限），等于拿到 Master 权限。
            return isinstance(data.get(token_hash), list)
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
            # 评审 P0：非法配置值（null / 字符串 / 数字）→ 零权限，
            # 绝不能返回 None（不受限）。check_token 已拒绝这类 Token，
            # 这里是防御性 fail-closed。
            return set()
        return set(str(s) for s in subjects)

    def find_record(self, ident: str) -> dict | None:
        # 直接用 index 连接：必须持 Index 的锁（同 build/stats 的那把），
        # 否则和别的 HTTP 线程同时开 cursor 会互相顶掉。
        with self.index._lock:
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
        # 整趟遍历持 Index 的锁：中途被别的线程插一脚会把 cursor 顶掉。
        with self.index._lock:
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
        """按主体权限过滤记录列表；allowed=None 时不过滤。

        评审最终验收 P1：比较时用标准命名空间——旧拼写 "用户" 与 "user"
        是同一命名空间，绝不能因为拼写不同就把授权记录过滤掉。
        """
        if allowed is None:
            return items
        allowed_canon = {subjects.canonical_subject(s) for s in allowed}
        return [
            it for it in items if subjects.canonical_subject(it.get("subject", "")) in allowed_canon
        ]

    def _check_subject_allowed(self, subject: str | None, allowed: set[str] | None) -> bool:
        """单条记录的主体是否在允许范围内；allowed=None 时总允许。

        评审最终验收 P1：比较时用标准命名空间，"用户" 与 "user" 是同一命名空间。
        """
        if allowed is None:
            return True
        allowed_canon = {subjects.canonical_subject(s) for s in allowed}
        return subjects.canonical_subject(subject or "") in allowed_canon

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
                # health 也要先保鲜：否则重启后 health 报的还是旧记录数。
                svc.ensure_readable_index()
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
            # 读接口每次先保鲜索引：文件在服务运行时被改写（如本地 agent
            # 直接写 markdown）时，不靠重启也能读到新记忆。指纹没变时
            # 只是 stat 几个文件，不重建。
            svc.ensure_readable_index()

            if path == "/v1/search":
                q = (query.get("q") or [""])[0]
                limit = min(50, int((query.get("limit") or ["10"])[0]))
                # 主体权限：越权请求直接 403（person 参数指定的主体不在允许范围内）
                # 评审最终验收 P1：比较时用标准命名空间，"用户" 与 "user" 是同一命名空间。
                req_person = (query.get("person") or [None])[0]
                if req_person and allowed is not None:
                    allowed_canon = {subjects.canonical_subject(s) for s in allowed}
                    if subjects.canonical_subject(req_person) not in allowed_canon:
                        return self._send(
                            *_json(HTTPStatus.FORBIDDEN, {"error": "subject not allowed"})
                        )
                # 多主体 SQL IN 过滤：直接把 allowed 集合传给 DB，不过滤后漏召回
                # （评审反例：31 条未授权 + 1 条授权，3 倍策略会返回空）
                # 整趟 search 持 Index 的锁：search.py 直接用这个连接做多次查询，
                # 别的线程同时进来会把 cursor 顶掉（health 并发断连就是这么来的）。
                with svc.index._lock:
                    hits = S.search(
                        svc.index.con,
                        q,
                        kinds=(query.get("kind") or [None])[0],
                        since=(query.get("since") or [None])[0],
                        until=(query.get("until") or [None])[0],
                        person=req_person,
                        limit=limit,
                        persons=allowed,
                        status=("active",),
                    )
                return self._send(
                    *_json(HTTPStatus.OK, {"query": q, "count": len(hits), "items": hits})
                )

            if path == "/v1/timeline":
                req_limit = min(500, int((query.get("limit") or ["200"])[0]))
                # 多主体 SQL IN 过滤（同 search：整趟查询持 Index 的锁）
                with svc.index._lock:
                    rows = S.timeline(
                        svc.index.con,
                        since=(query.get("since") or [None])[0],
                        until=(query.get("until") or [None])[0],
                        limit=req_limit,
                        subjects=allowed,
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
                    # history/related 直接用 index 连接做多次查询，整趟持 Index 的锁
                    with svc.index._lock:
                        data = {"claim_id": ident, "items": S.history(svc.index.con, ident)}
                elif kind == "related":
                    with svc.index._lock:
                        data = {"id": ident, "items": S.related(svc.index.con, ident)}
                else:
                    data = svc.find_record(ident)
                if not data:
                    return self._send(
                        *_json(HTTPStatus.NOT_FOUND, {"error": "not found", "id": ident})
                    )
                # 主体权限：单条记录的主体不在允许范围内 → 404（不透露是否存在）。
                # 越权记录与不存在的记录必须回同一个状态码，否则 403/404 的区分
                # 就能被拿来逐个探测别的主体有没有某条记录。
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
                    # 存在性不泄露：越权记录与不存在的记录一律 404。
                    return self._send(*_json(HTTPStatus.NOT_FOUND, {"error": "not found"}))
                return self._send(*_json(HTTPStatus.OK, data))

            return self._send(
                *_json(HTTPStatus.NOT_FOUND, {"error": "unknown endpoint", "path": path})
            )
        except RuntimeError as exc:
            return self._send(*_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc)}))
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
        # recall 读索引，先保鲜（同 do_GET 的读接口）。
        try:
            svc.ensure_readable_index()
        except RuntimeError as exc:
            return self._send(*_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc)}))

        if path == "/v1/recall":
            payload = self._read_json()
            # 评审 P1：区分"没传 budget"和"明确传 0"。
            # `or 1200` 会把 0 变成 1200，零预算关不掉召回；负数钳到 0。
            raw_budget = payload.get("budget")
            budget = 1200 if raw_budget is None else max(0, int(raw_budget))
            # 主体权限：recall 的 subject 参数若越权 → 403
            # 评审最终验收 P1：比较时用标准命名空间，"用户" 与 "user" 是同一命名空间。
            req_subject = payload.get("subject")
            if req_subject and allowed is not None:
                allowed_canon = {subjects.canonical_subject(s) for s in allowed}
                if subjects.canonical_subject(req_subject) not in allowed_canon:
                    return self._send(
                        *_json(HTTPStatus.FORBIDDEN, {"error": "subject not allowed"})
                    )
            # recall 直接用 index 连接做多次查询，整趟持 Index 的锁
            # （同 search：别的线程同时进来会把 cursor 顶掉）。
            with svc.index._lock:
                result = S.recall(
                    svc.index.con,
                    payload.get("prompt") or "",
                    budget_chars=budget,
                    decay=bool(payload.get("decay", True)),
                    explain_decay=bool(payload.get("explain_decay", False)),
                    subject=req_subject,
                    subjects=allowed,
                    hit_votes=svc.hit_votes,
                )
            # P0 修复：过滤 items 后必须同步重建 text/chars，否则 text 泄露越权主体的正文
            # （DB 层已按 subjects 过滤，这里是防御性二次过滤）
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
            if path == "/v1/vote":
                # 手动投票：只加赞成票，没有反对票；只影响已获准召回内的排序。
                # 受限 Token 只读，投不了票（与 approve/reject 同一条线）。
                subj = str(payload.get("subject") or "").strip()
                record_id = str(payload.get("record_id") or "").strip()
                if not subj or not record_id:
                    return self._send(
                        *_json(
                            HTTPStatus.BAD_REQUEST,
                            {"error": "subject and record_id required"},
                        )
                    )
                try:
                    subj = subjects.validate_subject(subj)
                except ValueError:
                    return self._send(
                        *_json(HTTPStatus.BAD_REQUEST, {"error": f"unknown subject: {subj}"})
                    )
                rec = svc.find_record(record_id)
                if not rec:
                    return self._send(
                        *_json(HTTPStatus.NOT_FOUND, {"error": "not found", "id": record_id})
                    )
                # 投票主体与记录主体必须是同一命名空间，否则拒绝（不透露正文）。
                if not validate_vote(subj, rec.get("subject") or ""):
                    return self._send(*_json(HTTPStatus.FORBIDDEN, {"error": "subject mismatch"}))
                snap = svc.hit_votes.vote(subj, rec["id"], kind=rec.get("kind") or "")
                return self._send(*_json(HTTPStatus.OK, {"ok": True, **snap}))

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
