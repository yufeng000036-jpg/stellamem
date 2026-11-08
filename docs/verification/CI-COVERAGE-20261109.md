# Task 8: accurate engineering coverage and release boundaries

Baseline: `e4f5cd9eea8cc2baf128845836af26efe4181092`, CI
https://github.com/yufeng000036-jpg/stellamem/actions/runs/42475792853.
This branch is independent of PRs #100 (score audit) and #101 (MCP faults).

## Confirmed gaps and changes

README listed both 868 and 901 Python tests plus 31 Node as current.
Fresh baseline execution actually ran 925 core Python and 45 Node tests.
The CI command `unittest discover -s tests` did not discover
`memory_service/tests`, a separate module namespace with real authorization,
temporal, HTTP, backup, and persistence regressions.

Running that omitted suite in a fresh checkout produced **101 errors in
101 executions**: both setup classes required an ignored, nonexistent
`memory_service/work` directory. They now use OS temporary directories.
No production directories or database paths change.

Discovery also collected `test_service.MemoryTests` twice because
`test_temporal.py` imported the class into its module globals. Importing the
module instead preserves **exactly the same 71 unique test IDs** and removes
30 duplicate executions. The repaired suite runs 71/71 successfully.
The assertion comparing sorted unique IDs before and after passed; no
tests were deleted to obtain a green result.

CI now runs the service suite in a separate Python process, avoiding
collisions between `compiler/store.py` and `memory_service/store.py`.
It also explicitly runs the existing versioned negative-memory runner
and frozen-evaluation hash/byte check in every OS/Python matrix job.
These fixtures require no cloud, model, private data, or API budget.
README links to per-commit CI output, which generates actual test counts
and skips automatically; it no longer maintains stale current totals.
Historical release counts and historical benchmark results are retained.

## Verification

Fresh Linux/Python 3.12.14, Node 24.22.1, Ruff 0.11.13:

| Check | Baseline | Candidate |
| --- | --- | --- |
| core Python | 925 pass | 925 pass |
| service Python | not in CI; fresh local 101 errors | 71 unique tests pass, no missing IDs |
| Node | 45 pass | 45 pass |
| negative controls | 9/9 pass, indirectly tested | 9/9 pass, explicit CI step |
| frozen evaluation | checks in tests | hash/byte checks pass, explicit CI step |
| Ruff, offline demo, history secret scan | pass | pass |

Hosted CI must additionally pass on Windows/macOS at the candidate SHA.
Engineering net improvement: a previously omitted safety/persistence suite
can now run in a clean checkout and gates future changes. Tests added to CI
are not evidence of improved answer accuracy. No runtime code changes.

The initial candidate CI exposed a second pre-existing dependency gap:
[macOS/Python 3.10 run](https://github.com/yufeng000036-jpg/stellamem/actions/runs/42494375649)
failed all 71 service tests at Store initialization's FTS5 `secure-delete`
command (`store.py:159`, SQLite SQL logic error). SQLite introduced that
command in [3.42.0](https://www.sqlite.org/releaselog/3_42_0.html).
The service therefore requires Python linked to SQLite >=3.42 with FTS5;
older runtime bundles cannot provide its deletion guarantee. CI prints
the original runtime version and verifies the actual FTS5 capability on
every platform before running tests. It does not catch/ignore the
security-setting error or skip tests.

**Final task status: BLOCKED, draft, not accepted for merge.** Follow-up
[run 42494947972](https://github.com/yufeng000036-jpg/stellamem/actions/runs/42494947972)
confirmed both Windows and macOS Python 3.10.11 load SQLite 3.40.1.
An attempted Homebrew library-path override did not change the macOS
runtime, even when invoking Python directly; that ineffective setup was
removed. The direct Python CI shell is retained to avoid PowerShell quoting
errors. No third runtime workaround, unsupported security fallback, removed
matrix job or hidden skip is introduced. The next separate engineering task
is to supply and verify a supported Python/SQLite runtime on these two
platforms. Until then, this PR deliberately exposes the unmet dependency.
The earlier local green result does not establish cross-platform acceptance.

Reproduce using the three commands in README plus:

```sh
node benchmark/longmemeval/negative_samples.mjs
python benchmark/longmemeval/freeze.py --verify
```

Rollback: revert the PR commit. No migration/deployment is involved.

## Current release and work queue boundaries

Release gates remain CLOSED. Fourth-round historical Recall@5=0.857 meets
the 0.80 threshold for that run; correct refusal=24/30 (0.80) fails 0.90.
Independent semantic support >=95% and fact precision >=95% were not
measured. Engineering CI does not fill these gaps. PR #100's deterministic
audit corrects historical score artifacts separately; it does not supply
independent semantic entailment labels.

Ordered intake review of the eight tasks:

1. Historical mislabels have a separate candidate in PR #100. Raw route-B
   judge runner/responses remain unavailable; full integration is open.
2. `adapters/openclaw/index.js` relevance already scores evidence (PR #78),
   sorts by score, then caps 3 per kind / 12 total and chars. Claim CSVs
   retain IDs and drop reasons, not every candidate's text/context. They
   cannot prove unchanged false injection under a new ranking. No algorithm
   is promoted without original cache/sidecars and held-out controls.
3. Frozen worker builds reference IDs from projected claims and validates
   membership; it does not establish entailment. Raw evidence/answer pairs
   are required before changing reader logic or measuring support.
4. Extraction prompt excludes assistant from its source enumeration;
   pipeline restores actual speaker, and projection explicitly allows user
   only. PR #83 locks that policy; #92 confirms missing assistant claims
   already at extraction. Do not remove that guard alone: reviewed,
   separately attributed history evidence needs full-chain evaluation.
5. PR #92 shows near-miss evidence is the failure mode. PR #94 rejects
   global CoT (5/10 positive controls retained) and unvalidated semantic
   judge rollout. Neither is enabled. No fresh paid experiment was run.
6. Frozen worker has a 32 logical extraction-call bound and independent
   answer budget. Pipeline already splits truncated outputs/retries
   malformed ones. No cap increase or unsupported cost-saving claim;
   full cache is required to attribute the 32 historical extraction errors.
7. PR #101 verifies MCP fault behavior with isolated SQLite fixtures and
   compares authorization/gate behavior; no production host changed.
8. This PR closes the missing-service-CI and stale-count issues, not the
   absent independent quality gate. Deployment SHA cannot be confirmed
   from this isolated workspace; no production upgrade attempted.

To resume tasks 2–6 without new spending, provide access to the public
evaluation run's results.json, complete answer/extraction cache, oracle,
and projection sidecars (plus the external route-B runner). Retain original
files read-only, verify hashes and cache fidelity before any candidate
experiment. A new paid 500-question run still requires budget verification
and explicit authorization. No private memories are needed.
