# Stage E — bounded measurement; acceptance NOT passed

> **Note (2026-11-04, v0.3.0):** This document records the historical Stage E evaluation status as of 2026-11-02. It does NOT reflect the v0.3.0 formal deployment (DSH three-stage acceptance, 2026-11-04). The "NOT passed" refers specifically to the Stage E synthetic benchmark criteria listed below, not to the project's overall deployment readiness. See README.md "2026-11-04 正式部署" for current deployment status.
>
> **注意（2026-11-04，v0.3.0）：** 本文档记录的是 2026-11-02 时的 Stage E 历史评估状态，不反映 v0.3.0 正式部署情况（DSH 三阶段验收，2026-11-04）。"未通过"特指下方列出的 Stage E 合成基准标准，不代表项目整体部署状态。当前部署状态见 README.md "2026-11-04 正式部署"章节。

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

**E acceptance: STOP / NOT MET.** Missing: independent human-reviewed gold, a pre-development blind holdout, actual extraction precision/recall, generated-answer evidence support, end-to-end provenance completeness metrics, peak-memory/cost measurements for the complete memory pipeline. No memory-quality uplift against the older diary benchmark can be quantified. Do not use the reader-only 1.00 scores to claim 8–9 maturity. No paid APIs or real private data were used.

# Stage F — release gate remains CLOSED

No merge, release or production deployment. This is an isolated development PR with rollback and evidence; it is not a release candidate. D7–D13 original audit is still unknown. F cannot pass while E acceptance is unmet. All production services/configuration/data unchanged.

Provisional engineering estimate, subject to final same-SHA CI: governance 1.4/2, privacy/security 1.5/2, cross-platform 1.8/2, simulated E2E/recovery 1.1/1.5, memory-quality evidence 0.3/1.5, maintainability/docs 0.7/1 = **6.8/10**. This is a judgment rubric, not a measured benchmark score; strongest deductions are missing independent quality evidence and real host/long-term validation. An 8-point assertion would be unjustified.

Rollback: keep this PR unmerged, or revert its C–E commit on the isolated branch; this restores earlier vulnerabilities and is not an endorsed production downgrade. No production rollback needed. Original A/B evidence remains. To proceed past E, independent quality validation must be completed; no additional features or production access is a substitute.
