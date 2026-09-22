import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("installer", REPO / "setup.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
sys.path.insert(0, str(REPO / "compiler"))
sys.path.insert(0, str(REPO / "demo"))
from fixture_model import FixtureServer
from model_client import call_llm, ModelError
from source_reader import content_hash, read_daily, SourceError
from migrate import plan


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.mem = self.root / "memory"
        self.cfg = self.root / "config.json"
        self.args = ["install", "--mem-root", str(self.mem), "--config", str(self.cfg),
                     "--out-dir", str(self.root / "report")]

    def tearDown(self):
        self.tmp.cleanup()

    def test_dry_run_then_install_and_repeat_preserves_memory(self):
        self.assertEqual(installer.main(self.args + ["--dry-run"]), 0)
        self.assertFalse(self.mem.exists())
        self.assertEqual(installer.main(self.args), 0)
        f = self.mem / "core/preferences.md"
        f.write_text("user customization", encoding="utf-8")
        self.assertEqual(installer.main(self.args), 0)
        self.assertEqual(f.read_text(), "user customization")
        self.assertEqual(installer.main(["check", "--config", str(self.cfg), "--offline"]), 0)

    def test_missing_config_and_disk_failure_are_nonzero(self):
        self.assertNotEqual(installer.main(["check", "--config", str(self.cfg), "--offline"]), 0)
        with patch.object(installer, "atomic_write", side_effect=OSError("synthetic")):
            self.assertNotEqual(installer.main(self.args), 0)

    def test_conflicting_config_is_not_overwritten(self):
        self.cfg.write_text(json.dumps({"memories_root": str(self.root / "different"), "server_url": "http://localhost"}))
        before = self.cfg.read_bytes()
        self.assertNotEqual(installer.main(self.args), 0)
        self.assertEqual(self.cfg.read_bytes(), before)
        self.assertFalse(self.mem.exists())

    def test_installed_js_imports_and_custom_files_refused(self):
        host = self.root / "test-host"
        args = self.args + ["--oc-root", str(host)]
        self.assertEqual(installer.main(args), 0)
        hook = host / "hooks/daily-autolog/handler.js"
        adapter = host / "extensions/stellamem-adapter/index.js"
        import shutil
        if not shutil.which("node"):
            self.skipTest("Node is optional for compiler")
        script = "await import(" + json.dumps(hook.as_uri()) + "); await import(" + json.dumps(adapter.as_uri()) + ");"
        run = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        hook.write_text("custom content", encoding="utf-8")
        self.assertNotEqual(installer.main(args + ["--upgrade"]), 0)
        self.assertEqual(hook.read_text(), "custom content")
        self.assertFalse((host / "openclaw.json").exists())

    def test_model_http_client_against_actual_local_server(self):
        with FixtureServer() as server:
            cfg = {"server_url": server.url + "/v1", "model_id": "synthetic-fixture"}
            content = call_llm("extract", json.dumps([{"speaker": "user", "text": "fictional",
                               "timestamp": "2026-01-01"}]), config=cfg)
            self.assertEqual(json.loads(content)[0]["value"], "fictional")

    def test_pointer_validates_content_speaker_and_rotation(self):
        sessions = self.root / "sessions"
        sessions.mkdir()
        self.mem.mkdir()
        daily = self.mem / "2026-01-01.md"
        daily.write_text("summary only")
        source = sessions / "session.jsonl"
        entry = {"type": "message", "id": "msg", "timestamp": "2026-01-01T15:32:01Z",
                 "message": {"role": "user", "content": [{"text": "complete original"}]}}
        source.write_text(json.dumps(entry), encoding="utf-8")
        pointer = {"session_file": source.name, "entry_index": 0, "message_id": "msg",
                   "timestamp": entry["timestamp"], "speaker": "user", "content_hash": content_hash("complete original")}
        daily.with_suffix(".sources.json").write_text(json.dumps({"schema_version": 2, "pointers": [pointer]}))
        config = {"sessions_dir": str(sessions)}
        self.assertEqual(read_daily(daily, config)[0]["text"], "complete original")
        rotated = source.with_name(source.name + ".reset.test")
        source.rename(rotated)
        self.assertEqual(read_daily(daily, config)[0]["text"], "complete original")
        rotated.write_text(json.dumps(dict(entry, message={"role": "assistant", "content": [{"text": "complete original"}]})))
        with self.assertRaises(SourceError):
            read_daily(daily, config)

    def test_legacy_migration_is_pending_and_non_destructive(self):
        self.assertEqual(installer.main(self.args), 0)
        f = self.mem / "core/preferences.md"
        f.write_text("- `claim-id:pref.user.reply` **reply**: concise (confidence 0.99)\n", encoding="utf-8")
        before = f.read_bytes()
        claims, blocked, unparsed = plan(self.mem)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["scope"], "global")
        self.assertFalse(claims[0]["auto_write"])
        self.assertEqual(f.read_bytes(), before)
