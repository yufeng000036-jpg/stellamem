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

MCP_PROTOCOL_VERSION = "2024-11-05"


class StellamemMCPServer:
    """Lightweight JSON-RPC 2.0 MCP server backed by a Stellamem workspace."""

    def __init__(self, workspace_dir: Path | str, index_db_path: Path | str | None = None) -> None:
        self.workspace_dir = Path(workspace_dir).resolve()
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.active_subject: str | None = None
        self._pref_store = PreferenceCardStore(
            audit_path=self.workspace_dir / "preference_history.jsonl"
        )
        # Optional path to the Stellamem index SQLite DB for live recall.
        # If unset or missing, stellamem_recall returns wired:false (scaffold).
        self.index_db_path = Path(index_db_path).resolve() if index_db_path else None

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
                "description": "Explicitly set active subject namespace (user, persona, or viewer). Fail-closed if omitted.",
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
                "description": "Recall subject-isolated memories with strict token budget and 90-day decay weighting. Wired to live recall when the server was constructed with index_db_path; otherwise returns wired:false.",
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
            # 评审最终验收 P1：主体切换只验证名称合法，不验证调用方有没有权利切到该主体。
            # 本接口只适用于单人可信部署（自己的 DSH / OpenClaw），调用方被假定为可信宿主。
            # 未来多客户端版本必须把主体身份绑定到宿主授权，绝不能让模型自行扩大访问范围。
            # 在那之前，不得把本接口宣称为不可信多客户端的安全边界。
            subj = str(args.get("subject", "")).strip()
            if subj not in VALID_SUBJECTS:
                raise ValueError(
                    f"Invalid subject '{subj}'. Must be one of {sorted(VALID_SUBJECTS)}"
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


def main() -> None:
    workspace = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd() / ".stellamem"
    # Index DB for live recall: 2nd CLI arg, or STELLAMEM_INDEX_DB env var.
    # If unset or missing, stellamem_recall returns wired:false.
    import os

    index_db = None
    if len(sys.argv) > 2:
        index_db = Path(sys.argv[2])
    elif os.environ.get("STELLAMEM_INDEX_DB"):
        index_db = Path(os.environ["STELLAMEM_INDEX_DB"])
    server = StellamemMCPServer(workspace, index_db_path=index_db)
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
