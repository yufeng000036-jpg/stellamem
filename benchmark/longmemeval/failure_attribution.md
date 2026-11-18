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
