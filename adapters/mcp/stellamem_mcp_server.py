"""Standard Model Context Protocol (MCP) Server for Stellamem.

Exposes Stellamem's subject-isolated memory recall, preference ledger,
conflict inspection, and crash-safe snapshots over JSON-RPC 2.0 (stdio).
Zero external dependencies required — mounts directly into Claude Desktop,
Cursor, Windsurf, OpenClaw, or local VTuber / digital twin runtimes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from compiler.memory_purge import run_memory_purge
from compiler.preference_summary import PreferenceCardStore, PreferenceEvidenceError
from compiler.subjects import SUBJECTS as VALID_SUBJECTS
from compiler.subjects import validate_subject

MCP_PROTOCOL_VERSION = "2024-11-05"


def parse_allowed_subjects(raw: str | None) -> set[str] | None:
    """Parse a comma-separated allowed-subjects binding value.

    None means unbound: the server keeps today's single-person trusted
    behaviour and warns on stderr at startup. Any other value is
    normalized through subjects.validate_subject (legacy aliases such as
    用户 -> user are accepted); an empty value or an unknown subject
    raises ValueError so a misconfigured server fails at startup instead
    of silently running unbound.
    """
    if raw is None:
        return None
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        raise ValueError(
            "allowed-subjects is empty: pass at least one subject "
            f"({', '.join(sorted(VALID_SUBJECTS))})"
        )
    return {validate_subject(p) for p in parts}


class StellamemMCPServer:
    """Lightweight JSON-RPC 2.0 MCP server backed by a Stellamem workspace."""

    def __init__(
        self,
        workspace_dir: Path | str,
        index_db_path: Path | str | None = None,
        allowed_subjects=None,
    ) -> None:
        self.workspace_dir = Path(workspace_dir).resolve()
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.active_subject: str | None = None
        # Host authorization binding (review final-acceptance P1 fix):
        # the set of subjects this server instance is allowed to serve.
        # None = unbound: today's single-person trusted behaviour, with a
        # stderr warning at startup. A bound server refuses every tool
        # call outside the set (switch/recall fail closed, writes raise).
        if allowed_subjects is None:
            self.allowed_subjects: frozenset[str] | None = None
            print(
                "stellamem: WARNING: subject binding not configured "
                "(--allowed-subjects or STELLAMEM_ALLOWED_SUBJECTS); "
                "single-person trusted use only, not for multi-client deployments.",
                file=sys.stderr,
            )
        else:
            self.allowed_subjects = frozenset(validate_subject(s) for s in allowed_subjects)
        self._pref_store = PreferenceCardStore(
            audit_path=self.workspace_dir / "preference_history.jsonl"
        )
        # Optional path to the Stellamem index SQLite DB for live recall.
        # If unset or missing, stellamem_recall returns wired:false (scaffold).
        self.index_db_path = Path(index_db_path).resolve() if index_db_path else None

    def _subject_allowed(self, subj: str) -> bool:
        """True when the (already canonical) subject is inside the host binding.

        Unbound servers (allowed_subjects is None) allow everything, exactly
        like before this change; bound servers allow only their set.
        """
        return self.allowed_subjects is None or subj in self.allowed_subjects

    def _ensure_index_fresh(self) -> None:
        """MCP 直接读 index.db：召回前先比对源文件指纹，过期就重建。

        记忆根从库里 meta 的 `root` 读（build 时写进），不依赖
        workspace_dir（它不一定是记忆根）。失败时打 stderr 日志，
        读旧索引比不读好，下次调用再试（验收第三轮：原来这里静默吞错，
        stdio 模式下重建失败也看不出来）。
        """
        if not self.index_db_path or not self.index_db_path.is_file():
            return
        try:
            import sqlite3

            from compiler.indexer import Index

            con = sqlite3.connect(str(self.index_db_path))
            try:
                root_row = con.execute("SELECT value FROM meta WHERE key='root'").fetchone()
            finally:
                con.close()
            if not root_row:
                return
            idx = Index(self.index_db_path)
            try:
                if idx.is_stale(Path(root_row[0])):
                    idx.build(Path(root_row[0]))
            finally:
                idx.close()
        except Exception as exc:  # noqa: BLE001
            print(f"stellamem: index refresh failed: {exc}", file=sys.stderr)

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "stellamem_switch_subject",
                "description": "Explicitly set active subject namespace (user, persona, or viewer). Fail-closed if omitted; refused when the target is outside this server's host-authorized subject binding (--allowed-subjects / STELLAMEM_ALLOWED_SUBJECTS).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "subject": {
                            "type": "string",
                            "enum": sorted(VALID_SUBJECTS),
                            "description": "Target isolated subject namespace.",
                        }
                    },
                    "required": ["subject"],
                },
            },
            {
                "name": "stellamem_recall",
                "description": "Recall subject-isolated memories with strict token budget and 90-day decay weighting. Wired to live recall when the server was constructed with index_db_path; otherwise returns wired:false. Irrelevant questions are gated to zero injection (status gated, see compiler/injection_gate.py); set STELLAMEM_INJECTION_GATE=0 to disable the gate.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "subject": {"type": "string", "enum": sorted(VALID_SUBJECTS)},
                        "query": {"type": "string"},
                        "max_chars": {"type": "integer", "default": 2000},
                    },
                    "required": ["subject", "query"],
                },
            },
            {
                "name": "stellamem_propose_preference",
                "description": "Propose an evidence-backed preference card (enters pending state until approved).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "subject": {"type": "string", "enum": sorted(VALID_SUBJECTS)},
                        "topic": {"type": "string"},
                        "statement": {"type": "string"},
                        "evidence": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    },
                    "required": ["subject", "topic", "statement", "evidence"],
                },
            },
            {
                "name": "stellamem_purge_and_audit",
                "description": "Run deterministic memory deduplication, decay check, and conflict-log.md audit.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "archive_after_days": {"type": "integer", "default": 90},
                    },
                },
            },
        ]

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method = request.get("method", "")
        req_id = request.get("id")
        params = request.get("params") or {}

        if method == "notifications/initialized":
            return None

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "stellamem-mcp", "version": "0.3.0"},
                },
            }

        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": self.list_tools()},
            }

        if method == "tools/call":
            tool_name = params.get("name")
            args = params.get("arguments") or {}
            try:
                output = self._call_tool(tool_name, args)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {"type": "text", "text": json.dumps(output, ensure_ascii=False)}
                        ],
                        "isError": False,
                    },
                }
            except Exception as exc:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Error: {exc}"}],
                        "isError": True,
                    },
                }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }

    def _call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "stellamem_switch_subject":
            # 评审最终验收 P1 已修复：主体切换不再只验证名称合法。
            # 本接口现在受宿主授权绑定约束：启动时用 --allowed-subjects 或
            # STELLAMEM_ALLOWED_SUBJECTS 配好允许集，切到集外主体直接拒绝；
            # 没配绑定时保持单人可信行为，但启动时会在 stderr 告警。
            # 多客户端部署必须配绑定，否则不得宣称具备多客户端隔离。
            subj = str(args.get("subject", "")).strip()
            if subj not in VALID_SUBJECTS:
                raise ValueError(
                    f"Invalid subject '{subj}'. Must be one of {sorted(VALID_SUBJECTS)}"
                )
            if not self._subject_allowed(subj):
                raise ValueError(
                    f"Subject '{subj}' is not in this server's allowed subjects "
                    f"({sorted(self.allowed_subjects)}); host authorization binding "
                    "refuses the switch"
                )
            self.active_subject = subj
            return {"status": "ok", "active_subject": self.active_subject}

        if name == "stellamem_recall":
            subj = str(args.get("subject") or self.active_subject or "").strip()
            if subj not in VALID_SUBJECTS:
                return {
                    "status": "fail_closed",
                    "items": [],
                    "reason": "Missing or invalid subject",
                }
            # 宿主授权绑定：显式 subject 与 active_subject 两条路都要拦，
            # 集外主体一律空结果，绝不返回数据。
            if not self._subject_allowed(subj):
                return {
                    "status": "fail_closed",
                    "items": [],
                    "reason": f"Subject '{subj}' is not allowed for this server",
                }
            query = str(args.get("query", "")).strip()
            # 评审 P1：区分"没传 max_chars"和"明确传 0"。
            # `or 2000` 会把 0 变成 2000，零预算关不掉召回；负数钳到 0。
            raw_max = args.get("max_chars")
            max_chars = 2000 if raw_max is None else max(0, int(raw_max))
            # Live recall when an index DB is configured; otherwise scaffold.
            # 验收 P1：MCP 直接读 index.db，同样会被旧索引坑——召回前先
            # 比对源文件指纹，过期就重建。
            self._ensure_index_fresh()
            if self.index_db_path and self.index_db_path.is_file():
                try:
                    import sqlite3

                    from compiler.search import recall as live_recall

                    con = sqlite3.connect(str(self.index_db_path))
                    con.row_factory = sqlite3.Row
                    try:
                        # P1 注入门（调用方实体/关键词门）：问题与记忆没有词面
                        # 关联时不注入，budget=0。规则借 OpenClaw 适配器的
                        # lexical 门（compiler/injection_gate.py）。
                        # HTTP API 默认行为不变；门只在 MCP 召回入口生效；
                        # STELLAMEM_INJECTION_GATE=0 可关。门自己出错时
                        # 失败放行（照常召回），绝不因为门查不到而搞挂召回。
                        if max_chars > 0:
                            try:
                                from compiler.injection_gate import (
                                    check as _gate_check,
                                )
                                from compiler.injection_gate import (
                                    gate_enabled as _gate_enabled,
                                )

                                if _gate_enabled():
                                    gate = _gate_check(con, query, subject=subj)
                                    if not gate["allow"]:
                                        return {
                                            "status": "gated",
                                            "subject": subj,
                                            "query": query,
                                            "items": [],
                                            "wired": True,
                                            "chars": 0,
                                            "budget": 0,
                                            "reason": (f"injection gate blocked: {gate['reason']}"),
                                            "gate": gate,
                                        }
                            except Exception as exc:  # noqa: BLE001
                                print(
                                    f"stellamem: injection gate failed open: {exc}",
                                    file=sys.stderr,
                                )
                        result = live_recall(
                            con,
                            query,
                            budget_chars=max_chars,
                            max_items=6,
                            pref_subject=subj,
                            subject=subj,
                        )
                    finally:
                        con.close()
                    # Subject isolation: database-level filtering via subject=subj
                    # above, plus post-query check as defense in depth.
                    # 评审最终验收 P1：比较时用标准命名空间——旧拼写 "用户" 与 "user"
                    # 是同一命名空间，不能因为拼写不同就丢掉授权记录。
                    from compiler.subjects import canonical_subject

                    items = [
                        {
                            "id": it.get("id"),
                            "text": it.get("text") or it.get("value") or "",
                            "score": it.get("score"),
                            "kind": it.get("kind"),
                        }
                        for it in result.get("items", [])
                        if canonical_subject(it.get("subject", subj)) == subj
                    ]
                    return {
                        "status": "ok",
                        "subject": subj,
                        "query": query,
                        "items": items,
                        "wired": True,
                        "chars": result.get("chars", 0),
                    }
                except Exception as exc:  # noqa: BLE001
                    return {
                        "status": "error",
                        "subject": subj,
                        "query": query,
                        "items": [],
                        "wired": True,
                        "reason": f"recall failed: {exc}",
                    }
            # Scaffolding only: the live recall pipeline is not wired here yet.
            # `wired: false` lets callers distinguish "no results" from "not connected".
            return {"status": "ok", "subject": subj, "query": query, "items": [], "wired": False}

        if name == "stellamem_propose_preference":
            subj = str(args.get("subject") or self.active_subject or "").strip()
            if subj not in VALID_SUBJECTS:
                raise ValueError("Explicit valid subject required for preference proposal")
            # 宿主授权绑定：集外主体的写一律拒绝，不只是读。
            if not self._subject_allowed(subj):
                raise ValueError(
                    f"Subject '{subj}' is not in this server's allowed subjects "
                    f"({sorted(self.allowed_subjects)}); host authorization binding "
                    "refuses the write"
                )
            evidence = args.get("evidence") or []
            if not evidence or not any(str(e).strip() for e in evidence):
                raise ValueError("Preference proposal rejected: non-empty evidence is required")
            topic = str(args.get("topic") or "").strip()
            statement = str(args.get("statement") or "").strip()
            # Persist to the real pending store (status=pending, needs named
            # human approval to activate). No more false "pending" without storage.
            evidence_items = [
                {"statement": topic or statement, "evidence": str(e).strip()}
                for e in evidence
                if str(e).strip()
            ]
            try:
                card = self._pref_store.create(
                    subject=subj, topic=topic, text=statement, evidence=evidence_items
                )
            except KeyError:
                # Card exists: propose as an update (new pending version).
                try:
                    card = self._pref_store.update(
                        subject=subj, topic=topic, text=statement, evidence=evidence_items
                    )
                except (PreferenceEvidenceError, KeyError) as exc:
                    raise ValueError(f"Preference proposal rejected: {exc}") from exc
            except PreferenceEvidenceError as exc:
                raise ValueError(f"Preference proposal rejected: {exc}") from exc
            return {
                "status": card.get("status", "pending"),
                "subject": subj,
                "topic": topic,
                "statement": statement,
                "evidence_count": len(evidence_items),
                "version": card.get("version", 1),
                "persisted": True,
            }

        if name == "stellamem_purge_and_audit":
            conflict_path = self.workspace_dir / "conflict-log.md"
            chain_path = self.workspace_dir / "version-chain.json"
            _, report = run_memory_purge(
                [],
                conflict_log_path=conflict_path,
                version_chain_path=chain_path,
                archive_after_days=int(args.get("archive_after_days", 90)),
            )
            return {"status": "ok", "report": report.to_dict()}

        raise ValueError(f"Unknown tool: {name}")


def build_server(argv: list[str] | None = None, environ: dict | None = None) -> StellamemMCPServer:
    """Build the server from CLI args + environment (CLI wins).

    Positional args stay compatible with the old invocation:
    ``stellamem_mcp_server.py [workspace [index_db]]``.
    Host authorization binding comes from ``--allowed-subjects`` (comma
    separated, aliases accepted) or ``STELLAMEM_ALLOWED_SUBJECTS``; the
    CLI flag wins when both are set. An invalid binding value raises
    ValueError so the process fails at startup instead of silently
    running unbound. Neither set means unbound (single-person trusted
    use only; the constructor warns on stderr).
    """
    import argparse
    import os

    parser = argparse.ArgumentParser(
        prog="stellamem_mcp_server",
        description="Stellamem MCP server (stdio).",
    )
    parser.add_argument(
        "workspace",
        nargs="?",
        default=None,
        help="Workspace directory (default: ./.stellamem).",
    )
    parser.add_argument(
        "index_db",
        nargs="?",
        default=None,
        help="Path to the Stellamem index SQLite DB for live recall "
        "(or STELLAMEM_INDEX_DB). Unset means wired:false scaffold.",
    )
    parser.add_argument(
        "--allowed-subjects",
        default=None,
        help="Comma-separated subjects this server may serve "
        "(user, persona, viewer; legacy aliases accepted). "
        "Overrides STELLAMEM_ALLOWED_SUBJECTS. If neither is set, "
        "the server runs unbound: single-person trusted use only.",
    )
    args = parser.parse_args(argv)
    env = os.environ if environ is None else environ

    workspace = Path(args.workspace) if args.workspace else Path.cwd() / ".stellamem"
    # Index DB for live recall: 2nd CLI arg, or STELLAMEM_INDEX_DB env var.
    # If unset or missing, stellamem_recall returns wired:false.
    index_db = None
    if args.index_db:
        index_db = Path(args.index_db)
    elif env.get("STELLAMEM_INDEX_DB"):
        index_db = Path(env["STELLAMEM_INDEX_DB"])
    raw_allowed = (
        args.allowed_subjects
        if args.allowed_subjects is not None
        else env.get("STELLAMEM_ALLOWED_SUBJECTS")
    )
    return StellamemMCPServer(
        workspace,
        index_db_path=index_db,
        allowed_subjects=parse_allowed_subjects(raw_allowed),
    )


def main(argv: list[str] | None = None) -> None:
    server = build_server(argv)
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        req = json.loads(line)
        resp = server.handle_request(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
