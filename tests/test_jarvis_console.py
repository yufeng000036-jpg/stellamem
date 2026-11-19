"""Run the distribution users receive, including the Windows batch launcher."""

import os
from pathlib import Path
import shutil
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

            if os.name == "nt":
                self.check_portable_python(installed, dest)

    def check_portable_python(self, installed, dest):
        """Start all shipped entry points with no working python/py on PATH."""
        system = Path(os.environ["SystemRoot"]) / "System32"
        store = dest / "Microsoft" / "WindowsApps"
        store.mkdir(parents=True)
        (store / "py.exe").touch()
        node = Path(shutil.which("node")).parent
        env = dict(
            os.environ,
            PATH=os.pathsep.join(map(str, (store, node, system))),
            JARVIS_BATCH_TEST="1",
            PYTHONIOENCODING="utf-8",
        )
        env.pop("JARVIS_PYTHON", None)
        comspec = env["COMSPEC"]
        drive = Path("E:/")
        mapped = False
        if not drive.exists():
            # A disposable drive in this hosted VM. Never modify an occupied E:.
            subprocess.run(
                [comspec, "/d", "/c", "mklink", "/J", str(dest / "Python"), sys.base_prefix],
                check=True,
            )
            subprocess.run([str(system / "subst.exe"), "E:", str(dest)], check=True)
            mapped = True
        else:
            env["JARVIS_PYTHON"] = sys.executable
        try:
            for script, args, reply, marker in [
                ("Start.cmd", ["--home", str(dest / "isolated home")], "退出\n", "本地控制台"),
                ("Check.cmd", [], "", "受控场景 44/44"),
                ("ModelCheck.cmd", [], "\n", "已取消"),
            ]:
                p = subprocess.run(
                    [comspec, "/d", "/c", script, *args],
                    cwd=installed,
                    env=env,
                    input=reply,
                    encoding="utf-8",
                    errors="replace",
                    capture_output=True,
                    timeout=160,
                )
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                self.assertIn(marker, p.stdout)
            print(
                "Launcher regression: "
                + ("E:/Python automatic" if mapped else "explicit interpreter on occupied E:")
                + "; Start/Check/ModelCheck OK",
                flush=True,
            )
        finally:
            if mapped:
                subprocess.run([str(system / "subst.exe"), "E:", "/d"], check=True)
