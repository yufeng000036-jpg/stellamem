"""Exercise the opt-in console in a separate interpreter and temporary workspace."""

import os
from pathlib import Path
import subprocess
import sys
import unittest


class JarvisConsoleTest(unittest.TestCase):
    def test_fixed_controlled_tasks(self):
        script = Path(__file__).resolve().parents[1] / "experiments/jarvis/local_check.py"
        subprocess.run(
            [sys.executable, str(script)],
            check=True,
            timeout=160,
            env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"),
        )
