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

    def __init__(self, workspace_dir: Path | str) -> None:
        self.workspace_dir = Path(workspace_dir).resolve()
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.active_subject: str | None = None
        self._pref_store = PreferenceCardStore(
            audit_path=self.workspace_dir / "preference_history.jsonl"
        )

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
                "description": "Recall subject-isolated memories with strict token budget and 90-day decay weighting. SCAFFOLDING: not yet wired to the live recall pipeline; returns no items until wired.",
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
    server = StellamemMCPServer(workspace)
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
