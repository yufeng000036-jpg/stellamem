# Stage A — frozen baseline (2026-11-02)

- Branch: `fix/reliability-evidence-20261102`.
- Latest remote main / tested baseline: `910f27d9261a01feb12110a77c88ad1c1df3f91f`; `git ls-remote` independently confirmed. No commits after the instruction baseline.
- Fresh full clone; no AGENTS.md found in the repository. Initial eight dirty paths were byte-for-byte equal to HEAD blobs: committed CRLF conflicts with `.gitattributes` LF policy. Scoped untracked `.git/info/attributes` overrides preserve the exact baseline bytes; subsequent status clean. No source changes in A.
- Linux x86_64 kernel 6.18.57, Python 3.12.14, Node 24.22.1, Git 2.51.1, Ruff 0.11.13 in a separate virtual environment. Local Node differs from CI Node 22; CI is authoritative for that matrix.
- Only synthetic fixtures and temporary stores. No production paths, credentials, services or configuration accessed. No paid API calls.

## Local commands on the frozen baseline

| Command | Exit | Result |
|---|---:|---|
| `python -m unittest discover -s tests -v` | 0 | 372 passed |
| `node --test tests/hooks.test.mjs` | 0 | 6 passed |
| `python demo/run_demo.py --offline` | 0 | 7 sources, 7 active; engineering fixture only |
| `ruff check .` | 0 | all checks passed |
| `ruff format --check .` | 1 | only tests/test_bridge_gui.py and tests/test_privacy.py |
| `python tools/secret_scan.py` | 0 | no findings under existing policy |
| `python tools/secret_scan.py --history` | 0 | no findings under existing policy; full clone, all refs |

Ruff executable is from the separate verification venv. Test output includes an expected inbox error-path diagnostic despite overall exit 0; it is not a failing test.

## Remote evidence (same SHA, logs read for all seven jobs)

Run [41394271413](https://github.com/yufeng000036-jpg/stellamem/actions/runs/41394271413), completed failure. Latest run collection and recent PRs checked: #14 is the latest merged PR; #10 is still open and contains older cumulative work already represented in main. No PR content was overwritten.

| Job | Job ID | Python / Node | Demo / current scan |
|---|---:|---|---|
| Ubuntu 3.10 | 125112127879 | 372 / 6 pass | pass / pass |
| Ubuntu 3.12 | 125112127988 | 372 / 6 pass | pass / pass |
| Windows 3.10 | 125112127979 | 372 / 6 pass | pass / pass |
| Windows 3.12 | 125112128062 | 372 / 6 pass | pass / pass |
| macOS 3.10 | 125112128072 | 372 / 6 pass | pass / pass |
| macOS 3.12 | 125112128133 | 372 / 6 pass | pass / pass |
| Quality | 125112127884 | Ruff check pass, format exit 1 | history / Gitleaks SKIPPED |

Exact quality log: `Would reformat: tests/test_bridge_gui.py`; `Would reformat: tests/test_privacy.py`; `2 files would be reformatted, 107 files already formatted`.

## Newly reproduced P0: scanner allowlist bypass

On existing `scan_text`, a generated fictional detectable key alone returns `[1]`, the same key preceded by the word `synthetic` returns `[]`, and an exact historical fixture plus that second key also returns `[]`. Thus whole-line marker / substring exemptions can suppress unrelated credentials. The zero-finding scans above do not prove the policy is safe. Regression tests and match-scoped exemption repair belong to B; no real secrets were used or printed.

Gitleaks `[allowlist]` also combines paths and regexes without an explicit AND; runtime negative tests are required before trusting it. D7–D13 details remain unknown; no unsupported closure.

## Gate and rollback

Stage A diagnostic exit: GO to B, with security P0 priority; STOP before C until B safety and CI gates pass. No production write path was started. Current score: not rescored; 8–9 is unsubstantiated while P0 and full CI remain open.

Rollback A: revert the documentation-only commit on this branch, or abandon the branch. Production rollback is unnecessary (zero changes). Baseline remains available at `910f27d9261a01feb12110a77c88ad1c1df3f91f`.
