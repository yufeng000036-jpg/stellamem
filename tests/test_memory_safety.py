import importlib
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest
import atexit


REPO = pathlib.Path(__file__).resolve().parents[1]
COMPILER = REPO / "compiler"
sys.path.insert(0, str(COMPILER))

_bootstrap_root = pathlib.Path(tempfile.mkdtemp(prefix="stellamem-bootstrap-"))
atexit.register(shutil.rmtree, _bootstrap_root, ignore_errors=True)
_config = _bootstrap_root / "config.json"
_config.write_text(
    json.dumps(
        {
            "memories_root": str(_bootstrap_root / "memories"),
            "server_url": "http://127.0.0.1:1",
        }
    ),
    encoding="utf-8",
)
os.environ["STELLAMEM_CONFIG"] = str(_config)

sleep_purify = importlib.import_module("sleep_purify")
extract = importlib.import_module("extract")


def make_claim(**overrides):
    claim = {
        "kind": "preference",
        "subject": "user",
        "scope": "global",
        "predicate": "response_length",
        "value": "concise",
        "evidence": "user explicitly requested concise replies",
        "confidence": 0.99,
        "source": "user",
    }
    claim.update(overrides)
    return sleep_purify.normalize_claim(claim)


class MemorySafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        (root / "system").mkdir(parents=True)
        (root / "core").mkdir()
        (root / "long-term").mkdir()
        (root / "semantic").mkdir()
        sleep_purify.ROOT = str(root)
        sleep_purify.INBOX = str(root / "system" / "memory-inbox.md")
        sleep_purify.REVIEW_LOG = str(root / "system" / "review-log.md")
        sleep_purify._INBOX_SEQ = 0

    def tearDown(self):
        self.tmp.cleanup()

    def test_scope_defaults_to_global(self):
        claim = make_claim(scope="")
        self.assertEqual(claim["scope"], "global")

    def test_scope_is_part_of_deduplication_key(self):
        global_claim = make_claim(scope="global")
        project_claim = make_claim(scope="project:stellamem")
        self.assertNotEqual(
            sleep_purify.claim_key(global_claim),
            sleep_purify.claim_key(project_claim),
        )

    def test_core_identity_kinds_are_always_blocked(self):
        for kind in ("identity", "user-core"):
            blocked, reason = sleep_purify.check_security(make_claim(kind=kind))
            self.assertTrue(blocked)
            self.assertIn("逐字确认", reason)

    def test_non_user_source_requires_review(self):
        blocked, _ = sleep_purify.check_security(make_claim(source="quoted"))
        self.assertTrue(blocked)

    def test_every_kind_has_an_explicit_threshold(self):
        self.assertEqual(set(sleep_purify.THRESHOLDS), sleep_purify.VALID_KINDS)

    def test_formal_write_persists_four_part_claim_id(self):
        claim = make_claim(scope="project:stellamem")
        rel = "core/preferences.md"
        sleep_purify.append_to_file(rel, claim)
        text = pathlib.Path(sleep_purify.ROOT, rel).read_text(encoding="utf-8")
        self.assertIn("`claim-id:", text)
        self.assertIn(".project_stellamem.", text)
        self.assertIn(sleep_purify.claim_key(claim), sleep_purify.load_existing_keys())

    def test_inbox_participates_in_deduplication(self):
        claim = make_claim(scope="channel:private")
        sleep_purify.append_inbox(claim)
        self.assertIn(sleep_purify.claim_key(claim), sleep_purify.load_existing_keys())

    def test_review_log_is_not_named_false_write(self):
        claim = make_claim()
        sleep_purify.append_review_log(claim, "test")
        self.assertTrue(pathlib.Path(sleep_purify.REVIEW_LOG).exists())
        self.assertFalse(pathlib.Path(sleep_purify.ROOT, "system", "false-write-log.md").exists())

    def test_long_text_prompt_has_no_minimum_claim_count(self):
        prompt = extract.build_user("x" * 5000, "sample.md")
        self.assertIn("没有最低条数", prompt)
        self.assertNotIn("15 条以上", prompt)
        self.assertNotIn("10 条以上", prompt)


if __name__ == "__main__":
    unittest.main()
