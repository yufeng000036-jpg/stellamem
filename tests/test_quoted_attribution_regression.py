"""Source attribution must survive extraction from reported speech."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))
from pipeline import compile_messages
from source_reader import parse_messages
from store import MemoryStore


class QuotedAttributionTest(unittest.TestCase):
    def test_reported_speech_remains_quoted_when_evidence_omits_marker(self):
        messages = parse_messages(
            "[15:32:01] user: he said: I prefer black coffee\n", "2026-01-01.md"
        )

        def model(system, payload):
            return json.dumps(
                [
                    dict(
                        kind="preference",
                        subject="user",
                        scope="global",
                        predicate="coffee",
                        value="I prefer black coffee",
                        evidence="I prefer black coffee",
                        confidence=0.99,
                        source="user",
                        message_index=0,
                    )
                ]
            )

        rows = [
            c
            for batch in compile_messages(messages, {"chunk_chars": 512}, model)
            for c in batch["claims"]
        ]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["evidence_verified"])
        self.assertEqual(rows[0]["provenance"]["speaker"], "user")
        self.assertEqual(rows[0]["source"], "quoted")
        self.assertEqual(rows[0]["write_policy"], "confirm")
        self.assertFalse(rows[0]["auto_write"])
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(Path(tmp))
            store.ingest(rows[0])
            self.assertEqual(store.counts["active"], 0)
            self.assertEqual(store.counts["pending"], 1)

    def test_unquoted_user_and_assistant_keep_speaker(self):
        for role in ("user", "assistant"):
            with self.subTest(role=role):
                messages = parse_messages(
                    f"[15:32:01] {role}: I prefer black coffee\n", "2026-01-01.md"
                )

                def model(system, payload):
                    return json.dumps(
                        [
                            dict(
                                kind="preference",
                                subject="user",
                                scope="global",
                                predicate="coffee",
                                value="I prefer black coffee",
                                evidence="I prefer black coffee",
                                confidence=0.99,
                                source="user",
                                message_index=0,
                            )
                        ]
                    )

                rows = [
                    c
                    for batch in compile_messages(messages, {"chunk_chars": 512}, model)
                    for c in batch["claims"]
                ]
                self.assertEqual(rows[0]["source"], role)
                self.assertTrue(rows[0]["evidence_verified"])

    def test_assistant_markdown_sections_keep_assistant_source(self):
        # Mirrors the 2026-11-18 fixed-set flips: a single "according to"
        # in a long assistant reply used to flip every claim extracted
        # from that message to quoted. Only the marker's own line (or a
        # colon lead-in line) may downgrade a claim now.
        body = (
            "Here are some options to consider:\n"
            "\n"
            "**Online Marketplaces:**\n"
            "\n"
            "1. **Discogs**: A popular platform for buying and selling vinyl records.\n"
            "2. **eBay**: A well-established online marketplace for listing records.\n"
            "\n"
            "**Selling Tips:**\n"
            "\n"
            "* Research completed listings to determine a fair price.\n"
            "* Clean and grade your records according to their condition.\n"
        )
        messages = parse_messages(f"[10:00:00] assistant: {body}", "2026-01-01.md")

        def model(system, payload):
            return json.dumps(
                [
                    dict(
                        kind="fact",
                        subject="vinyl selling",
                        scope="global",
                        predicate="marketplace",
                        value="Discogs sells vinyl records",
                        evidence="**Discogs**: A popular platform for buying and selling vinyl records",
                        confidence=0.9,
                        source="assistant",
                        message_index=0,
                    ),
                    dict(
                        kind="fact",
                        subject="vinyl selling",
                        scope="global",
                        predicate="pricing",
                        value="Research completed listings for pricing",
                        evidence="Research completed listings to determine a fair price",
                        confidence=0.9,
                        source="assistant",
                        message_index=0,
                    ),
                    dict(
                        kind="fact",
                        subject="vinyl selling",
                        scope="global",
                        predicate="grading",
                        value="Grade records by condition",
                        evidence="Clean and grade your records according to their condition",
                        confidence=0.9,
                        source="assistant",
                        message_index=0,
                    ),
                ]
            )

        rows = [
            c
            for batch in compile_messages(messages, {"chunk_chars": 4096}, model)
            for c in batch["claims"]
        ]
        self.assertEqual(len(rows), 3)
        by_value = {r["value"]: r for r in rows}
        self.assertEqual(by_value["Discogs sells vinyl records"]["source"], "assistant")
        self.assertEqual(by_value["Research completed listings for pricing"]["source"], "assistant")
        # The marker's own line still downgrades.
        self.assertEqual(by_value["Grade records by condition"]["source"], "quoted")
        for r in rows:
            self.assertTrue(r["evidence_verified"])

    def test_user_first_line_reported_second_line_own_statement(self):
        messages = parse_messages(
            "[15:32:01] user: he said: the shop closes at nine\nI prefer black coffee\n",
            "2026-01-01.md",
        )

        def model(system, payload):
            return json.dumps(
                [
                    dict(
                        kind="fact",
                        subject="shop",
                        scope="global",
                        predicate="closing time",
                        value="the shop closes at nine",
                        evidence="the shop closes at nine",
                        confidence=0.9,
                        source="user",
                        message_index=0,
                    ),
                    dict(
                        kind="preference",
                        subject="user",
                        scope="global",
                        predicate="coffee",
                        value="I prefer black coffee",
                        evidence="I prefer black coffee",
                        confidence=0.99,
                        source="user",
                        message_index=0,
                    ),
                ]
            )

        rows = [
            c
            for batch in compile_messages(messages, {"chunk_chars": 4096}, model)
            for c in batch["claims"]
        ]
        self.assertEqual(len(rows), 2)
        by_value = {r["value"]: r for r in rows}
        reported = by_value["the shop closes at nine"]
        self.assertEqual(reported["source"], "quoted")
        self.assertEqual(reported["write_policy"], "confirm")
        self.assertFalse(reported["auto_write"])
        own = by_value["I prefer black coffee"]
        self.assertEqual(own["source"], "user")
        self.assertEqual(own["provenance"]["speaker"], "user")
        self.assertTrue(own["evidence_verified"])


if __name__ == "__main__":
    unittest.main()
