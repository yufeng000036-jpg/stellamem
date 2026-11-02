# Stellamem Execution Plan
Baseline 3efebd0. Branch codex/stabilization-p0. Work and test files on E:.
Never touch real memories, credentials, active OpenClaw, LICENSE or Git history.
Each subsystem: ADR -> implementation -> meaningful tests -> diff -> commit.

## P0 (current implementation scope)
- [ ] S1 Shared model client, strict JSON parser, claim normalizer, policy and IDs.
      Secret gate before model/output/storage; independent importance/sensitivity/
      storage/injection permissions. Core identity always pending.
- [ ] S2 Message-aware reader/chunker and summary+pointer hook.
      Cover 10/100/500, huge single message, middle/tail, failed chunk, retries,
      same-minute/out-of-order arrivals and source rotation. No second raw archive.
- [ ] S3 Canonical orchestration, verified provenance, relation integration.
      No silent overwrite; revisions/conflicts recorded; ambiguous relations pending.
      Durable status, content-aware cursor, single writer, transactional recovery.
- [ ] S4 Claim-level context projection; bounded total prompt budget, no system
      injection from memory, no unapproved sensitive/legacy data.
- [ ] S5 Honest installer/check/demo, rollback/migration safeguards, cross-platform
      startup. Fresh sandbox install; failure must exit nonzero.
- [ ] S6 Synchronize schema, README languages, bootstrap, security, roadmap,
      changelog, templates and integration docs to actual behavior.

## P1 (release gates / engineering)
- [ ] Windows/Linux/macOS CI definitions: Python+Node tests, lint/format,
      secret scan and basic dependency/security checks.
- [ ] CONTRIBUTING, CODE_OF_CONDUCT, ARCHITECTURE, DEVELOPMENT, Alpha guide,
      issue/PR templates and documented review/commit process.
- [ ] Reproducible synthetic scaling benchmark (10..100000); no borrowed accuracy.
- [ ] Confirm hosted CI after authorized push, real model and installed-host smoke,
      external 5-10 Alpha users and at least a week of use.
      This session cannot fabricate external-user/elapsed-time/hosted CI evidence.

## P2 (deliberately deferred)
Desktop Inbox/Timeline, Pro tooling, opt-in E2EE Sync, Team/RBAC, Enterprise/SSO,
signing/SBOM and additional adapters. Core stays local-first and MIT, no telemetry.
Index is rebuildable; human-readable canonical data and explicit migrations first.

## Acceptance evidence
Record commands/counts and known limits in docs/VERIFICATION.md.
Offline fixtures establish engineering behavior, not language-model accuracy.
No public release, paid API, deployment, real-data migration or upload is implied.
