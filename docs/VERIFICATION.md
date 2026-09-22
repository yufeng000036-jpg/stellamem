# Local verification log
## 2026-09-22: shared compiler and transactional governance
WHAT CHANGED: source_reader/pipeline shared by extract and sleep_purify, strict
model parser, independent policy, protected identities, version/conflict routing,
source content cursor, one writer lock, journal recovery and rollback primitives.
WHY: remove duplicate rules and permanent prefix/day-cursor data loss.
TEST RESULT: 29 unittest cases PASS, Python 3.12.4 on Windows; includes 10/100/500
message coverage, 15000-character message, failed middle chunk/retry, same-day
append, secret no-egress/no-write, source attribution, conflict/supersession,
interrupted transaction recovery, rollback conflict and Windows concurrent lock.
RISKS: deterministic fake-model responses test engineering behavior only.
Rule-based sensitivity and injection detection are incomplete; no quality claim.
New schema-v2 IDs differ from legacy slugs; legacy remains non-projectable until
reviewed migration. Existing user files have not been migrated.
NEXT STEP: real pointer hook, bounded claim projection, safe installation/CLI and
installed synthetic integration probes. No host deployment or remote CI yet.
