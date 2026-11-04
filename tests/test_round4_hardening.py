"""Round 4 hardening modules: file locks, derived cache, purge, MCP server.

These tests run in CI (unlike the installer script's self-contained suite).
Covers the fixes from the round-4 external review:
- version-chain fail-closed (never overwrite non-empty with empty)
- delimiter-injection-safe chain keys (cf. Mem0 CVE-2026-116575)
- core (subject, predicate, value) field compatibility in purge
- derived-cache sha256 verification on hit
- MCP preference proposals actually persist to pending store
"""

from __future__ import annotations

import json
import tempfile
import threading
from pathlib import Path

import pytest

from adapters.mcp.stellamem_mcp_server import StellamemMCPServer
from compiler.derived_cache import DerivedMarkdownCache
from compiler.file_lock import atomic_append_jsonl
from compiler.memory_purge import run_memory_purge


@pytest.fixture()
def tmp_ws():
    with tempfile.TemporaryDirectory(prefix="stellamem_r4_") as d:
        yield Path(d)


def test_concurrent_atomic_jsonl_append_heals_torn_tail(tmp_ws):
    ledger = tmp_ws / "preference_history.jsonl"
    ledger.write_bytes(b'{"id": "init", "version": 0}')  # torn tail, no newline
    errors: list[Exception] = []

    def worker(tid: int) -> None:
        try:
            for seq in range(15):
                atomic_append_jsonl(ledger, {"thread": tid, "seq": seq})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=w, args=(i,)) for i, w in enumerate([worker] * 4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    lines = ledger.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 + 4 * 15
    for line in lines:
        assert isinstance(json.loads(line), dict)


def test_derived_cache_verifies_hash_on_hit(tmp_ws):
    md = tmp_ws / "m.md"
    md.write_text("# T\nhello", encoding="utf-8")
    calls = 0

    def parser(text: str, path: Path) -> dict:
        nonlocal calls
        calls += 1
        return {"len": len(text)}

    cache = DerivedMarkdownCache(tmp_ws)
    assert cache.get_or_parse(md, parser) == {"len": 9}
    assert cache.get_or_parse(md, parser) == {"len": 9}
    assert calls == 1  # second call was a verified hit, no re-parse


def test_purge_never_overwrites_nonempty_chain_with_empty(tmp_ws):
    chain_path = tmp_ws / "version-chain.json"
    chain_path.write_text(
        json.dumps({"chains": {"user%3Ax:theme": [{"id": "r1"}]}}), encoding="utf-8"
    )
    _, report = run_memory_purge([], version_chain_path=chain_path, now_ms=1_760_000_000_000)
    # Existing chains must survive; refusal is logged, not silent.
    data = json.loads(chain_path.read_text(encoding="utf-8"))
    assert data["chains"] == {"user%3Ax:theme": [{"id": "r1"}]}
    assert any(
        c.get("reason") == "version_chain_write_refused_empty_over_nonempty"
        for c in report.conflicts_logged
    )


def test_purge_chain_keys_resist_delimiter_injection(tmp_ws):
    chain_path = tmp_ws / "version-chain.json"
    records = [
        {
            "id": "a",
            "subject": "user:persona",
            "topic": "theme",
            "statement": "s1",
            "updated_at_ms": 1_760_000_000_000,
            "hit_count": 1,
        },
        {
            "id": "b",
            "subject": "user",
            "topic": "persona:theme",
            "statement": "s2",
            "updated_at_ms": 1_760_000_000_000,
            "hit_count": 1,
        },
    ]
    run_memory_purge(records, version_chain_path=chain_path, now_ms=1_760_000_000_000)
    chains = json.loads(chain_path.read_text(encoding="utf-8"))["chains"]
    assert (
        len(chains) == 2
    )  # no collision between ("user:persona","theme") and ("user","persona:theme")


def test_purge_reads_core_value_field(tmp_ws):
    # Core semantic records use (subject, predicate, value); they must not
    # be read as empty statements (which would fold distinct memories).
    now_ms = 1_760_000_000_000
    records = [
        {
            "id": "c1",
            "subject": "user",
            "predicate": "likes",
            "value": "深色模式",
            "updated_at_ms": now_ms,
            "hit_count": 1,
        },
        {
            "id": "c2",
            "subject": "user",
            "predicate": "likes",
            "value": "早起跑步",
            "updated_at_ms": now_ms,
            "hit_count": 1,
        },
    ]
    retained, report = run_memory_purge(records, now_ms=now_ms)
    assert len(retained) == 2
    assert report.deduplicated_count == 0


def test_mcp_propose_persists_to_pending_store(tmp_ws):
    server = StellamemMCPServer(tmp_ws)
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "stellamem_propose_preference",
                "arguments": {
                    "subject": "user",
                    "topic": "editor",
                    "statement": "Uses Vim keybindings",
                    "evidence": ["said so on 2026-11-04"],
                },
            },
        }
    )
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["status"] == "pending"
    assert payload["persisted"] is True
    # The pending card must actually be in the store's audit ledger.
    audit = (tmp_ws / "preference_history.jsonl").read_text(encoding="utf-8")
    assert "Vim keybindings" in audit
