# ADR 0002: Complete sources, pointers and resumable compilation
Status: accepted, 2026-09-22.
Host session JSONL remains the original conversation owner. The hook records
activity summaries and a pointer manifest, never a second raw conversation.
Pointers carry session filename, entry index, message ID, timestamp, speaker and
SHA-256 of original message text. Compiler resolves and validates each pointer.
Missing/changed sources stop the source; never substitute summaries as originals.
Legacy plain daily text has honest day/message precision; no fabricated speaker.
Messages are batched, split at sentence/newline boundaries where possible.
An oversized indivisible span is split with contiguous offsets and an explicit
forced_boundary flag. No fixed prefix/tail dropping. No raw deletion.
Whole secret-bearing messages are skipped before splitting and model transport.
The omission count is diagnostic only. Source content is unchanged.
Chunk failure records typed status without raw error text; source cursor remains
before failure. Changed same-day sources are re-read and exact evidence deduped.
