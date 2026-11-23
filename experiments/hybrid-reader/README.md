# Stellamem Hybrid Reader — Frozen Candidate 1.0.0-v62

V6.3 Stage 1 deliverable: the V6 deterministic-hybrid prototype after the
V6.2 safety hardening, frozen as a reproducible standalone candidate.
Pipeline: **prefilter_v2 → C1 LoRA reader (prompt v2) → deterministic
assembly (pattern-first routing + constrained operand completion +
binding refusals + citation guards)**. Research artifact only — NOT
wired into production Stellamem, DSH, OpenClaw or Jarvis.

## Contents

- `code/` — the full pipeline (records, det_module, prefilter_v2,
  assemble_v2), the frozen scorer (`scorer.py`, copy of
  `reader-train/baseline/scorer.py`), input builders, the Kaggle kernel
  builder/runner, the 189-chain scorer and the gate evaluator.
- `prompt_v2.txt` — the reader system prompt (sha256 in `config.json`).
- `config.json` — model/adapter/decoding/assembly configuration and the
  frozen eval2 results.
- `tests/` — 57 unit tests (22 original v1 + 14 prefilter_v2 +
  21 assemble_v2).
- `results/` — the frozen eval2 A/B result, per-question flips and the
  prefilter gold-survival audit.
- `MANIFEST.sha256` — sha256 of every file in this package plus the key
  external evidence artifacts.

## Replay (eval2, end to end)

All commands run inside `code/`; the builders resolve the datasets by
absolute path (`~/workspace/reader-train/...`) and write their outputs
next to themselves.

```bash
cd code
python3 -m unittest discover -s tests -v        # 57 tests
python3 build_inputs_v2.py                          # -> v62_inputs.jsonl (gold survival must print 583/583)
python3 build_kernel_v62.py --inputs v62_inputs.jsonl --n 420 \
    --out-name v62_outputs.jsonl --kernel-out v62_kernel.py
python3 run_v62.py --kernel v62_kernel.py --slug muse-reader-v62 \
    --out-dir kaggle-output-v62 --note replay       # Kaggle T4, ~2,700 GPU-s
python3 assemble_v2.py --reader-outputs kaggle-output-v62/v62_outputs.jsonl \
    --reader-inputs v62_inputs.jsonl --tag replay --bind completeness
python3 eval_gates.py ab_result_replay.json
```

## Replay (189 full chain, observational regression)

```bash
python3 build_189_inputs.py                         # prefilter_v2 over B2 rows (0 removals expected on this data)
python3 build_kernel_v62.py --inputs b2_inputs_v62.jsonl --n 189 \
    --out-name b2_v62_outputs.jsonl --kernel-out v62_kernel_189.py
python3 run_v62.py --kernel v62_kernel_189.py --slug muse-reader-v62-189 \
    --out-dir kaggle-output-189 --note replay189    # ~1,200 GPU-s
python3 score_189_v62.py --reader-outputs kaggle-output-189/b2_v62_outputs.jsonl
```

## Rollback

Delete this package (and, in the repo, the `experiments/hybrid-reader/`
directory / revert the PR). No production file, memory record or config
is touched by anything in this package; all historical experiment files
remain in place.
