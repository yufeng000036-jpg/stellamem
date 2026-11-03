# Local inference failure and v2 freeze

> Status note (2026-11-04): the code later merged to main via PR #15 (`803df0d`).
> Independent quality acceptance stays unmet and the release gate stays CLOSED.
> This file's invalid v1 run is still not a quality score. Current main recount
> is 563 Python tests + 42 subtests and 8 Node tests; the 422 below is this
> commit's own check.



v1 completed 140 case attempts across four arms but generated zero successful
responses. Every main/candidate arm failed in extraction; direct/no-memory
failed at the reader. This is INVALID infrastructure evidence, not 0% memory
accuracy. Per-case failures are retained in longmemeval-v1-infrastructure.json.
No benchmark answer was available to tune prompts, labels or thresholds.

Diagnosis: cgroup memory.max=8589934592; memory.events reported oom=7 and
oom_kill=3 during the probes. The repeated four-slot process exited -9; another
retained four-slot process exited137/Killed. A one-slot/4096-token synthetic
probe returned HTTP200 with generated text and remained alive. These observations
justify reducing total KV allocation; they do not establish model quality.

v2 retains the exact model, dataset/labels, selection, per-request 16384-token
window, prompts, output budgets and acceptance thresholds. Total allocation is
one slot/16384 tokens instead of four slots/65536. Case workers queue on that
one slot. A generation preflight precedes cases and process death invalidates
the run. Linux worker/server peak RSS is read from /proc VmHWM: v1 ru_maxrss
could inherit the parent's high-water mark and is not used as worker evidence.
Old report and code remain available at 7fd5855; v2 script hashes are frozen
before this retry's inference results.

Before upload: 422 Python tests, 8 Node tests, offline demo, Ruff lint/format
(121 files), own history scan passed; staged-source Gitleaks is the upload gate.
New evaluator test refuses a health-only response as proof of inference.
No product safety checks or benchmark labels changed. Run instructions remain
in LONGMEMEVAL_FREEZE.md; use a NEW cache/work directory for v2.

Rollback: git revert <v2-freeze-commit> restores the original evaluator. It also
restores its resource and measurement limitations, so it is not a recommendation
to rerun the known failing profile. Delete only the named scratch run directory
to clear local public-data caches. Product memory and production are unaffected.
Independent quality acceptance and release gate remain CLOSED.
