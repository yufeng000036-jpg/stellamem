# Reclassified pending update regression

> Status note (2026-11-04): this fix is on main via PR #15 (`803df0d`). The
> 423 Python tests below are this note's own run. Independent acceptance stays
> unmet and the release gate stays CLOSED. Merge is not production deployment.



During the real host/quality work, a second concrete classification edge was
reproduced: an approved named topic has a namespaced ID; a new unclassified
pending update initially has another ID. After a human used `inbox edit
--namespace user`, the pending ID became the occupied ID but its relation stayed
NEW. Consequently the projector did not suppress the unresolved old value.

Before repair, the new regression failed (`NEW != UNCERTAIN`). `_apply_edits`
now checks the edited slot against actual active memory, records the previous
relation and marks an occupied slot UNCERTAIN. This chooses no winner and never
overwrites history. Generic approval still refuses the occupied slot. The
existing projection conflict gate then suppresses the unresolved value.

Evidence: 423 Python / 8 Node tests, offline demo, Ruff lint/121-file format and
own history scan passed. The focused seven-test review suite includes the new
real store/CLI/Node-projector regression; previous isolation/scope/legacy-ID and
rollback checks remain. Exact staged source export is Gitleaks scanned before
upload. LongMemEval remains frozen on its earlier checkout; this additional
product change is not silently represented as measured by that ongoing run.

Files: compiler/inbox.py, tests/test_review_namespace.py, this report.
Rollback: `git revert <this-stage-commit>` restores the old reclassification
behavior, including this confirmed stale projection risk. No production data
was touched. No optional interface was removed. Independent acceptance and
release remain closed.
