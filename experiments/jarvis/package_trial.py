"""Build a reproducible Windows source trial ZIP from the checked-in Git snapshot."""

import argparse
import io
import subprocess
import tarfile
import zipfile
from pathlib import Path


def package(output):
    root = Path(__file__).resolve().parents[2]
    files = [
        "compiler",
        "shared",
        "adapters/openclaw",
        "experiments/jarvis",
        "package.json",
        "LICENSE",
    ]
    data = subprocess.check_output(["git", "archive", "--format=tar", "HEAD", *files], cwd=root)

    def entry(z, name, content):
        info = zipfile.ZipInfo("Jarvis-V01/" + name)
        info.compress_type = zipfile.ZIP_DEFLATED
        z.writestr(info, content)

    with tarfile.open(fileobj=io.BytesIO(data)) as archive, zipfile.ZipFile(output, "x") as z:
        for item in archive:
            if item.isfile():
                body = archive.extractfile(item).read()
                if item.name.endswith(".cmd"):
                    body = body.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
                entry(z, item.name, body)
        for name, flags in (
            ("Start.cmd", "%*"),
            ("Check.cmd", "--verify"),
            ("ModelCheck.cmd", "--model-check"),
        ):
            entry(
                z,
                name,
                (
                    '@echo off\r\ncall "%~dp0experiments\\jarvis\\Start.cmd" '
                    + flags
                    + "\r\nexit /b %errorlevel%\r\n"
                ).encode(),
            )
        entry(z, "先读我.txt", archive.extractfile("experiments/jarvis/FIRST-STEPS.txt").read())
        entry(
            z, "COMMIT.txt", subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=root)
        )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("new_zip", type=Path)
    package(p.parse_args().new_zip)
