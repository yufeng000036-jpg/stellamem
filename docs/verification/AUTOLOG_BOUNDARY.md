# Automatic intake boundary — 2026-11-03

> Status note (2026-11-04): PR #15 is merged at `803df0d`. That merge is not a
> production deployment. Stage E remains NOT MET and Stage F remains CLOSED.
> Counts in this file (423 / 426 Python, 8 Node) are this run's evidence, not
> the current main recount (563 Python tests + 42 subtests, 8 Node). See README.



Resume baseline: PR #15 head 353e5e0, main 910f27d. All seven baseline jobs in
run 41405907440 succeeded; all logs checked (423 Python / 8 Node per matrix job).
The prior temporary environment was unavailable after interruption. Published
commits are intact; unfinished local source diagnostics were reconstructed.
No final v2 LongMemEval result or model cache was recoverable. The frozen 140
official cases remain published, but the interrupted run is NOT a quality pass.

## Evidence-based reduction

Reference graph: setup.py copies hooks/daily-autolog/{HOOK.md,handler.js} and
shared/runtime.mjs; OpenClaw dispatches command:new/reset and gateway:startup;
handler calls writeDaily -> readPointers -> JSONL files -> .sources.json;
compiler/source_reader.py verifies those original pointers. Tests/hooks.test.mjs
imports readPointers/writeDaily and covers rotation/idempotency. These are public
compatibility surfaces, not proven dead code. No downstream usage inventory is
available; deleting them would be unjustified.

Instead, the generated snippet now explicitly disables only daily-autolog and
does not toggle the host's global internal-hook switch. Approved-memory projection
and its explicit conversation-access grant are preserved. Existing live config
is never read or rewritten. Automatic extraction jobs elsewhere are not disabled
or silently changed. This reduces a confirmed misleading default, not test scope.

The current host uses agents/main/agent/openclaw-agent.sqlite. The legacy capture
hook only understands JSONL. Read-only setup diagnostics now fail for this known
SQLite layout or a missing configured JSONL directory. An existing directory is
NOT_VERIFIED, not a success claim. Custom host layouts need explicit inspection;
this diagnostic does not claim universal storage-format detection.

## Validation and remaining blocker

Three new regressions failed before repair (one assertion, two missing-diagnostic
errors). After repair: 426 Python tests, 8 Node tests, offline demo, Ruff 0.11.13
lint/format, own history secret scan pass. Real-host rerun evidence is stored in
real-host-source-results.json: the 18 transport/safety cases are separate from
the source-capture verdict. Expected diagnostic failure is not successful intake.
Standard CI does not execute the opt-in real-host harness.

A supported SQLite source adapter with authenticated ownership, stable original
message pointers, hash verification, bounded reads and restart semantics is still
required. We do not replace missing original evidence with a summary, guess a
private database schema, or treat fixture extraction as actual model accuracy.
Memory-quality and complete-host acceptance stay NOT_MET; release stays closed.

## Migration / fallback / rollback

Existing installed configurations are untouched. For a verified legacy JSONL host,
review the generated snippet and explicitly opt in to daily-autolog; validate a
new message, its original pointer and repeat capture first. Existing compiler
source-file workflows, pending review and approved-memory projection remain.
For SQLite hosts keep automatic capture disabled until a supported adapter passes.

`git revert <this-stage-commit>` restores the old default and diagnostic behavior;
it also restores the misleading automation risk. No memory format/data migration
occurs. To roll back only an isolated opt-in, set that temporary hook entry false.
No production rollback is needed: no production service/config/data was accessed.
