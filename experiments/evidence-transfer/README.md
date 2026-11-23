# Evidence transfer audit and public temporal experiment

[中文研究论文草稿与审计](PAPER.zh.md) · [先例核查](LITERATURE.zh.md) · [预注册](PROTOCOL.md) · [研究状态](STATE.zh.md) · [逐题语义复核](REVIEW.tsv)

Python 3.10+ and Git, standard library only. All research code is under this directory. The public runner reads published artifacts, downloads the pinned LoCoMo file and works in the **new** directory supplied. It runs the frozen package tests, checks 26 file hashes, recomputes archived evidence and all public predictions, and compares an independent SQL aggregation with the registered outcome counts. It does not need a model server, private files, GPU, or credentials. No eval3 data is requested. The downloaded upstream candidate includes a text attestation that lists external file hashes.

## Quick public reproduction on Windows
From this directory in an isolated copy of this research branch:
```powershell
py -3 reproduce.py .\public-replay-01
```
Linux/macOS equivalent: `python3 reproduce.py ./public-replay-01`. Pick a fresh output path for every run. Linux full reproduction was completed; Windows instructions use portable Python/subprocess/Git but were not executed on the user's workstation. For a single script, use the paths inside the created directory. All result comparisons ignore machine-specific timing; per-question calculations, cohorts and decisions must match.

## What is fixed
- At takeover main eaf21965ed5333b539f5ef5f968b785a029174bb; candidate PR 166 at 9e554da2e40084f7ad0a1de55a6a053469cc6352. Pre-existing CI: [PR](https://github.com/yufeng000036-jpg/stellamem/pull/166), [run 1](https://github.com/yufeng000036-jpg/stellamem/actions/runs/45118473129), [run 2](https://github.com/yufeng000036-jpg/stellamem/actions/runs/45118480343).
- GitHub protocol freeze 607dca99f4d0ce5b3e1c90a50b3cdc23d52fa118. Public reader sealed locally at 751684d before validation/test; original reader source SHA-256 cd013eb8057eea842ebad44b8de08c1c1312942b6b2aebdb437561d12d04babb; remote archive 864e7b0fa802836e571955a0fa6b25ac4493ed60. A later formatting pass does not change any published item decision.
- [LoCoMo source](https://github.com/snap-research/locomo/tree/3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376), snap-research, Maharana et al., ACL 2024, CC BY-NC 4.0. LoCoMo-derived question indexes, dates, evidence IDs and paraphrased audit notes here are attributed to the upstream work under that license. Raw public dialogue and all private user data are omitted from this branch.
- The experimental slice contains only fully dated, answerable English questions. Correct refusal, revoked/deleted use, privacy rate and severe harm rates for a general assistant are unmeasured. Frozen group splits, eligible/excluded counts, exact hits, wrong answers, error/abstention counts, rank, IDs, evidence coverage, costs and paired statistics are in results/. Individual results contain source IDs, with full text obtainable at the public source. A/D model token and GPU consumption is exactly zero for this deterministic run; unmeasured LLM metrics are missing, not zero.
- public_dates.py implements the registered reader and metric; audit.py verifies old result files, tests the frozen classifier, and preserves expected and observed values. archive500.py performs a separately labeled retrospective reanalysis of earlier results. independent_check.py verifies the published split, per-row gold date, citations and aggregated outcomes with a separate normalizer and SQL. reproduce.py also computes the exploratory k=1,3,5 table. A second program is arithmetic verification, not an independent human review or a new dataset replication.
- Ratios in the evidence tables use answered plus abstained denominators as declared. The 189 B2 scorer checks lexical carriers and a supersession condition; the eval2 scorer checks normalized answers and separately counts exact-set citations. Neither implements the full human support definition in PROTOCOL.md.
- Frozen 189 result file's `det_latency_ms` list was written directly from a seconds timer (56 routed calls). After multiplying by 1000 its historical mean is 0.01154 ms/routed operation. This is not the reader time.

## Local 189 crossed replay (cached, no model or paid service)
This optional path is only for a machine holding the research cache. Do not point it at production or eval3. `--diag-root` is the directory containing `replay-stage2` and `replay-topup2` manifests and their diagnostics subdirectories. Four primary input files are SHA-256 verified against the frozen candidate; labels and diagnostic inputs are fingerprinted in the output. Six conditions use the same post-filter candidate inputs for assembly. The baseline naked reader retains its original cache. A nonzero prefilter or a formatting change in the reader's original payload would confound a prompt-only claim even when IDs match.

```powershell
py -3 replay189.py --package ".\public-replay-01\source\experiments\hybrid-reader" --rows "X:\research\b2_rows.jsonl" --inputs "X:\research\b2_inputs_v62.jsonl" --old "X:\research\c_outputs.jsonl" --new "X:\research\b2_v62_outputs.jsonl" --labels "X:\research\labels_189.jsonl" --diag-root "X:\research\work" --out ".\local-189-run01.json"
```

Replace X:\research paths with actual read-only research artifacts. The output stays on that machine. Each record contains the question ID, material-availability stratum, six outcomes and binding diagnostics, not raw chats. Report totals only after archived 189/63 transitions and the IDs/direction of all GOOD flips match. Cached old/v2 readers differ in prompt and potentially serialization; this diagnostic does not rerun the model. New missing-artifact errors, partial outputs, or mismatched hashes should not be counted as zero performance. To assess natural correctness/harm, double-blind review original questions, allowed evidence and answers offline. This semantic audit has not been completed here.

Post-hoc representation audit: representation.py compares the canonical byte size and explicit source-turn pointer coverage of the published LoCoMo raw conversations, observation notes, session summaries, and raw plus observations. No gold labels are added to the representations. See results/representations.json and the paper, section 6.1.
