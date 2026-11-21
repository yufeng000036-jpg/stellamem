# Route B failure attribution, historical round 4

Input system commit 9034cde. The corrected verdict is inherited from score_integrity.py. The attached CSV has one row and one primary outcome for each of the 500 public cases. Its counts cannot measure a later commit. Reproduce with the pinned oracle corpus and a new output path:

    python benchmark/longmemeval/failure_attribution.py longmemeval_oracle.json attribution.csv

| Observable primary outcome | Cases | Interpretation and next experiment |
|---|---:|---|
| All annotated turns represented, answer incorrect | 87 | First reader experiment. Compare raw quote versus extracted value and a speaker/date-aware reader on identical inputs. A claim from the right turn can omit the needed fact. The reader's causal share is unknown. |
| Only some annotated turns represented | 110 | First multi-session experiment. Compare missing-turn recovery at a fixed token budget. 56 of these questions are multi-session. |
| No tracked verified extraction on any needed turn | 48 | 47 are assistant-history questions. Inspect extraction before increasing projection width. |
| Execution or verdict unavailable | 32 | Budget and execution failures remain in the denominator. Rerun separately before inferring retrieval or reader errors. |
| Mixed losses, no annotated turn represented | 26 | Inspect per-turn traces and overlapping tags before assigning a single engineering change. |
| Review refused | 10 | Trace the reviewed record and its original speaker. An intentional review policy may explain the loss. |
| Relevance filter | 7 | Compare at identical character limits. |
| Answered an unanswerable question | 5 | Inspect the raw question, answer and proposed source to determine whether a near miss caused it. |
| Per-kind cap | 1 | One question exclusively attributable to this gate at this level. Per-claim drop counts use a different denominator. |
| Correct after audit | 174 | Historical corrected strict verdict from the retained audit. |

The 87 and 110 are experiment populations, not predicted recoveries. Fixing 25 previously wrong cases with zero regressions would add five percentage points on 500. No causal estimate or semantic-entailment rate is available from the CSV. Turning off review or widening injection would increase risk and needs a paired trial. Comparing two reader prompts at the same evidence budget is cheaper and reversible.

The public benchmark defines unanswerability. Raw source absence, source text conflicts, plan versus completion, semantic correctness despite a bad citation, and semantic judge mistakes cannot be distinguished reliably using anonymous replay ids alone. They need source and response review. The audit identifies three historical positive verdicts it overrules. A right-turn citation measures an ID match, not whether it supports the answer. Citation and abstention tags may overlap the primary cause and must never be added to the total failed count.

## Source-grounded spot check of wrong answers

Within the all-gold-turns-represented bucket, sort by SHA-256 of `qualitative-v1:` plus case id, then take the first three per category for assistant history, multi-session, knowledge update and temporal reasoning. This is an AI-assisted source review, not a new semantic judge or an accuracy estimate. The inspected inputs are the public question and reference, the tracked judge hypothesis, and the annotated source turns. The prompt actually seen by the historical reader is unavailable.

| Category | Case ids | Observable outcome |
|---|---|---|
| Assistant history | 4388e9dd, 5809eb10, f523d9fe | Three explicit abstentions. A represented turn does not show whether the particular clothing description, year or title survived claim compression. |
| Multi-session | 60bf93ed, edced276, a96c20ee | Three explicit abstentions. For 60bf93ed the source contains purchase and arrival dates in separate turns. The reference allows either elapsed or inclusive day counting. |
| Knowledge update | e493bb7c, 6a1eabeb, a2f3aa27 | The first is a candidate historical judge mistake: the latest source locates the painting above the bed in the bedroom, the tracked Chinese answer names the bedhead, and the reference names the room. The second returned the older 5K record; both records and their order are explicit in the source. The third abstained. |
| Temporal reasoning | b9cfe692, gpt4_fe651585, e4e14d04 | Two abstentions. For e4e14d04 the hypothesis gives three weeks since joining; the question asks membership duration at an earlier meetup and the reference gives two weeks. |

Nine of these twelve were abstentions, two answered using a wrong time or state, and one needs bilingual adjudication. The CSV and 174/500 historical verdict remain unchanged. A future matched reader review should show the exact scored projection to decide whether to fix evidence selection, date reasoning or the grading model.
