"""P0-B: prove raw text is redacted/blocked BEFORE any LLM call.

Fixtures are synthetic. No real credentials appear in this file.
The test double records exactly what leaves the process, so any secret
reaching the model request body is a hard failure.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))
from pipeline import compile_messages
from security import contains_secret, redact
from sleep_purify import run
from source_reader import parse_messages

# Synthetic secret families. Each is clearly fake and must never reach a model.
SECRETS = {
    "openai": "sk-" + "A" * 32,
    "anthropic": "sk-ant-" + "B" * 32,
    "github_pat": "github_pat_" + "C" * 40,
    "github_classic": "ghp_" + "D" * 36,
    "google": "AIza" + "E" * 35,
    "aws": "AKIA" + "F" * 16,
    "bearer": "Bearer " + "G" * 24,
    "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",  # secret-scan: synthetic
    "private_key": "-----BEGIN PRIVATE KEY-----\nMIIEfakefakefake\n-----END PRIVATE KEY-----",  # secret-scan: synthetic
    "password": "password=fictional-hunter2",
    "cookie": "Cookie: session=synthetic-session-token",
    "refresh": "refresh_token=synthetic-refresh-value",
}


class RecordingModel:
    """Stand-in for the local model; records every payload it is asked to process."""

    def __init__(self):
        self.payloads = []

    def __call__(self, system, user):
        self.payloads.append(user)
        return "[]"


class SecretGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "daily").mkdir()
        self.config = {"memories_root": str(self.root), "chunk_chars": 512}

    def tearDown(self):
        self.tmp.cleanup()

    def test_every_secret_family_is_detected_and_redacted(self):
        for name, value in SECRETS.items():
            with self.subTest(family=name):
                self.assertTrue(contains_secret(value), name)
                self.assertNotIn(value, redact(value), name)

    def test_secret_never_reaches_the_model_request_body(self):
        for name, value in SECRETS.items():
            with self.subTest(family=name):
                messages = parse_messages(
                    "[15:32:01] user: here is my key " + value + "\n"
                    "[15:32:20] user: please keep replies concise\n",
                    "2026-01-01.md",
                )
                model = RecordingModel()
                list(compile_messages(messages, self.config, model))
                for payload in model.payloads:
                    self.assertNotIn(value, payload, name)
                    self.assertNotIn("hunter2", payload, name)

    def test_whole_secret_message_is_omitted_not_truncated(self):
        value = "-----BEGIN PRIVATE KEY-----\nFAKEBODYLINE\n-----END PRIVATE KEY-----"  # secret-scan: synthetic
        payload = json.dumps({"source": "2026-01-01.md", "data": "x" * 5000 + value + "TAIL"})
        self.assertTrue(contains_secret(payload))
        messages = parse_messages(
            "[15:32:01] user: " + value + " tail follows\n[15:32:30] user: unrelated preference\n",
            "2026-01-01.md",
        )
        model = RecordingModel()
        chunks = list(compile_messages(messages, self.config, model))
        rendered = json.dumps(chunks, ensure_ascii=False)
        self.assertNotIn("FAKEBODYLINE", rendered)
        self.assertNotIn("tail follows", rendered)

    def test_secret_never_persists_to_canonical_memory_or_inbox(self):
        secret = SECRETS["openai"]
        (self.root / "daily/2026-01-01.md").write_text(
            "[15:32:01] user: my token is " + secret + "\n[15:32:20] user: concise replies\n",
            encoding="utf-8",
        )
        seen = []

        def model(system, user):
            seen.append(user)
            return json.dumps(
                [
                    {
                        "kind": "preference",
                        "subject": "user",
                        "scope": "global",
                        "predicate": "reply",
                        "value": "concise",
                        "evidence": "concise replies",
                        "source": "user",
                        "message_index": 0,
                        "confidence": 0.99,
                    }
                ]
            )

        result = run(self.config, llm=model)
        self.assertTrue(result["ok"])
        self.assertGreaterEqual(result["blocked"], 1)
        for payload in seen:
            self.assertNotIn(secret, payload)
        for path in self.root.rglob("*"):
            if path.is_file() and "daily" not in path.parts:
                self.assertNotIn(secret, path.read_text(encoding="utf-8", errors="replace"))

    def test_scanner_does_not_flag_documentation_keywords(self):
        # Regression: identifiers that merely contain "token"/"secret" as a
        # prefix or inside a larger word are NOT credentials. Flagging them would
        # silently drop legitimate user content from memory.
        for safe in (
            "max_tokens = 4096",
            "token_count = 5",
            "blocked_secrets: 3",
            "secret = contains_secret(c)",
            '  "secret": [',
            "contains_secret(claim)",
            "I will keep the secret recipe",
            "note: the token is expired",
        ):
            self.assertFalse(contains_secret(safe), safe)

    def test_real_credential_assignments_still_detected(self):
        # The tightened pattern must still catch genuine credential-shaped keys,
        # including prefix/suffix forms. Values below are synthetic.
        for flagged in (
            "password=fictional-value",
            "api_key=synthetic-123",
            "OPENAI_API_KEY=abc123",
            "client_secret = abc",
            "access_token=AQAB",
            "refresh_token=synthetic-value",
            "SERVICE_SECRET=synthetic-value",
            "Cookie: session=synthetic",
        ):
            self.assertTrue(contains_secret(flagged), flagged)


if __name__ == "__main__":
    unittest.main()
