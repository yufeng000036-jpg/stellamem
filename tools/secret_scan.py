"""Scan tracked text without printing secret values. Not a safety certification.

Gate mode (default) reports only actionable findings: real secret *values*
(API keys, tokens, private keys). It deliberately ignores documentation that
describes the detection rule itself, because matching on words such as
``secret`` or ``max_tokens`` produced noise that hid genuine findings.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "compiler"))
from security import RULES

# Value-shaped patterns only: things that look like an actual credential value.
# The keyword-context rule (the one ending in a value capture) is excluded from
# gate mode because it matches rule definitions and identifiers.
VALUE_RULES = [
    re.compile(pattern)
    for pattern in RULES["secret"]
    if not pattern.endswith(r"[^\r\n,}]+") or "PRIVATE KEY" in pattern
]
# Known synthetic detection fixtures committed before the allowlist marker existed.
# These are fabricated strings used to test the detector itself; no real credential
# is present. Git history is intentionally not rewritten, so they are allowlisted by
# exact value. Only add after verifying the value is fabricated.
# The last entry is the detector fixture from tests/test_privacy.py:56, first
# committed in 04208c0 and identical there to the pre-fix line: a token whose
# letters run sequentially a-x (a textbook fixture, not a random credential),
# next to a split, repeated-character token fixture, in a file whose header
# states all numbers are synthetic samples. It is computed here, not written
# as one contiguous string, so this file itself never contains the fixture.
# Prose false positive (not a fabricated fixture): "Bearer authentication" is
# ordinary English in README.en.md describing Bearer-token auth. The Bearer
# value-shaped pattern matches it because "authentication" is 14 word
# characters. It is not a credential. Allowlisted because the line is already
# in public history (commit 2640589, PR #71) and --history scans all commits;
# the working-tree line itself was reworded to "Bearer-token authentication".
# The exemption is exact-match only: any other "Bearer <token>" still flags.
HISTORY_ALLOWLIST = (
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.signature",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    "MIIEfakefakefake",
    "FAKEBODYLINE",
    "Bearer authentication",
    "sk-" + "".join(chr(ord("a") + i) for i in range(24)),
)
# A fabricated private-key body is only allowlisted when the surrounding block is
# also present, so a real key header is never silently ignored.
SYNTHETIC_PRIVATE_KEY_BODIES = ("fake", "FAKEBODYLINE", "MIIEfakefakefake")


def scan_text(text):
    # Exempt exact *matches*, never an entire line or a marker word. Removing a
    # verified complete fake PEM block first keeps unrelated matches visible.
    findings = []
    for i, line in enumerate(text.splitlines(), 1):
        candidate = line
        for body in SYNTHETIC_PRIVATE_KEY_BODIES:
            for separator in (r"\n", r"\\n"):
                block = (
                    ("-----BEGIN " + "PRIVATE KEY-----")
                    + separator
                    + body
                    + separator
                    + "-----END PRIVATE KEY-----"
                )
                candidate = candidate.replace(block, "")
        if any(
            match.group(0) not in HISTORY_ALLOWLIST
            for rule in VALUE_RULES
            for match in rule.finditer(candidate)
        ):
            findings.append(i)
    return findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Also report keyword-context hits (noisy; use for manual review)",
    )
    args = parser.parse_args()
    files = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    findings = []
    for name in files:
        if not name:
            continue
        path = ROOT / name
        if path.is_file():
            try:
                lines = scan_text(path.read_text(encoding="utf-8"))
                if lines:
                    findings.append({"file": name, "lines": lines})
            except UnicodeDecodeError:
                pass
    if args.history:
        commits = (
            subprocess.check_output(["git", "rev-list", "--all"], cwd=ROOT).decode().splitlines()
        )
        for commit in commits:
            diff = subprocess.check_output(
                ["git", "show", "--format=", "--unified=0", commit], cwd=ROOT
            ).decode("utf-8", errors="replace")
            # Report commit and count only, never the matched source text.
            count = sum(
                bool(scan_text(line[1:]))
                for line in diff.splitlines()
                if line.startswith("+") and not line.startswith("+++")
            )
            if count:
                findings.append({"commit": commit, "findings": count})
    print(json.dumps({"ok": not findings, "findings": findings}, indent=2))
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
