"""Run the distribution users receive, including the Windows batch launcher."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile


class JarvisConsoleTest(unittest.TestCase):
    def test_fixed_controlled_tasks(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="Jarvis 包 测试 ") as temp:
            dest = Path(temp)
            package = dest / "trial.zip"
            subprocess.run(
                [sys.executable, str(root / "experiments/jarvis/package_trial.py"), str(package)],
                check=True,
                timeout=30,
            )
            with zipfile.ZipFile(package) as z:
                self.assertIsNone(z.testzip())
                z.extractall(dest)
            installed = dest / "Jarvis-V01"
            if os.name == "nt":
                command = [os.environ["COMSPEC"], "/d", "/c", "Check.cmd"]
            else:
                command = [sys.executable, str(installed / "experiments/jarvis/local_check.py")]
            subprocess.run(
                command,
                cwd=installed,
                check=True,
                timeout=160,
                env=dict(
                    os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", JARVIS_BATCH_TEST="1"
                ),
            )
