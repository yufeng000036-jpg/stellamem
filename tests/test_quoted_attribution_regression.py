"""Source attribution must survive extraction from reported speech."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))
from pipeline import compile_messages
from source_reader import parse_messages


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


if __name__ == "__main__":
    unittest.main()
