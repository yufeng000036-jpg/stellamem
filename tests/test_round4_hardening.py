"""Round 4 hardening modules: file locks, derived cache, purge, MCP server.

unittest-style so the existing CI (`python -m unittest discover -s tests`)
picks them up without a pytest dependency.
Covers the fixes from the round-4 external reviews:
- version-chain fail-closed (never overwrite non-empty with empty)
- delimiter-injection-safe chain keys and signatures (cf. Mem0 CVE-2026-116575)
- core (subject, predicate, value) field compatibility in purge
- derived-cache sha256 verification on hit
- MCP preference proposals actually persist to pending store
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from adapters.mcp.stellamem_mcp_server import StellamemMCPServer
from compiler.derived_cache import DerivedMarkdownCache
from compiler.file_lock import atomic_append_jsonl
from compiler.memory_purge import compute_statement_signature, run_memory_purge


class Round4HardeningTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="stellamem_r4_")
        self.tmp_ws = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_concurrent_atomic_jsonl_append_heals_torn_tail(self):
        ledger = self.tmp_ws / "preference_history.jsonl"
        ledger.write_bytes(b'{"id": "init", "version": 0}')  # torn tail, no newline
        errors: list = []

        def worker(tid: int) -> None:
            try:
                for seq in range(15):
                    atomic_append_jsonl(ledger, {"thread": tid, "seq": seq})
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        lines = ledger.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1 + 4 * 15)
        for line in lines:
            self.assertIsInstance(json.loads(line), dict)

    def test_derived_cache_verifies_hash_on_hit(self):
        md = self.tmp_ws / "m.md"
        md.write_text("# T\nhello", encoding="utf-8")
        calls = 0

        def parser(text: str, path: Path) -> dict:
            nonlocal calls
            calls += 1
            return {"len": len(text)}

        cache = DerivedMarkdownCache(self.tmp_ws)
        self.assertEqual(cache.get_or_parse(md, parser), {"len": 9})
        self.assertEqual(cache.get_or_parse(md, parser), {"len": 9})
        self.assertEqual(calls, 1)  # second call was a verified hit

    def test_derived_cache_reparses_on_stealth_content_change(self):
        """Content changed but size+mtime kept: sha256 must catch it."""
        md = self.tmp_ws / "m.md"
        md.write_text("aaaa", encoding="utf-8")
        cache = DerivedMarkdownCache(self.tmp_ws)
        self.assertEqual(cache.get_or_parse(md, lambda t, p: {"v": t}), {"v": "aaaa"})
        # Rewrite with same size, forge the mtime back.
        st = md.stat()
        md.write_text("bbbb", encoding="utf-8")
        import os

        os.utime(md, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertEqual(cache.get_or_parse(md, lambda t, p: {"v": t}), {"v": "bbbb"})

    def test_purge_never_overwrites_nonempty_chain_with_empty(self):
        chain_path = self.tmp_ws / "version-chain.json"
        chain_path.write_text(
            json.dumps({"chains": {"user%3Ax:theme": [{"id": "r1"}]}}),
            encoding="utf-8",
        )
        _, report = run_memory_purge([], version_chain_path=chain_path, now_ms=1_760_000_000_000)
        data = json.loads(chain_path.read_text(encoding="utf-8"))
        self.assertEqual(data["chains"], {"user%3Ax:theme": [{"id": "r1"}]})
        self.assertTrue(
            any(
                c.get("reason") == "version_chain_write_refused_empty_over_nonempty"
                for c in report.conflicts_logged
            )
        )

    def test_purge_chain_keys_resist_delimiter_injection(self):
        chain_path = self.tmp_ws / "version-chain.json"
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
        self.assertEqual(len(chains), 2)

    def test_signature_resists_delimiter_injection(self):
        a = compute_statement_signature("viewer::alice", "hobby", "x")
        b = compute_statement_signature("viewer", "alice::hobby", "x")
        self.assertNotEqual(a, b)

    def test_purge_reads_core_value_field(self):
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
        self.assertEqual(len(retained), 2)
        self.assertEqual(report.deduplicated_count, 0)

    def test_mcp_propose_persists_to_pending_store(self):
        server = StellamemMCPServer(self.tmp_ws)
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
        self.assertEqual(payload["status"], "pending")
        self.assertTrue(payload["persisted"])
        audit = (self.tmp_ws / "preference_history.jsonl").read_text(encoding="utf-8")
        self.assertIn("Vim keybindings", audit)


if __name__ == "__main__":
    unittest.main()
