# Abstention three-arm control experiment

PR #79's entity-match guard had zero net effect on the 30 abstention
questions in round 4 (fixed 2, broke 2). Instead of stacking more keyword
rules, this experiment tests whether the reader obeys the abstention
constraint at all, under three controlled evidence conditions — same
question, same answer prompt (`SYSTEM` in `claim_tracking.py`), same model
(DeepSeek flash via the eval proxy):

| arm | evidence | n | abstained | answered |
| --- | --- | --- | --- | --- |
| none | projection empty (header only) | 30 | 30 | 0 |
| projected | the exact context round 4 gave the reader | 29 | 24 | 5 |
| wrong | another abstention question's projected context (rotation by one) | 29 | 29 | 0 |

`projected` is n=29, not 30: `gpt4_372c3eed_abs` has no sidecar — its
extraction failed (`ChunkFailure`) in round 4 and in the replay, so there
is no projected context to reuse. The 5 `projected` answers are exactly
round 4's abstention fail set minus that question
(`031748ae`, `0ddfec37`, `2133c1b5`, `6456829e`, `gpt4_93159ced`) — the arm
reproduces round 4 for free (identical cache key, pure cache hit).

## What it proves

- The reader **does** obey the abstention instruction when it has no
  evidence (30/30) and is **not** fooled by wrong evidence (29/29) — the
  wrong contexts are just as rich as the own ones (mean `projected_n`
  4.6 vs 4.8, same range 1–9), so this is discrimination, not thin
  evidence.
- The failures happen only on the question's **own** projected evidence:
  near-miss claims — same entities/topic, but the specific fact is absent
  or different — look answerable enough that the reader answers. The
  failure mode is a semantic relevance/sufficiency judgment, not
  instruction disobedience and not "any evidence makes it answer".
- Consequence for fixes: the lever is either a stricter
  evidence-sufficiency check in the reader for near-miss evidence, or
  better relevance scoring in the projection so near-miss claims are not
  injected. More keyword guards (the PR #79 approach) are the wrong tool.

## Cost

59 fresh model calls (30 none + 29 wrong; projected is all cache hits),
26,661 tokens total, run 2026-11-08 via the eval proxy. Results:
`work/abstain-experiment/abstain_results.json` in the round-4 run dir
(not committed); the harness is `benchmark/longmemeval/abstain_experiment.py`.

## Re-run

```sh
python benchmark/longmemeval/abstain_experiment.py \
  --results work/full/results.json --oracle work/longmemeval_oracle.json \
  --diagnostics work/claim-replay-full/diagnostics \
  --cache-dirs work/full/shard0/cache work/full/shard1/cache \
               work/full/shard2/cache work/full/shard3/cache \
  --work work/abstain-experiment --endpoint http://127.0.0.1:$PORT
```

`--arms` takes a comma-separated subset of `none,projected,wrong`. The
script is resumable (skips arms already in `abstain_results.json`) and
never writes into the round-4 cache dirs; fresh entries go to
`--work/cache`. A cached *error* entry is retried, never treated as a
result.
