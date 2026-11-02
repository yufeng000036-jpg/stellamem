"""CLI stdout must be UTF-8 on Windows, where Python otherwise defaults
to the console code page and printing readable Chinese JSON raises
UnicodeEncodeError (the two inbox JSON/show failures in CI run
41391359251). Synthetic placeholder data only; no real memory touched.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import inbox
from cli_output import ensure_utf8_stdout
from store import MemoryStore
from test_memory_safety import verified


class FakeStdout:
    """Mimics a Windows console stream: cp936 until reconfigured."""

    def __init__(self):
        self.encoding = "cp936"
        self.buffer = []
        self.reconfigured = None

    def reconfigure(self, encoding=None, errors=None):
        self.reconfigured = encoding
        self.encoding = encoding

    def write(self, text):
        if self.encoding != "utf-8" and any(ord(c) > 127 for c in text):
            raise UnicodeEncodeError("cp936", text, 0, 1, "not encodable")
        self.buffer.append(text)
        return len(text)

    def flush(self):
        pass


class EnsureUtf8Tests(unittest.TestCase):
    def test_reconfigures_to_utf8(self):
        fake = FakeStdout()
        with patch.object(sys, "stdout", fake):
            self.assertTrue(ensure_utf8_stdout())
        self.assertEqual(fake.reconfigured, "utf-8")
        self.assertEqual(fake.encoding, "utf-8")

    def test_tolerates_stream_without_reconfigure(self):
        class Plain:
            def write(self, text):
                pass

        with patch.object(sys, "stdout", Plain()):
            self.assertFalse(ensure_utf8_stdout())

    def test_tolerates_missing_stdout(self):
        with patch.object(sys, "stdout", None):
            self.assertFalse(ensure_utf8_stdout())


class InboxUtf8Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.mem = self.root / "memory"
        self.mem.mkdir()
        config = self.root / "config.json"
        config.write_text(
            json.dumps(
                {
                    "memories_root": str(self.mem),
                    "server_url": "http://127.0.0.1:8081",
                }
            ),
            encoding="utf-8",
        )
        self.env = patch.dict("os.environ", {"STELLAMEM_CONFIG": str(config)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.tmp.cleanup)

    def seed(self):
        store = MemoryStore(self.mem)
        candidate = verified(
            kind="decision",
            value="决定改用本地模型，因为云端太慢",
            confidence=0.5,
        )
        store.ingest(candidate)
        store.commit()
        return candidate

    def run_cli(self, fake, argv):
        old = sys.stdout
        sys.stdout = fake
        try:
            return inbox.main(argv)
        finally:
            sys.stdout = old

    def test_list_json_under_non_utf8_stdout(self):
        self.seed()
        fake = FakeStdout()
        self.assertEqual(self.run_cli(fake, ["list", "--json"]), 0)
        self.assertEqual(fake.encoding, "utf-8")
        records = json.loads("".join(fake.buffer))
        self.assertEqual(len(records), 1)
        # Readable Chinese is kept, not escaped to \uXXXX.
        self.assertIn("决定改用本地模型", "".join(fake.buffer))

    def test_show_under_non_utf8_stdout(self):
        candidate = self.seed()
        from storage import load_records

        from store import INBOX

        cid = load_records(self.mem / INBOX)[1][0]["claim_id"]
        self.assertTrue(cid)
        fake = FakeStdout()
        self.assertEqual(self.run_cli(fake, ["show", cid]), 0)
        self.assertIn("决定改用本地模型", "".join(fake.buffer))
        self.assertEqual(candidate["value"], "决定改用本地模型，因为云端太慢")


if __name__ == "__main__":
    unittest.main()
