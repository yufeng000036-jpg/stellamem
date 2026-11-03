# Stellamem · 星忆

> **Give AI memory a Git.** Traceable, reviewable, revertible.

Stellamem is an AI memory system that keeps memory on the machine and turns
conversation into claims with evidence, relations, and human review. The
model client accepts loopback endpoints only (`compiler/model_client.py`).
The four goals are: remember, remember accurately, do not mix subjects, and
do not leak. They are goals, not a completed acceptance.

Running locally and retrieving text are not the differentiator. The MemOS
README (notes dated 2026-11-04) already describes a local OpenClaw plugin
with SQLite, FTS5, and vectors, and it self-reports LoCoMo 88.83 and
LongMemEval 89.20. Those figures are theirs. **Stellamem has no public
benchmark score.** The difference is governance: source hashes, pre-write
snapshots, a human review inbox, a secret gate, and subject namespaces.
Chinese status and the 9-point list: [`README.md`](README.md).

Code on `main` (`e732da5`, including PR #15, #26, #28, and #29) is **not** a
production deployment. Stage E is NOT MET. Stage F is CLOSED. See
[`docs/verification/STAGES_E_F_STATUS.md`](docs/verification/STAGES_E_F_STATUS.md).
An external reviewer's subjective rating of the earlier main `803df0d` was
7.8/10. That is not a benchmark, not a new rating of `e732da5`, and it does
not pass Stage E or open Stage F. The five code gaps from that review are
fixed in #28 and #29. Real-data verification is still pending and needs the
owner's approval. LongMemEval v2 is still running and has no result, so there
is no score.

Current recount on this tree: **577 passed + 42 subtests** (Python 3.12.3),
**8** Node tests. The 563 on `803df0d` is the previous recount.

<p align="center">
  <b>🚧 Alpha (v0.2.0-alpha)</b> ·
  🦞 First host: OpenClaw ·
  🧩 Framework-agnostic core ·
  🔒 Memory stays on the machine ·
  📝 Markdown-first
</p>

<p align="center">
  <a href="README.md">中文</a> ·
  <a href="docs/QUICKSTART.md">Quick Start</a> ·
  <a href="docs/ARCHITECTURE.md">Architecture</a> ·
  <a href="docs/PHILOSOPHY.md">Philosophy</a> ·
  <a href="SECURITY.md">Security</a> ·
  <a href="docs/USE_CASES.md">Use Cases</a> ·
  <a href="CONTRIBUTING.md">Contributing</a>
</p>

---

## What it solves

The hard problem in AI memory is **not** that "the AI can't remember".

It is that:

> **once the AI has remembered, you cannot tell why it remembered, where it came
> from, whether it is stale, whether it misread you, or whether you can undo it.**

A realistic example:

```text
User: I like concise answers.

Months later:
User: For technical questions, please answer in detail.

Plain memory: it may overwrite, duplicate, contradict, or silently go wrong.
```

What Stellamem does instead:

```text
Conversation
   ↓
Claim
   ↓  scope        — which situation this belongs to
   ↓  evidence     — what was actually said
   ↓  relation     — how it relates to existing memory
   ↓  revision     — which new version it produces
Memory
   ↓
Version history — old versions are kept and can be rolled back
```

> **Memory should be inspectable infrastructure, not invisible magic.**

---

## Why Stellamem

- **Not a chat-log backup.** Conversation is raw material; long-term memory is the
  refined result. They are not the same thing.
- **Every memory has a source.** Claims carry `evidence` + `provenance`. When a
  source cannot be verified, Stellamem **raises instead of fabricating**.
- **Scoped.** The same topic can carry different preferences in different
  situations, without cross-contamination.
- **Versioned.** `SUPERSEDES` updates a value while the old one stays in the
  version chain.
- **Conflicts don't vanish.** `CONTRADICTS` is logged; there is no silent overwrite.
- **Secrets never enter.** Credentials are blocked **before** any model call.
- **Sensitive content is never auto-written.** Sensitive and identity claims go to
  a human review inbox.
- **You own your memory.** View it, correct it, reject it, delete it, roll it back.

---

## Principles

| Principle | Meaning |
|---|---|
| **Memory is not a dump** | A chat log is not long-term memory. |
| **Secret is not memory** | Credentials should never become long-term memory. |
| **Sensitive is not auto-write** | Worth keeping ≠ allowed to write automatically. |
| **Memory must have provenance** | The AI should know *why* it believes something. |
| **Conflict must not disappear** | Conflicts may not be silently overwritten. |
| **Users own their memory** | Users can view / edit / reject / delete / roll back / export it. |

> Expanded in [`docs/PHILOSOPHY.md`](docs/PHILOSOPHY.md).

---

## Architecture

```mermaid
flowchart TD
    A[Agent Conversation] --> B[Source Pointer]
    B --> C[Claim Extraction]
    C --> D[Security Policy]
    D --> E[Relation Engine]
    E --> F[Human Review Inbox]
    F --> G[Canonical Memory]
    G --> H[Context Projection]
    H --> I[AI Agent]
```

Memory unit lifecycle:

```text
Conversation → Claim → Evidence → Scope → Revision → Relation → Memory
```

> See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for details.

---

## What Stellamem does

### Claim-based memory
Long-term memory is distilled into structured claims:
`kind / subject / scope / predicate / value / confidence / evidence / provenance`.

### Provenance
Every claim carries a source pointer and a content hash. If the source cannot be
resolved, Stellamem raises rather than inventing content.

### Scope
Different situations can hold different preferences: `global` "concise" and
`technical` "detailed" coexist.

### Revision
A fact can evolve; the old revision enters the version chain instead of vanishing.

### Relation Engine
Eight relations: `NEW` / `DUPLICATE` / `REINFORCES` / `REFINES` / `COMPATIBLE` /
`SUPERSEDES` / `CONTRADICTS` / `UNCERTAIN`.

### Human Review
Sensitive, identity, low-confidence and suspicious claims go to an inbox for
`approve` / `reject` / `edit`.

### Secret Gate
Secrets are deterministically detected and blocked **before any model call** —
not written, not logged, not exfiltrated.

### Conflict Handling
Conflicts are written to a conflict log with both sides preserved. No silent
overwrite.

### Transaction / Rollback
A memory write is a transaction with a journal: recoverable and reversible, and a
rollback refuses to clobber later edits.

### Context Projection
Only task-relevant, approved claims are injected into the agent, under a global
character budget.

---

## What it is not

Stellamem is **not**:

- ❌ a tool to break into ChatGPT Memory
- ❌ a chat-log backup utility
- ❌ a cloud user-profiling platform
- ❌ a service that uploads all of your private data
- ❌ a project that only exists if you use OpenClaw

**OpenClaw is the first host wired in this tree, not the only possible host.** Isolated-host injection transport has evidence. Stage E is not passed.
The core compiler is framework-agnostic.

---

## Status matrix

| Capability | Status |
|---|---|
| OpenClaw Adapter | Alpha · isolated-host injection transport has evidence; SQLite auto-intake is not adapted; Stage E is NOT MET |
| Claim compiler | ✅ |
| Relation Engine | ✅ |
| Source provenance | ✅ |
| Human Review Inbox (CLI) | ✅ |
| Secret Gate | ✅ |
| Context Projection | ✅ |
| Transaction / Rollback | ✅ |
| Crash recovery | ✅ |
| Local models (llama.cpp etc.) | ✅ |
| Remote cloud models | 🚧 not supported (loopback only) |
| Other agent adapters | 🚧 |
| GUI | 🚧 No desktop product GUI. Bridge loopback page exists and is test-imported only |
| Sync | 🚧 |

> Status reflects the **real implementation** only. 🚧 means not yet built.

---

## Quick start

```bash
# 1. Environment check (read-only, changes nothing)
python setup.py check --offline

# 2. Dry run — see exactly what would be created
python setup.py install --mem-root <MEMORY_ROOT> --dry-run

# 3. Install (idempotent; never overwrites your customised files)
python setup.py install --mem-root <MEMORY_ROOT> --config <path/to/config.json>

# 4. Offline demo with fictional data (no model needed)
python demo/run_demo.py --offline

# 5. Self-check
python compiler/memory.py doctor
```

Requirements: **Python 3.10+** (the core uses only the standard library). Node is
optional and only needed for OpenClaw integration.

> 👉 Full steps: [`docs/QUICKSTART.md`](docs/QUICKSTART.md).
> ⚠️ A local model service is required (llama.cpp / Ollama / LM Studio) and it
> must be a **loopback** endpoint.

### Install with an AI coding agent

If you don't code, you can hand the repository to an AI that can operate a
computer and have it read [`AI_BOOTSTRAP.md`](AI_BOOTSTRAP.md). Paste this:

```text
Clone this repository and read AI_BOOTSTRAP.md.
Install Stellamem in an isolated way.
Do not modify existing memory or agent configuration without asking me first.
Run the built-in checks and report whether installation succeeded.
```

---

## Safety and privacy

Short version below; full version in [`SECURITY.md`](SECURITY.md).

- **On this machine** — memory is stored locally by default. That is where the
  files live, not a claim that no other project runs locally.
- **Original text** — the compile path stores source pointers and content hashes.
  Optional `compiler/raw_archive.py` writes a second, append-only copy under
  `<memories_root>/_raw/`. Do not read this as "a second copy is never stored."
- **Secret before model** — secrets are detected and blocked before any model call.
- **Sensitive review** — sensitive and identity content needs human confirmation
  and is **never auto-written**.
- **Provenance or nothing** — when a source cannot be verified, Stellamem does not
  fabricate.

---

## Benchmark

**There is no public benchmark score.** MemOS README figures are not Stellamem
scores. The old 30-diary recall ≈ 0.705 / precision ≈ 0.505 cannot be reproduced
with the current matcher (`benchmark/README.md`). `False Write = 0` only means
that failure was not observed on the defined adversarial samples. Stage E's
reader Recall@1 of 1.00 is a synthetic projection contract and does not pass
acceptance. The evaluation plan is unverified:
[`docs/evaluation-plan.md`](docs/evaluation-plan.md).

---

## Current limitations

- **OpenClaw is the only host with an isolated transport trial** (core is
  framework-agnostic; SQLite auto-intake is not adapted; other adapters are not done)
- **Remote cloud models are not supported** — the model client is loopback-only
- **Adapter retrieval is still keyword-based** — it can under-recall natural
  questions that share no token with a stored claim
- **The local 4B model shows kind-classification drift**
- **English temporary-emotion detection is incomplete**
- **`distill.py` is not in the tree.** Text sleep-consolidation is on main and is
  not candidate A (candidate A is planned, not implemented; see the Chinese README)
- **No desktop product GUI** (the bridge loopback page is not on the production call path)
- **Not yet validated by a large external user base over time**
- **Merged to main is not deployed to production.** Stage E is NOT MET. Stage F is CLOSED

This transparency is part of the design, not a hidden defect.

---

## Roadmap

### Alpha (now)
- reliability, retrieval quality, external testing

### Beta
- more host adapters, semantic retrieval, migration stability, review UX

### Future
- desktop GUI, companion integrations, MCP, encrypted sync, team memory,
  enterprise governance

See [`ROADMAP.md`](ROADMAP.md).

---

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md).
> ⚠️ Any change to memory behaviour **must ship a regression test**.

Please do not report security issues in a public issue; use the channel in
[`SECURITY.md`](SECURITY.md).

---

## Vision

Future AI systems will exist for years. If an AI is to genuinely accompany, work
with, or serve a person over time, it must be able to answer:

- Why do I know this?
- When did the user say it?
- Is it still true?
- Has it changed since?
- What if two memories conflict?
- Can the user undo it?
- Should sensitive content be recalled at all?

The aim is a Git-like history for AI memory: traceable, reviewable, revertible.
Governance is the differentiator. Local execution and retrieval are not.

**OpenClaw is the first stop, not the destination.** Code on main is not a production release.

---

## License

MIT © 宇枫 · see [`LICENSE`](LICENSE)
