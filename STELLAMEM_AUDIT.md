# Stellamem Stabilization Audit
Date: 2026-09-22. Baseline: 3efebd0779890fe33755b7936917f4e4a2275bf9.
Remote main verified with git ls-remote; equals the local main.
All 52 tracked files were read: source, templates, seven fictional diaries,
both READMEs, installer, security/schema/docs, benchmark description and HTML.
Ignored local config, backups and real memory were not copied or inspected.
Working branch: codex/stabilization-p0 in an isolated E: clone.

## Actual execution chain
OpenClaw command/new/reset/startup -> handler.js -> summarize -> daily Markdown.
sleep_purify.py independently calls the model -> parses -> normalizes -> dedup ->
checks source/threshold -> appends canonical Markdown or inbox -> day cursor.
extract.py is a separate CLI, NOT called by sleep_purify.py.
judge.py is standalone and NOT called by either compiler or router.
Adapter always reads three core files, then whole files on keyword matches.
memory.py reads Markdown and template logs; rollback copies a single backup.
Core Python has only standard-library dependencies. No package distribution,
CI, lint configuration, reproducible benchmark runner or migration exists.

## Findings (baseline; remediation tracked in STELLAMEM_EXECUTION_PLAN.md)
| ID | Priority | Evidence | Impact |
|---|---|---|---|
| P0-01 | P0 | extract.build_user and sleep_purify.main use text[:6000] | Middle/tail permanently omitted after cursor advances. |
| P0-02 | P0 | handler readSessionsForDate slice(0,500), diff.slice(-400), minute-only filter | Long messages, older batches and same-minute arrivals lost. |
| P0-03 | P0 | duplicated SYSTEM/call_llm/parse_json/normalize/detectors | Heavy-emotion and provenance changes only in extract; production drift. |
| P0-04 | P0 | normalize clears suspicious; heavy-emotion raises confidence then early returns | Model flags lost; importance conflated with confidence; incomplete provenance. |
| P0-05 | P0 | check_security does not gate sensitive/outdated; no secret detector | Credentials can enter inbox, logs, Markdown and prompts; sensitive can auto-write. |
| P0-06 | P0 | same-slot changed value appended as new in main; judge unused | Contradictory current values; empty version chain/conflict templates. |
| P0-07 | P0 | cursor template null, lexical file filtering, cursor only at end | Fresh install processes nothing; later same-day appends skipped; retries incomplete. |
| P0-08 | P0 | parse_json repairs truncated responses by discarding tail | Incomplete model output accepted as complete; silent loss. |
| P0-09 | P0 | append-only multiple files, no lock/atomic transaction | Concurrent/crashed runs can duplicate, split state or lose writes. |
| P0-10 | P0 | append_to_file trusts model fields; main lacks verified provenance | Markdown/control injection; fabricated evidence; false speaker/source attribution. |
| P0-11 | P0 | model URL unrestricted; urllib honors proxy/redirect | Local-first promise not enforced; unexpected content egress possible. |
| P0-12 | P0 | adapter identity in system context, first-N-lines personal files | Weather prompt can expose health/identity; length limit is not privacy policy. |
| P0-13 | P0 | installer catches errors then return 0; Demo return 0 on failure | False success for strangers/AI installers. |
| P0-14 | P0 | installer backup then rmtree plugin; raw string interpolation | Updates destructive despite customization; quoted names/paths break JS. |
| P0-15 | P0 | rollback no protected-core check, ambiguous basename, weak resolution | Core identity can be changed by generic --yes; symlink escape/incorrect restore. |

## Documentation and implementation conflicts
- Commit 3efebd0 says summary+pointer; hook stores neither reliable source pointers
  nor hashes and falls back to a second raw transcript. Schema claims _raw archive.
- Schema says 4-part IDs but gives 3-part examples; templates disagree about dedup
  versus revisions. No actual legacy reader/migration guarantee.
- README.en claims cloud APIs supported; Python sends no model/auth header.
- SECURITY explicitly calls secrets a convention; README wording sounds stronger.
- AI_BOOTSTRAP claims per-conversation writes to protected core; code blocks them.
- INSTALL claims install runs doctor; it does not. check probes fixed ports rather
  than selected config and always succeeds. Default install touches real OpenClaw.
- Demo descriptions disagree on cleanup; compilation failures can be reported as success.
- Benchmark README advertises match.py, absent from Git. Historical private single-
  user scores are not independently reproducible and cannot justify accuracy claims.
- THREAT_MODEL says no remote repository, despite configured GitHub origin.
- docs/index.html claims no message loss and existing SQLite cache, neither true.
- Documentation says 9 tests; actual baseline has 11. No JavaScript tests/CI.

## Privacy, reliability and platform boundaries
No repository-level evidence of real conversation files was found in tracked paths;
the seven demo inputs explicitly describe a fictional user. This is a bounded
inspection, not certification of all Git history or ignored local files.
All tested artifacts remain on E:, with no paid API, live deployment or data upload.
Python is not in PATH here; E:/Python/python.exe is 3.12.4. Node is 24.15.0.
start_server.py hardcodes CUDA0 and llama 'serve', uses a Windows-only creation flag.
No Windows/Linux/macOS CI has run. Markdown full scans are O(memory size).
No retention deletion exists; missing source should fail visibly, not invent pointers.

## Test baseline and gaps
11 unittest cases PASS on Windows with Python 3.12.4.
They mostly exercise helper functions. Missing: actual orchestration, 10/100/500
messages, overlong message, invalid/truncated model JSON, retries, same-minute/
out-of-order arrivals, secrets, sensitive overrides, installed hook, projections,
rollback, migration, atomic failures and installer subprocess exit codes.
No local language-model quality evaluation or multi-day endurance run was done.

## P1 / P2 and commercialization
P1: installed runtime smoke tests, multi-OS CI evidence, Alpha protocol, measured
scaling, external users, migrations and operational docs. Markdown can remain
canonical; optional search indexes must be rebuildable.
P2: Desktop Inbox/Timeline, opt-in encrypted sync, team access/approvals, signatures,
SBOM and support. Do not start these before correctness gates.
Commercial blocker: install and privacy claims exceed implementation, and no
independent retention/use evidence exists. Remain Alpha, no production-ready label.

## Phase record
WHAT CHANGED: audit and prioritized execution plan; no runtime changes in this commit.
WHY: establish falsifiable baseline before refactoring.
RISKS: code inspection and 11 helper tests do not establish deployment safety.
TEST RESULT: 11/11 baseline tests, remote main verified.
NEXT STEP: shared pipeline/policy, regression tests, then source pointer reliability.
