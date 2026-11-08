# Task 1: historical score integrity, v1

Baseline: `e4f5cd9eea8cc2baf128845836af26efe4181092` (PR #99).
Baseline CI: https://github.com/yufeng000036-jpg/stellamem/actions/runs/42475792853
(completed/success). Local Linux: Python 3.12.14, Node 24.22.1,
Ruff 0.11.13; standard library only, isolated checkout and venv.
Baseline 925 Python / 45 Node; candidate 937 Python / 45 Node.

## Reproduction and cause

`benchmark/longmemeval/tracking_table.py:judge_label` accepts the model's
label without checking execution success. `loss_stage` checks that label
before the extraction error. Replaying the committed fourth-round row
`gpt4_a1b77f9c` with `judge_label=True`, `hypothesis=""`,
`error="ChunkFailure"`, `extracted=0` returns `("pass", "")`.
Both committed rounds contain that row and `gpt4_372c3eed_abs` with empty
judge hypotheses and positive labels despite extraction failures.
`6456829e_abs` is also labeled correct although it answered an unanswerable
question (`abstain=False`, `correct_abstention=False`).

## Minimal change and reproducible comparison

`score_integrity.py` independently audits every tracking row. It rejects
errors, empty ordinary answers, malformed fields, inconsistent response
states, and wrong refusals. Answer correctness and citation qualification
are separate; a valid refusal does not require citations. Valid citation
IDs do **not** establish semantic entailment: that metric stays null.
The tracking generator now emits additive `integrity_*` columns and the
new summary; legacy columns and historical CSVs are preserved.

Same 1,000 historical rows, no inference, no new tokens or cloud spend:

| Round | Historical judge | Corrected v1 | Correct refusal |
| --- | ---: | ---: | ---: |
| third | 175/500 (0.350) | 172/500 (0.344) | 24/30 (0.800) |
| fourth | 177/500 (0.354) | 174/500 (0.348) | 24/30 (0.800) |

Both rounds: exactly the same three false positives above are detected;
all other verdicts unchanged. Ordinary question totals: 148/470 and
150/470, respectively. Recall, evidence utilization, context, model
parameters and historical costs are unchanged. Per-category counts,
input hashes and auditor hash are in `score-integrity-20261109.json`.
This is improved measurement reliability, not improved memory intelligence.

Run (exit 1 means known false positives detected, not an infrastructure failure):

```sh
python benchmark/longmemeval/score_integrity.py benchmark/longmemeval/tracking_table_20261108.csv --out /tmp/score-audit.json
python -m unittest discover -s tests -p test_score_integrity.py -v
```

Output uses exclusive creation: no existing file, including input, can be
overwritten. Reports contain IDs/verdicts, not answer text. Exit 2 denotes
invalid input/output; 0 denotes no detected positive-score anomaly, not
release acceptance. Twelve tests include known historical failures,
whitespace, malformed schema, extraction/budget/JSON errors, no citations,
valid empty refusals, all-refusal negatives and CLI overwrite protection.

Local verification: 937 Python / 45 Node, Ruff lint/189-file format check,
offline demo, history secret scan, freeze hash/byte verification, negative
samples 9/9 all passed. Existing suites include subject isolation,
deletion/rollback and negative controls. Hosted cross-platform CI is
required separately; do not interpret the local run as Windows/macOS proof.

## Boundaries and next work

The repository contains derived tracking CSVs, not the external route-B
judge runner, complete original responses or round-4 caches. This audit
cannot re-parse unavailable raw responses or independently prove semantic
support. Frozen worker/evaluate/manifest/protocol/lock files were not
changed. Task 1's historical-mislabel subtask is verified locally; full
raw-response judge integration remains open. Release gates stay CLOSED.

Reviewed PR #15 instructions and #78–#99 descriptions and relevant code:
#78 evidence/date rescue and #85 prefix fix already landed; #83 intentionally
blocks assistant-source projection; #92 locates the missing assistant
evidence at extraction; #94 rejects blanket CoT and semantic-judge rollout;
#93/#95 are superseded by #97/#98. Open PRs at intake: #2–#10, #27, #30;
no non-PR open issues. No old PR closed or branch rewritten.

Tasks 2–6 need original evidence/cache for fixed-budget end-to-end candidate
comparisons. Claim CSV alone lacks candidate text, full context and model
responses, so it cannot substantiate a ranking/generation improvement.
Do not raise budgets or relax subject/review/security filters to fill this
gap. Task 7's broad MCP gate exception path remains present; inspect with
isolated fault injection next. Task 8 README still says 901/31 despite
baseline 925/45; fix separately, without relabeling tests as memory quality.

Rollback: revert this PR's commit; no migration, production file/service
or credential change. Historical source data remains byte-for-byte intact.
