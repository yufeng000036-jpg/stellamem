# Stage E / Stage F status after PR #15 merged

PR #15 is merged to `main` at `803df0d` (2026-11-03). **Code on main is not a production deployment.** No production service, configuration, or data change is evidenced by this repository. Stage E acceptance stays **NOT MET**. The Stage F release gate stays **CLOSED**. This tree is not a release candidate.

An external review of current `main` (`803df0d`) gave a subjective **7.8/10**. That number is one reviewer's judgment, not a benchmark score, and it does not pass Stage E or open Stage F. The 6.8/10 rubric later in this file is the older pre-merge engineering estimate. It is also a judgment, not a measurement, and it is not the current rating.

## Evidence still required before a production release

Stage E cannot pass, and Stage F cannot open, until the items below exist. The bounded run recorded in this file does not supply them.

### Stage E — acceptance NOT MET

Still required:

1. Independent human-reviewed gold labels. The labels in the bounded run are AI-authored.
2. A holdout frozen before development. The nominal holdout below was frozen after known fixes and is not an independent blind set.
3. Actual extraction precision and recall.
4. Generated-answer evidence support.
5. End-to-end provenance completeness metrics.
6. Peak-memory and cost measurements for the complete memory pipeline.
7. A quantified memory-quality comparison against the older diary benchmark. That uplift cannot be quantified from the evidence in this file.

Do not use the reader-only 1.00 scores below to claim 8–9 maturity. Those scores measure the adapter projection contract on synthetic fixtures. They do not measure language understanding, extraction, or generated answers.

### Stage F — release gate CLOSED

Still required, in addition to Stage E:

1. Stage E acceptance. F cannot pass while E is unmet.
2. A production deployment record: the running services, configuration, and data actually changed, plus a rollback receipt. Merging #15 did not produce that record. This repository still cannot show that production was touched.
3. The original D7–D13 audit text. It is still absent, so those items cannot be signed off. Invalid-namespace fail-closed behavior exists in `compiler/subjects.py`; that does not close the unknown remainder of D13.
4. An explicit release-candidate decision made after the three items above. None has been made.

Rollback of the git merge is a source-control decision. It is not a production rollback, because this evidence set never deployed to production.

---

# Stage E — bounded measurement; acceptance NOT passed

300 frozen synthetic projection-contract cases were actually executed against baseline `8430657` and the updated adapter, using identical corpus/environment. 200 functional cases (10 families x20 variants), 100 adversarial cases (10 families x10); train/dev/holdout partition, 90/300 holdout cases. Corpus SHA-256: `d20453b038a4cbd86e4a39aa652337c323838287f0d99b9b122e3e7b3d3555ac`.

**Validity limits:** explicit gold labels are AI-authored, not independently human-reviewed. Cases were frozen after known fixes, not before development; the nominal holdout is NOT an independent blind set. Templates repeat. Runtime values are generated synthetic fixtures, not real diary/chat material. This measures the actual adapter's projection contract, not language understanding, extraction or generated answers. No tuning after viewing holdout results was performed.

| Metric | Baseline | Updated | Denominator |
|---|---:|---:|---|
| Exact projection-contract passes | 140 | 300 | 300 cases |
| Unexpected projections | 180 | 0 | projected IDs outside explicit expected IDs; events, not unique people |
| Adversarial exact passes | 60 | 100 | 100 cases |
| Adversarial unexpected projections | 40 | 0 | 100 cases |
| Reader Recall@1 | 0.50 | 1.00 | 80 positive cases |
| Reader Recall@5 | 1.00 | 1.00 | 80 positive cases |
| Reader MRR | 0.75 | 1.00 | 80 positive cases |
| Nominal holdout exact passes | 42 | 90 | 90 cases; NOT independent blind validation |

All raw per-case results, expected/actual IDs, split labels and observed latency are in `benchmark/reliability/contract-{baseline,current}.json`. Run `node benchmark/reliability/evaluate.mjs <explicit adapter path>`; the runner checks the corpus hash, uses temporary stores and calls real project(). `build_corpus.py` refuses overwriting the frozen set. No current secret values are stored in corpus; a fake credential is generated at runtime for its negative case.

**E acceptance: STOP / NOT MET.** The missing evidence is the list at the top of this file. No paid APIs or real private data were used in this bounded run.

# Stage F — release gate remains CLOSED

#15 is on `main`. That is a git merge, not a release and not a production deployment. D7–D13 original audit is still unknown. F cannot pass while E acceptance is unmet. This file contains no evidence that production services, configuration, or data changed.

Provisional engineering estimate recorded before the merge, subject to final same-SHA CI at that time: governance 1.4/2, privacy/security 1.5/2, cross-platform 1.8/2, simulated E2E/recovery 1.1/1.5, memory-quality evidence 0.3/1.5, maintainability/docs 0.7/1 = **6.8/10**. This was a judgment rubric, not a measured benchmark score. It is not the current rating. The current external subjective rating of `803df0d` is **7.8/10**, also not a benchmark, and it does not change the NOT MET / CLOSED conclusions above.

To proceed past E, the missing quality evidence listed above has to be produced. Extra features are not a substitute. Production access is not a substitute for writing the evidence down.
