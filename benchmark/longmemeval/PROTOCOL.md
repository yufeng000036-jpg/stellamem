# Preregistered bounded local evaluation v2

Frozen before benchmark inference/results. Source: official LongMemEval cleaned,
MIT, revision and file SHA in manifest.json. 140 official cases: 20 each of seven
categories, including abstention. We use oracle evidence sessions to bound CPU
cost. This is NOT a full-distractor LongMemEval_S/M score. No private user data.
No raw conversations or model answers will be uploaded. Case IDs, result hashes,
counts and source hashes allow local reproduction.

Reader/extractor: official Qwen/Qwen3-0.6B-GGUF, Apache-2.0, revision
23749fefcc72300e3a2ad315e1317431b06b590a, Q8_0 SHA256
9465e63a22add5354d9bb4b99e90117043c7124007664907259bd16d043bb031.
CPU-only llama.cpp b12032 (dde879bc4), official Ubuntu x64 archive SHA256
9f10432ca3f66ef003a7a540aaffb9da0acadaefd05404521b9ab7a2436af304.
No thinking, temperature zero, 16384 tokens/slot, 1 slot, 8 CPU threads.
No paid API, external model service, inherited proxy or credential.

Compare main 910f27d and this PR, plus no-memory and direct-context baselines.
All memory inputs use all sessions and all turns, chronologically ordered;
no answer labels/has_answer/category enter the extractor, reviewer or reader.
Both memory arms use the real compile_messages, MemoryStore, pending inbox,
approval API and OpenClaw projector from their own checkout. The review actor
is scripted: accepts only verified user-source non-sensitive pending records;
explicitly classifies topic namespace as user when supported. It is NOT human
review. All model-generated scopes in that isolated case are explicitly enabled
by this review actor in both arms; no scope is taken from question prose.

Extraction: product prompt unchanged, chunks 2500 characters, max 1024 output
tokens per call, max 32 logical calls/case; first failure makes that case's memory
arm fail without partial writes. Retries count toward the budget. Identical
model requests may reuse hash-keyed local cache; cached generation time/token
counts are charged equally in logical totals. Physical calls/cache hits and
actual wall time are reported separately. This makes correctness comparison
repeatable but warmed wall times are not speed comparisons.

Answer reader: same local model, 512 output tokens, no tools, structured answer,
citations and abstain. Memory and direct-context arms share a 16000-character
context cap. Direct context packs complete chronological turns until that cap;
omitted turns counted. No-memory gets zero context. Do not silently call direct
context a full-history baseline. Incorrect/malformed/budget-exhausted output
counts as failure, not correct abstention. No semantic judge or paid GPT judge.

Metrics fixed before results:
- Strict normalized exact answer match, a deliberately limited lower bound,
  NOT official LongMemEval semantic accuracy. Report by official category.
- Abstention classification separately on official abstention labels.
- Recall@1/5 = fraction of official answer sessions in first k unique retrieved
  sessions; abstention excluded. Same definition for all arms where applicable.
- Evidence-turn coverage from official has_answer labels; not fact-extraction
  precision/recall. The benchmark has no exhaustive claim gold labels, so full
  extraction precision/recall remain unmeasured.
- Citation-reference validity and citation coverage of gold sessions; neither
  proves semantic entailment. Answer evidence support requires independent review.
- Per-case parse/budget/policy errors, extracted/pending/active counts, latency,
  logical/physical model calls and process peak RSS. No claim of measured
  cross-subject leak rate: official cases do not label subject namespaces.

Acceptance: full memory-quality acceptance remains CLOSED unless independently
reviewed semantic answer support >=95%, factual precision >=95%, recall >=80%,
correct abstention >=90%, zero unsafe writes/cross-subject leaks, and candidate
improves main by >=5 percentage points without >2 point category regressions.
The unmeasured criteria cannot pass. This CPU run can expose failure modes; a
high exact-match score alone cannot satisfy the release gate. No thresholds or
gold edits after seeing results. Corrections to evaluator bugs require a new
version, retaining the old report. No model change based on test answers.

## v2 infrastructure correction, before any valid benchmark answers

v1 at 7fd5855 produced zero successful responses. Four slots/65536 total context
were not viable under the observed 8 GiB cgroup limit; a repeated parallel
probe exited -9 and cgroup OOM-kill events were observed. Single-slot 4096
synthetic smoke generated text. v2 keeps the same 16384-token per-request
window but allocates only one slot (16384 total); four case workers queue on it.
Model, samples, prompts, output budgets, labels and thresholds are unchanged.
A real inference preflight now precedes cases; server death invalidates the run.
Memory accounting uses /proc VmHWM for actual worker/server peaks; v1 ru_maxrss
inherited the parent's high-water and cannot be used as worker memory evidence.
v1 case-level failures remain in docs/verification/longmemeval-v1-infrastructure.json.
The preflight call is reported separately from benchmark model call totals.
