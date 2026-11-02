# ADR 0003: Governance before confidence
Status: accepted, 2026-09-22.
PUBLIC/PRIVATE/SENSITIVE/SECRET classification is independent of confidence,
importance, storage/write policy and injection policy. Automatic extraction
defaults PRIVATE, never declassifies SENSITIVE. SECRET means never persist new
content. Identity/sensitive/uncertain evidence stays in the local pending inbox.
An accepted relation changes active status, retaining the historical record;
CONTRADICTS creates pending claim + conflict event. Ambiguous/compatible changes
to one slot await human review rather than silent replacement. Same value with
new provenance creates reinforcement audit evidence, not another active value.
A single process lock, write-ahead journal, per-transaction before/after snapshots
and atomically replaced files allow recovery. Projection refuses unfinished
transactions. Rollback previews changes and checks hashes before restoration,
backs itself up, never writes protected identity, never recursively deletes.
Snapshots are local user data; never put them in issues or public Git.
Directory-entry durability on abrupt power loss still depends on the filesystem.
