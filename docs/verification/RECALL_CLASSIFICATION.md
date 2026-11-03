# Explicit review classification — 2026-11-02

Instruction: PR #15 issuecomment-6570781363. Starting main 910f27d,
PR/local 17bc28d, clean worktree, no stash, ahead/behind 0/0. Both latest
17bc28d runs (41399345145, 41399348782) completed successfully; seven jobs.
No completed unpublished work existed. Production was not accessed.

## Reproduced defect and repair

The extractor's subject may be a topic/person, but the projection boundary
requires a namespace. Approval could edit the topic but could not explicitly
classify its namespace. Reproduction: 5 new checks, 2 CLI errors (unsupported
--namespace), 1 failure (identical topic IDs collide across namespaces).

`inbox edit/approve <revision-id> --namespace user|persona|viewer` now records an
explicit review decision without rewriting subject, evidence or provenance.
Model-supplied namespace is still discarded. An untagged topic remains
noninjectable. Same-subject task scopes still require explicit activeScopes.
No projector eligibility checks were weakened.

Explicit topic namespaces are part of the claim/revision ID. Existing legacy
IDs stay stable; adding the matching namespace to a legacy user/persona/viewer
subject preserves its ID and cannot bypass an occupied conflict slot. No bulk
migration is performed. Editing is pending-only; existing active unclassified
records require a separately reviewed migration, not an automatic relabel.

## Verification

- `python -m unittest discover -s tests -v`: 416 passed (410 existing + 6 new).
- `node --test tests/hooks.test.mjs`: 8 passed.
- `python demo/run_demo.py --offline`: passed.
- Ruff 0.11.13 lint and format: passed, 116 files formatted.
- Own scanner with history: passed.
- Gitleaks 8.24.3: scan the exact staged source export before upload. An initial
  whole-directory scan also read generated ignored Python bytecode and found
  the existing fake JWT/key fixture there. Those are not source changes; no
  exemption was broadened. The staged-source scan is the upload gate.

The six new tests cover approved topic recall, explicit scope, user/persona/viewer
isolation, model namespace refusal, pending edit audit, namespace-specific IDs,
legacy conflict-slot preservation, and real transaction rollback. These are
contract regressions, not independent long-term memory quality measurements.

## Limits and rollback

No quality score change claimed. LongMemEval and real OpenClaw acceptance remain
open. Source fields can still be inconsistent until deliberately reviewed.
No optional modules deleted: this focused repair does not establish that their
public interfaces are unused downstream. D7–D13 source audit remains unavailable.

On this branch, `git revert <this-stage-commit>` restores the prior CLI and ID
behavior. Do not continue creating explicitly classified records with an old
writer: older writers ignore namespace in IDs. In the isolated fixture use the
reported approval transaction ID with `Transaction(root).rollback(id, yes=True)`
under writer_lock; the test verifies the original pending record is restored.
No production rollback is needed because production is untouched.
