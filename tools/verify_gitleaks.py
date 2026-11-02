"""Exercise real Gitleaks with synthetic files; no production input or network.

Requires an explicit executable. Missing executable or a missed negative is a
failure, never a skip. Full repository current/history scans remain separate.
"""

import argparse
import importlib.util
import json
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gitleaks", required=True)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("scanner", ROOT / "tools/secret_scan.py")
    scanner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scanner)
    allowed = scanner.HISTORY_ALLOWLIST[1]
    other = allowed.rsplit(".", 1)[0] + "." + "otherGeneratedSignature9Z8Y7X6W"
    # Same detector rule and exact same path: proves AND, not path-wide ignore.
    cases = [
        ("audited_fixture", "tests/test_secret_gate.py", allowed, 0),
        ("other_token_same_path", "tests/test_secret_gate.py", other, 1),
        ("same_fixture_other_path", "unrelated.py", allowed, 1),
        ("ordinary_text", "tests/test_secret_gate.py", "ordinary text", 0),
        ("allowed_plus_other", "tests/test_secret_gate.py", allowed + " " + other, 1),
        ("marker_cannot_hide", "tests/test_secret_gate.py", "synthetic " + other, 1),
        (
            "other_private_key",
            "tests/test_secret_gate.py",
            "-----BEGIN "
            + "PRIVATE KEY-----\n"
            + ("aG9wZUluU3ludGhldGljVGVzdERhdGE5NzY1NDMyMTA=" * 4)
            + "\n-----END PRIVATE KEY-----",
            1,
        ),
        (
            "legacy_sequence_no_exemption_needed",
            "tests/test_privacy.py",
            scanner.HISTORY_ALLOWLIST[-1],
            0,
        ),
    ]
    results = []
    for name, relative, value, expected in cases:
        with tempfile.TemporaryDirectory(prefix="stellamem-secret-control-") as directory:
            root = Path(directory)
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding="utf-8")
            report = root.parent / (root.name + ".json")
            try:
                process = subprocess.run(
                    [
                        args.gitleaks,
                        "dir",
                        ".",
                        "--no-banner",
                        "--redact=100",
                        "--config=" + str(ROOT / ".gitleaks.toml"),
                        "--report-format=json",
                        "--report-path=" + str(report),
                    ],
                    capture_output=True,
                    cwd=root,
                    text=True,
                    timeout=30,
                )
                findings = json.loads(report.read_text()) if report.exists() else None
                ok = process.returncode == expected and findings is not None
                if expected == 1:
                    ok = ok and len(findings) > 0
                results.append(
                    {
                        "case": name,
                        "expected_exit": expected,
                        "exit_code": process.returncode,
                        "ok": ok,
                    }
                )
            finally:
                report.unlink(missing_ok=True)
    print(json.dumps({"ok": all(r["ok"] for r in results), "cases": results}, indent=2))
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
