# Local verification log
## 2026-09-22: shared compiler and transactional governance
WHAT CHANGED: source_reader/pipeline shared by extract and sleep_purify, strict
model parser, independent policy, protected identities, version/conflict routing,
source content cursor, one writer lock, journal recovery and rollback primitives.
WHY: remove duplicate rules and permanent prefix/day-cursor data loss.
TEST RESULT: 29 unittest cases PASS, Python 3.12.4 on Windows; includes 10/100/500
message coverage, 15000-character message, failed middle chunk/retry, same-day
append, secret no-egress/no-write, source attribution, conflict/supersession,
interrupted transaction recovery, rollback conflict and Windows concurrent lock.
RISKS: deterministic fake-model responses test engineering behavior only.
Rule-based sensitivity and injection detection are incomplete; no quality claim.
New schema-v2 IDs differ from legacy slugs; legacy remains non-projectable until
reviewed migration. Existing user files have not been migrated.
NEXT STEP: real pointer hook, bounded claim projection, safe installation/CLI and
installed synthetic integration probes. No host deployment or remote CI yet.

## 2026-09-22 (release pass): approval loop, real model, crash and scan results

WHAT CHANGED: tightened the credential-keyword rule; made the secret scanner
report only actionable findings; added the inbox approval CLI; added test suites
for the secret gate, source pointers, crash recovery and relation scaling.
WHY: a rule that flagged `max_tokens` silently dropped legitimate user content;
the scanner was too noisy to gate CI; there was no human approval loop.

TEST RESULT (all re-run on Python 3.12.4 / Node 22.22.3, Windows 11):
- Python: 78 unittest cases PASS (`python -m unittest discover -s tests`).
  Includes the new P0 suites: test_secret_gate (5), test_source_pointer (11),
  test_crash_recovery (8), test_relation_scaling (3), test_inbox (14).
- JavaScript: 3 groups PASS (`node --test tests/hooks.test.mjs`).
- Offline demo: PASS (`python demo/run_demo.py --offline`; 7 sources, 7 active).
- Installer sandbox (temp dir): check FAILs on missing config (exit 1) as
  designed; install --dry-run plans 20 writes without touching disk (exit 0);
  full install creates the tree and returns 0.
- Migration (synthetic legacy file): preview non-destructive (2 candidates,
  applied=false); apply is transactional (applied=true); legacy originals
  preserved byte-for-byte; both candidates land pending, not auto-approved;
  `memory.py rollback <txid> --yes` removes them cleanly.
- Secret scan: PASS on working tree and full Git history (`tools/secret_scan.py`
  and `--history`, both exit 0). No real credential present; the two historical
  hits were synthetic detection fixtures, now explicitly allowlisted.
- Lint: `ruff check .` clean; `ruff format --check .` clean.

REAL MODEL: VERIFIED (engineering behaviour), isolated. Ran a local
llama.cpp server with Qwen3-4B on a test port against a
temporary memory root, 9 synthetic scenarios. No secret leak, no false write,
provenance present on every claim, sensitive content forced to pending.
Observed problems were recorded in the run notes (kind drift,
filler claim from a no-memory question, zero-claim recall on repetitive input,
and an English temporary-emotion phrasing not flagged). None caused a bad write.
This is NOT an accuracy claim.

OPENCLAW HOST: DEFERRED. The installed hook and adapter import and the adapter's
projection was exercised against synthetic records, but the full chain
(conversation -> hook -> daily/source -> compiler -> memory -> adapter -> next
conversation) has not been run in a real OpenClaw host. The available host is the
user's live one and was not modified.

REMOTE CI: READY FOR REMOTE CI. `.github/workflows/ci.yml` runs Python tests,
JS tests, offline demo, secret scan, ruff and gitleaks on Ubuntu/Windows/macOS
with Python 3.10 and 3.12. Nothing was pushed; GitHub runner steps are unverified
by design.

RISKS: real-model extraction quality is untuned (4B model); rule-based
sensitivity/injection detection is incomplete; protected core files
(core/identity.md, core/user-core.md) cannot be approved through the inbox and
must be edited by hand; existing user files have not been migrated.

## 2026-09-22: D-drive large-diary repair handoff

Baseline: D-drive main `bf3a5f8`; repair branch `codex/large-diary-truncation-fix`.
The engineering changes through `7ec6ad3` were replayed without merging main.
Published defaults now use `chunk_chars=2500`, `model_max_tokens=8192` and
`context_size=16384`. The actual ignored local configuration was backed up on
the E drive, then set explicitly to those values with `gpu_layers=99` for the
existing Qwen3-4B/8GB machine. The backup contains local paths and must not be
committed or shared.

Final local gate after the reliability fixes: 96 Python tests PASS,
5 JavaScript groups PASS, Ruff lint/format PASS and tracked-tree secret scan
PASS. The suite covers truncation splitting, invalid-JSON bounded retry and
splitting, the retry floor, Secret Gate ordering, provenance offsets, atomic
failure and cursor idempotence.

REAL MODEL: PASS on Qwen3-4B with a 16384-token server context. Synthetic dense
inputs produced these complete results:
- 2000 characters: 1 model call, 1 claim, provenance/offset 1/1.
- 4000 characters: 4 model calls after retry, 125 claims,
  provenance/offset 125/125.
- 6000 characters: 4 model calls after retry, 188 claims,
  provenance/offset 188/188.

REAL LARGE SOURCE: PASS in a local E-drive isolation root. The source copy was
byte-identical to the original `2026-09-20.md`; no source text was printed or
sent off-device. The first two full runs exposed repeatable invalid JSON at
top-level chunk 5. Both runs returned `sources=0`, `chunks=0`, left no cursor and
wrote no candidate records. After adding one same-size retry followed by the
same lossless boundary split used for truncation, the source completed as 8/8
chunks with 141 pending candidates and 1 blocked secret-bearing message.
Post-commit checks found provenance and integer offsets on 141/141 candidates,
matching source hashes in the cursor and status, and no transaction journal.
An immediate second run returned `sources=0`, proving cursor idempotence.

The isolation root is
`E:\Stellamem-Engineering\.work\acceptance-20260920` and contains private
source material; keep it local and obtain user approval before deleting it.
The real memory root was not modified during this verification.
