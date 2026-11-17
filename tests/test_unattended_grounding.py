"""Unattended writes require value-level evidence, not merely a matching quote."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))
from pipeline import compile_messages
from source_reader import parse_messages
from store import MemoryStore


class UnattendedGroundingTests(unittest.TestCase):
    def test_contradicted_value_with_verbatim_quote_stays_pending(self):
        messages = parse_messages("[15:32:01] user: I like tea\n", "2026-01-01.md")

        def model(system, payload):
            return json.dumps(
                [
                    dict(
                        kind="fact",
                        subject="user",
                        scope="global",
                        predicate="tea",
                        value="I dislike tea",
                        evidence="I like tea",
                        confidence=0.99,
                        source="user",
                        message_index=0,
                    )
                ]
            )

        claims = [
            c
            for batch in compile_messages(messages, {"chunk_chars": 512}, model)
            for c in batch["claims"]
        ]
        self.assertEqual(len(claims), 1)
        self.assertTrue(claims[0]["evidence_verified"])
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(Path(tmp))
            store.ingest(claims[0])
            self.assertEqual(store.counts["active"], 0)
            self.assertEqual(store.counts["pending"], 1)

    def test_exact_user_quote_still_auto_writes(self):
        messages = parse_messages("[15:32:01] user: I like tea\n", "2026-01-01.md")

        def model(system, payload):
            return json.dumps(
                [
                    dict(
                        kind="fact",
                        subject="user",
                        scope="global",
                        predicate="tea",
                        value="I like tea",
                        evidence="I like tea",
                        confidence=0.99,
                        source="user",
                        message_index=0,
                    )
                ]
            )

        claims = [
            c
            for batch in compile_messages(messages, {"chunk_chars": 512}, model)
            for c in batch["claims"]
        ]
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(Path(tmp))
            store.ingest(claims[0])
            self.assertEqual(store.counts["active"], 1)
            self.assertEqual(store.counts["pending"], 0)
