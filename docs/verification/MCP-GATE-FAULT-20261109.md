# Task 7: MCP gate fault handling

Baseline `e4f5cd9eea8cc2baf128845836af26efe4181092`, independent of PR #100.
Only MCP's enabled relevance gate exception path changes. No live host,
production configuration, database or service was accessed.

## Confirmed behavior

At `adapters/mcp/stellamem_mcp_server.py`'s `_gate_check` try/except,
injecting `PermissionError("private sentinel")` in an isolated SQLite
fixture returned `status=ok`, one authorized memory, 29 context characters;
the exception text appeared in stderr. The broad catch neither distinguished
an authorization-related failure from a relevance failure nor informed the
caller that the gate had failed. This is **not proof of cross-subject access**:
the separate host binding check still rejected an unauthorized persona.

The same path accepts truthy non-boolean `allow` values, or falls through
after malformed results raise. New regression tests run against original
main: 14 tests, 10 failing assertions/subtests. Against the candidate:
14/14 pass. Reproduction: run `python -m unittest discover -s tests -p
test_injection_gate.py -v` using the candidate test file and baseline server,
then candidate server, in separate Python processes.

## Decision and tradeoff

An enabled gate's uncategorized exception cannot reliably be declared a
harmless relevance failure. Return `fail_closed`, no items, zero chars and
budget, with `gate.reason=gate_error` and an exception **type** only. Validate
`allow` as a real boolean and require a string reason. The server process
continues; a subsequent healthy request succeeds. There is no retry or cloud
call. Explicit gate disable remains supported and does not bypass subject
authorization, source freshness or record filtering.

| Fixed fixture | Baseline | Candidate |
| --- | --- | --- |
| RuntimeError, PermissionError, ValueError, DatabaseError | recall called in all 4 | recall called in 0/4; typed fault response |
| Five malformed verdicts | recall called in all 5 | recall called in 0/5 |
| Sensitive exception message | echoed to stderr | absent from response/stderr |
| Authorized coffee query, healthy gate | 1 item, 29 chars | 1 item, 29 chars |
| Unrelated query, healthy gate | gated, empty | gated, empty |
| Unauthorized persona query, broken gate | fail_closed before gate | same; gate and recall never called |
| Healthy request following a fault | succeeds | succeeds |
| Model calls / added tokens | 0 / 0 | 0 / 0 |

Availability cost is explicit: an otherwise answerable query during an
injected gate fault now loses its memory context (29 → 0 chars in this
fixture). This is a bounded failure, not a recall-quality improvement.
Healthy positive/negative behavior is unchanged; no claim about production
fault frequency or latency is made.

## HTTP comparison and remaining scope

`compiler/api.py:do_POST` `/v1/recall` has token and subject authorization,
freshness checks, database locking and output subject filtering. It calls
`search.recall` directly and does not use this MCP lexical gate. Therefore
MCP gate fault injection has no corresponding HTTP gate invocation; this
PR does not introduce one or weaken HTTP authorization. Existing HTTP
isolation/read-safety tests remain in the full suite. The lexical probe's
internal OperationalError fallback is unchanged and outside this fix.

## Validation and rollback

Local Python 3.12.14/Linux: 929 tests pass (925 baseline + 4 tests),
45 Node tests pass; Ruff 0.11.13 lint/187-file formatting pass, offline demo,
history secret scan, frozen hash/byte checks and negative samples 9/9 pass.
Subject isolation, deletion/rollback and HTTP safety remain included.
Hosted Windows/macOS/Linux CI must pass at the final commit before merge.
MCP fault behavior is verified with the real server class and SQLite fixture;
no real DSH/OpenClaw session or private memories were used.

Rollback: revert the PR commit, which restores the known fail-open and
exception-message logging behavior. No migration required. No deployment.
