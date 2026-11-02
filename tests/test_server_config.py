import sys
import tempfile
import unittest
import json
import os
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "compiler"))

from start_server import build_cmd
from config_loader import load_config


class ServerConfigTests(unittest.TestCase):
    def test_default_context_is_16384(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.gguf"
            model.write_bytes(b"synthetic")
            cmd = build_cmd(
                {
                    "server_url": "http://127.0.0.1:8081",
                    "llama_bin": "llama-server.exe",
                    "model_name": str(model),
                }
            )
        self.assertEqual(cmd[cmd.index("-c") + 1], "16384")

    def test_explicit_gpu_layers_and_context_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.gguf"
            model.write_bytes(b"synthetic")
            cmd = build_cmd(
                {
                    "server_url": "http://127.0.0.1:8081",
                    "llama_bin": "llama-server.exe",
                    "model_name": str(model),
                    "context_size": 32768,
                    "gpu_layers": 42,
                }
            )
        self.assertEqual(cmd[cmd.index("-c") + 1], "32768")
        self.assertEqual(cmd[cmd.index("-ngl") + 1], "42")

    def test_config_rejects_output_budget_that_fills_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "memories_root": tmp,
                        "server_url": "http://127.0.0.1:8081",
                        "model_max_tokens": 8192,
                        "context_size": 8192,
                    }
                ),
                encoding="utf-8",
            )
            previous = os.environ.get("STELLAMEM_CONFIG")
            os.environ["STELLAMEM_CONFIG"] = str(config_path)
            try:
                with self.assertRaises(ValueError):
                    load_config()
            finally:
                if previous is None:
                    os.environ.pop("STELLAMEM_CONFIG", None)
                else:
                    os.environ["STELLAMEM_CONFIG"] = previous


if __name__ == "__main__":
    unittest.main()
