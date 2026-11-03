# Stage C — safety repairs and module status

Branch: `fix/reliability-evidence-20261102`. Input B code SHA `8430657cd73b2e43554af6d529361f8b9354ae2d`; [run 41396619335](https://github.com/yufeng000036-jpg/stellamem/actions/runs/41396619335) is all seven jobs SUCCESS, same SHA, six OS/Python groups each 381 Python/6 Node tests. Logs read for every job. CI uses Gitleaks 8.24.3 on the latest commit, not entire history; additionally verified local 8.24.3 and 8.30.1 tree/history and eight real controls. No workflow permissions needed.

## Reproduced defects and changes

- Six governance regressions before repair: five failures and one error. Explicit null namespace incorrectly fell back to subject; collection-valued namespace raised TypeError; duplicate pending record IDs silently selected the last record; persona sync accepted a conflicting namespace; nightly picked a conflict winner by score and accepted nonfinite scores. All six now pass.
- Nightly is now a pure experimental planner: conflicting values go to `conflicts` with all candidate events; full deep-copied `source_events` retain timestamps, evidence and version fields including low-score items. No canonical writes or deletion. Text budgets keep complete lines only. Three old tests expecting a winner or a cut-off fact were corrected to retain the same counterexamples and assert the new safe semantics, not deleted.
- Eleven actual HTTP transport tests before repair: five failures and five errors. Fixed invalid/negative/duplicate Content-Length, body cap 16 KiB before read, two-second socket timeout, strict UTF-8, bounded form fields, duplicate-field rejection, non-ASCII token handling, mandatory token in handler factory, exact loopback host/port origin checks, serialized operations, generic errors and no-store/no-referrer/CSP headers. Added a stalled-body timeout/recovery test, for twelve transport tests total. Existing different-port Origin success test now uses the actual server origin; a new negative proves the different port is refused.
- Three bridge state tests failed before repair: returned nested fields mutated the store; approved copies survived TTL; audit exceptions left approved state. Bridge now returns deep copies, serializes public operations, expires pending/approved copies at the exact boundary, restores in-memory state on exceptions, and propagates audit file errors. A partially appended audit file may retain an attempted action; this in-memory planner is not a durable canonical store. Process restart persistence is intentionally not claimed.
- Lifecycle probe reproduced a core inbox P0: generic approval appended a second active revision in an occupied slot. `inbox approve` now refuses an occupied active claim slot before writing. Both old fact and new pending evidence survive; explicit conflict resolution remains required rather than guessing a replacement.
- Actual OpenClaw `project` function had no requester namespace boundary and projected expired/conflicting facts. Two added Node regressions failed before repair. It now defaults to requester `user`, enforces authoritative namespaces (null invalid), explicit task scopes, fact-time validity and unambiguous active revisions. Invalid requester/config fails closed. Default scope changes from unrestricted to **global only**, intentionally prioritizing isolation. Operators may explicitly set activeScopes; unknown legacy topic subjects require explicit namespace tagging before projection. No real memory was migrated. Unresolved conflicting pending slots are also blocked from projection, preserving the old record without presenting it as settled truth.

## Dependency map (repository static callers, not production assumptions)

| Module | Executable consumers found | Status / reason retained |
|---|---|---|
| gate.py | tests only | candidate injection-policy utility, boundary regressions retained |
| nightly.py | tests only | experimental no-write consolidation planner; no auto job wired |
| semantic_health.py | tests only | experimental cache/probe/rerank helpers; no service started |
| bridge_gui.py | tests only | experimental local ephemeral review server; CLI/store fallback retained |
| pending_batch.py | tests only | candidate plan only, apply_implemented=False |
| persona_sync.py | tests only | candidate pack only, user_library_write_implemented=False |
| adapters/openclaw/index.js | plugin registration and Node tests | executable projection path; real host not attached |
| inbox.py / sleep_purify.py / store.py / storage.py | CLI/compiler/tests | actual canonical write/approval/transaction path |

Static import searches and repository text searches found no production caller for the six candidate modules. This does not prove external callers do not exist; backward behavior changes are listed above. The unused `_loopback_origin` helper was removed after caller search and replaced by tested host+port validation. No other module removed. No two-route dead end required deletion.

## Validation / rollback / gate

Final local full run: 410 Python tests, 8 Node tests, offline demo; Ruff check/format and both scanners must be recorded at the final published code SHA. See accompanying verification results. Before/after failure counts are observations, not maturity metrics. Production services/configuration/data unchanged, no paid API calls, no safety gate disabled.

Rollback before merge: abandon branch. For surgical reversal, revert C/D code commit on this development branch; that reintroduces disclosed vulnerabilities and is not a safe production release. Baseline and B commit remain available. Preserve evidence reports when reverting.

C local safety repairs pass; remote matrix for the updated code must still be checked. D is explicitly a simulated host, not deployed OpenClaw. Eight-point maturity is NOT established by these unit tests.
