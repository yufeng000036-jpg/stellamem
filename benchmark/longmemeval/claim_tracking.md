# Claim-level tracking (LongMemEval)

The per-question tracking table (`tracking_table.py`) says *where* a question
was lost (extract / retrieve / project-cite / answer-use). This answers the
next question: **which gold claim was lost, and why** — per claim, not per
question. It is the direct response to the 2026-11-08 external evaluation's
P0: 211 of 500 round-4 questions were lost at project/cite, and the table
alone cannot say whether the evidence was never extracted, refused at
review, filtered by the adapter, cut by a cap, or projected but never cited.

## How it works (no model calls for the analysis itself)

1. **Diagnostics in the adapter** (`adapters/openclaw/index.js`, `project()`).
   Opt-in via the env var `STELLAMEM_PROJECT_DIAGNOSTICS_DIR`. When set,
   every call writes one JSON sidecar named by `sha256(prompt)[:16]` with one
   record per candidate claim: `claim_id`, `session_id`, `message_id`, `kind`,
   `score`, `stage` (`projected` / `dropped`) and the first failing gate as
   `reason` (`ineligible:<check>`, `duplicate-revision`,
   `blocked-quarantine-or-conflict`, `no-relevance`, `ambiguous-revision`,
   `kind-cap`, `total-cap`, `char-cap`), plus the exact context string and
   `empty_reason` when nothing was projected. The returned projection string
   is byte-identical with diagnostics on or off
   (tests/adapter-diagnostics.test.mjs). When the variable is unset, nothing
   is written and nothing changes.
2. **Replay** (`replay_project.py`). Re-runs `worker.py --arm main` per
   question against the round's saved model-call cache with the diagnostics
   env var set. `worker.py` is frozen and untouched. The per-question cache
   is copied to the replay work dir first, so a cache miss can never poison
   the original run's cache; any non-zero `physical_calls` is reported.
   The replay also writes `manifest.json` (`prompt_sha -> question_id`,
   question, question_date) and checks fidelity: the replayed
   `retrieved_sessions` / `cited_sessions` must equal the round's
   `results.json`, otherwise the replay did not reproduce the run.
3. **Analysis** (`claim_tracking.py`). Joins `results.json` + the oracle +
   the sidecars + the model-call cache into one CSV row per gold claim that
   reached the adapter, plus one row per gold turn that produced no active
   claim (`not-extracted` / `review-refused`). Claim-level citations come
   from the answer call's cached output (the cache key is recomputed from
   the sidecar's exact context string); when the entry is missing or
   unparseable, `cited` is `unknown`, never guessed.

## Running it (round-4 backfill)

```bash
# 1. Replay the 500 main-arm projections with diagnostics on (cached: ~$0).
#    Start the proxy first, exactly like run_smoke.sh does.
python3 benchmark/longmemeval/replay_project.py \
  --repo /tmp/stellamem-lme-rerun3 \
  --dataset work/longmemeval_oracle.json \
  --questions work/full/shard0_questions.json work/full/shard1_questions.json \
              work/full/shard2_questions.json work/full/shard3_questions.json \
  --cache-root work/full --work work/claim-replay \
  --endpoint http://127.0.0.1:$PORT --results work/full/results.json

# 2. Build the claim-level table.
python3 benchmark/longmemeval/claim_tracking.py \
  --results work/full/results.json --oracle work/longmemeval_oracle.json \
  --diagnostics work/claim-replay/diagnostics \
  --manifest work/claim-replay/manifest.json \
  --cache-dirs work/full/shard0/cache work/full/shard1/cache \
               work/full/shard2/cache work/full/shard3/cache \
  --out benchmark/longmemeval/claim_tracking_20261108.csv
```

## CSV columns

`question_id, category, kind, claim_id, session_id, message_id, stage,
drop_reason, cited, judge_main, gold_evidence_role`

- `stage`: `projected` | `dropped` | `not-extracted` | `review-refused`.
  Turn-level rows (`not-extracted` / `review-refused`) have empty
  `claim_id` / `kind`: the turn produced no active claim at all.
- `drop_reason`: only for `dropped` claims (see the reason list above).
- `cited`: `true` / `false` for projected claims when the answer call's
  cached output was found; `unknown` when it was not; empty otherwise.
  Per-claim "used in the answer" is not observable — the judge only scores
  the whole answer — so the CSV stops at `cited` and reports the question's
  `judge_main` alongside it.
- Abstention questions have no gold evidence and are skipped.

## Known limits

- A gold turn can produce several claims; the turn-level rows only say the
  turn produced *no* active claim, not which of its claims were refused.
- Two id-format facts the tools must respect (both verified against round 4,
  and regression-tested): `results.json` hashes session and turn ids as
  `sha256(id)[:16]`, while the oracle, the sidecars and the worker's raw
  output keep them readable — ids are hashed before any comparison across
  that boundary. And `worker.py` strips the projection
  (`proc.stdout.decode().strip()`) before the answer call, so the citation
  lookup recomputes the cache key over the *stripped* context.
- `review-refused` is inferred (extracted + verified, but never active):
  the approval step refuses non-user sources and SENSITIVE records.
- The citation lookup depends on the answer call's cache entry surviving;
  entries are keyed by the exact system prompt + question + context, so any
  change to the frozen `worker.py` prompt breaks the lookup (guarded by
  `tests/test_claim_tracking.py::SystemDriftTests`).
- The replay reproduces the run only if the repo code on the eval path is
  unchanged since the round (extraction, ingest, approval, projection).
  The fidelity check in step 1 fails loudly if it is not.

## Round-4 results (2026-11-08, main 9034cde)

Full replay: 495 ran + 5 skipped (smoke), 0 errors, 0 new model calls
(100% cache hits), fidelity mismatch 0 — the replay reproduces the run
exactly. CSV: `benchmark/longmemeval/claim_tracking_20261108.csv`
(7,851 rows: 7,639 gold claims + 212 gold turns without a claim).

- 7,639 gold claims: 2,084 projected (27.3%), 5,555 dropped — `no-relevance`
  2,608, `kind-cap` 1,831, `ineligible:temporary-flag` 1,115,
  `ineligible:outdated-flag` 1.
- Projected gold claims actually cited by the answer: 286/2,084 (13.7%).
  When the reader cites the gold claim, its question is usually judged
  right (207/286 rows); when it does not cite it, usually wrong (543/1,798
  rows are in judged-right questions). The answer/cite stage is the
  largest remaining loss after projection.
- 212 gold turns produced no active claim. 109 of those are from the 31
  questions whose extraction crashed entirely (`ChunkFailure`, no sidecar);
  of the remaining 103, 66 were never extracted and 37 were extracted but
  refused at review. The 51 single-session-assistant gold turns are all in
  the never-extracted group and none are from crashed questions: the
  extractor sees assistant speech but yields no evidence-verified claim
  from it. The H1 fix (speaker-tagged history evidence) has to span
  extraction, not only review/projection.
- H1 scope note (2026-11-10, refusal-gate wave 2): speaker-tagged assistant
  history does NOT fix the 5 refusal flips — the assistant turns in those
  haystacks do not disambiguate them (they echo the same near-miss facts).
  H1 targets score via the 51 assistant-turn gold cases, not refusal, and
  carries governance surface (assistant speech entering projection), so it
  is a separate PR, not part of the refusal fix. The refusal-relevant
  extraction fix is the qualifier-in-value rule (Fix 3, pipeline SYSTEM),
  which flipped 031748ae with the baseline reader.

## H1 implemented (2026-11-12, local branch fix/h1-assistant-history-evidence)

Round-4-code autopsy of the 51 (cache replay, $0): in 18/51 the extractor
DID yield an evidence-verified claim on the gold assistant turn and
`enforce_attribution` stripped the verification (source != user); in 33/51
the extractor yielded nothing on the gold turn under the durable-user-facts
prompt frame. The fix spans all three layers the evidence points at:

- `compiler/reliability.py`: evidence_verified is a property of the
  verbatim quote match, not of the speaker (normalize forces it False;
  only a real match sets it True). Assistant claims keep source=assistant,
  provenance.speaker=assistant, auto_write=False, injection recall-only;
  `is_user_fact` still requires source=user.
- `compiler/security.py`: a verified non-user claim's policy reason is now
  "non-user speaker requires review" (write_policy stays "confirm");
  unverified sources keep "unverified user speaker".
- `compiler/pipeline.py` SYSTEM: substantive assistant-provided content
  (recommendations, recipes, plans, lists, explanations, its own game
  moves) is in extraction scope, attributed to the assistant; pleasantries
  and restatements of the user stay out.
- `adapters/openclaw/index.js`: eligible() accepts source "assistant" on
  the same terms as "user". An assistant record can only be active via
  manual approval (ingest auto-write and the auto-approve sweep both
  require source=user), and approval may keep source=assistant
  (`inbox._approve_record --source`); before H1 such records could never
  project. The two adapter regression tests that encoded the old blanket
  ban were rewritten to the approval-gate semantics, as their own comments
  instructed for the day H1 landed.

Validation (fixed sets, production review semantics — cmd_approve with the
claim's own source; the frozen worker.py reviewer only approves
source=user, see below): target n=12 sampled from the 51 (6 per layer):
gold-turn extraction yield 0/12 -> 12/12; judged correct 1/12 -> 2/12
(6222b6eb flipped, verified load-bearing: the gold assistant claim was
projected and cited). Positive controls n=4: 4/4 -> 4/4. Abstention
subset n=7 (all 5 round-4 failures + 2 correct): abstentions 5/7 -> 5/7
(failures 3/5 -> 3/5), with one explained flip each way (2133c1b5
answer->abstain; 6456829e abstain->answer on a user-sourced near-miss line
in a changed context). Spend $0.0930 / 362 calls.

What H1 does NOT deliver: the +9.6pt ceiling assumed the evidence, once
extracted, would be used. On the sample, 3 questions' gold claims are
SENSITIVE-classified (health content) and refused at review — the separate
SENSITIVE-overkill item; 7 questions project gold-session claims but the
reader still abstains — the loss moves to the same reader/projection
stage that already dominates round 4. And in the frozen worker.py
simulation an official re-run would still score these 0: its reviewer
approves only source=user pending claims, so H1's effect there is limited
to the tracking stage label (not-extracted -> review-refused). Crediting
H1 in official runs requires revisiting that simulation's review rule,
which is a protocol decision, not part of this fix.
