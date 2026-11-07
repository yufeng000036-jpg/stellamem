# Memory read safety review — 2026-11-07

Base: main `fa9c952c` (PR #80). Isolated checkout; no production configuration or personal memory was accessed.

## Changes

- MCP refuses recall if index freshness cannot be established, including missing root metadata. Refresh failures no longer fall through to stale recall.
- HTTP content reads and health wait for in-flight index reconstruction and recheck freshness. An unavailable/stale index returns HTTP 503 without memory content. Background rebuilding remains available internally; external readers no longer use stale-while-revalidate. This can increase read latency during reconstruction.
- `/v1/search` explicitly searches active records only. Existing management/history interfaces retain their review semantics.
- Index reconstruction rolls back on malformed canonical files instead of silently skipping them. Records and freshness metadata are committed together. Source changes observed during a build abort it instead of marking mixed input fresh.
- Time-based inbox approval additionally requires value to equal verified evidence after trimming outer whitespace. Paraphrases, summaries and contradictory values remain pending for manual review. Existing subject/privacy/conflict checks still apply. Exact quotation does not establish correct subject, predicate, temporal attribution or surrounding context; this is a conservative restriction, not a semantic verifier. The existing manual approval path remains available.
- CI runs `npm test`, covering both JavaScript test files. No automatic approval scheduler was installed.

## Validation

- Python unittest discovery: 806 tests passed (798 baseline plus 8 new regression cases).
- Node: 18 tests passed across both files.
- Ruff lint and formatting checks passed.
- Offline demo and current-tree secret scan passed.
- Fault cases: revoked sources + failed rebuild on HTTP and MCP; in-flight build blocks reads; missing MCP root metadata; malformed file causes rollback and unhealthy HTTP response; source mutation during reconstruction; pending exclusion; contradictory value stays pending despite verified evidence.

## Limits and remaining acceptance work

This addresses the reproduced stale-index failure paths, not a proof of linearizable revocation across arbitrary external writers. External Markdown edits are not serialized with every read; a write after the freshness check can race an in-flight response. Strict cross-process revocation needs shared locking/generation checks across writers and readers, with separate concurrency acceptance tests.

No paid model calls, new LongMemEval 500-question run, live Windows/DSH deployment, or production MCP quality evaluation were performed. Recall@5, answer accuracy and refusal accuracy therefore retain their previously measured values; this patch supplies no evidence for a higher quality score. HTTP/MCP injection-gate parity and offline semantic-service quality remain separate work.
