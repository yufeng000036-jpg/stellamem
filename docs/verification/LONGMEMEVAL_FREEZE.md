# Independent-data evaluation freeze — 2026-11-02

> Status note (2026-11-04): PR #15, which carried this freeze, is merged at
> `803df0d`. Merge is not a quality pass and not a production deployment.
> Stage E remains NOT MET. The Stage F release gate remains CLOSED. The
> sentence below that says "Keep PR draft" described the freeze commit; it is
> not the current git state. There is still no public benchmark score.



140 official LongMemEval cases selected before inference: 20/category across
seven categories. MIT data revision/hash, model/runtime hashes, exact sampling,
budgets, script lock and acceptance thresholds are in benchmark/longmemeval/.
No gold answer is included in worker inputs; official answer-session/turn labels
are used only after inference. The frozen script is a real product pipeline
runner, not the old generated projection-contract corpus.

This is a bounded CPU oracle-session experiment; it cannot be reported as a
full LongMemEval_S/M or independently human-reviewed quality pass. Scripted
review is explicit and labelled. Missing claim-level labels and semantic answer
judgments remain unmet criteria even if strict exact-match scores look good.
All failures stay in denominators. Gold answers and raw prompts stay outside Git.

Pre-run checks: 420 Python tests, 8 Node tests, offline demo, Ruff 0.11.13 lint
and 120-file format check, own history secret scan passed. Four evaluator tests
cover numeric gold, partial multi-session recall, failed-abstention denial,
non-substring exact matching, result redaction and fixed official coverage.
The exact staged source export is additionally Gitleaks-scanned before push.

This commit freezes code/thresholds before benchmark results exist. No quality
gain or acceptance is claimed. Run on Linux with the pinned downloaded runtime
and model, using --baseline <910f27d checkout> --candidate <PR checkout>:

```sh
python benchmark/longmemeval/evaluate.py --dataset /scratch/oracle.json \
  --baseline /scratch/main-checkout --candidate /scratch/pr-checkout \
  --runtime /scratch/llama-b12032/llama-server --model /scratch/model.gguf \
  --work /scratch/lme-run --out /scratch/lme-results.json
```

The runner creates isolated memory roots and a loopback-only child model server,
then shuts it down. It never uses production config, user diaries or paid APIs.
The repository and four locked files must stay fixed throughout measurement.
Regression CI does not download the model or run this CPU benchmark.

Rollback: `git revert <freeze-commit>` removes only evaluation assets/tests/docs;
it does not alter product data. Discard its scratch work directory to remove
temporary public benchmark text/caches. Keep PR draft and release gate closed.
