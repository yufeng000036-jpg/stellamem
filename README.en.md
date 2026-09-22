# Stellamem

### Give AI memory a Git.

**Stellamem is a Markdown-first, Local-first long-term memory compiler for AI agents.**
It lets AI memory be inspected, explained, versioned, and rolled back.

Runs locally · You own your data · Free and open source

Stellamem — Compilable, auditable, local-first long-term memory for AI agents.

> ⚠️ **Alpha**: currently `v0.1.0-alpha`. Security boundaries are still being hardened — read [SECURITY.md](SECURITY.md) before using on important data.

---

## What it solves

- **A digestive system for AI**: daily conversation logs are automatically compiled into structured long-term memory (preferences, decisions, lessons, projects), with deduplication, routing, and storage.
- **Memory rot**: long-running AI memory degrades and contradicts itself. Stellamem ships conflict logs, version chains, and relation-judgment components (`judge.py`, 8 relation types); automatic Relation Engine integration into the main flow is still in development (see [ROADMAP.md](ROADMAP.md)), so conflict resolution is human-confirmed for now.
- **White-box transparency**: all memory is markdown. Humans can read it, Git can diff it, every cognitive change has an evidence chain.
- **Model-agnostic**: the extraction layer is a standardized Claim extraction task. Swap in a stronger model, get higher quality memory. 4B is the recommended starting point, not the ceiling.

---

## How it works

```text
AI conversation
→ auto diary (hooks/daily-autolog)
→ daily/*.md
→ compile (compiler/extract.py, local model)
→ structured Claims (kind / subject / scope / predicate / value / confidence)
→ route (compiler/sleep_purify.py)
→ markdown memory (core / long-term / semantic)
→ adapter injects into AI context (adapters/openclaw)
```

Write side and inject side are two independent components. Both are required:
`hooks/` handles "AI conversation → diary", `adapters/` handles "memory → AI context".
See `docs/INTEGRATION.md`.

---

## Directory layout

```text
compiler/      Compile and routing scripts
hooks/         Write side: auto diary hook
adapters/      Inject side: framework adapters
templates/     Empty memory structure templates
demo/          Fictional user, 7 days of diary + one-click demo
benchmark/     Evaluation tools
docs/          Documentation
schema.md      Claim spec
config.example.json  Config template
```

---

## Quick start

1. Copy config: `cp config.example.json config.json`, fill in your paths and model name.
2. Create memory structure: see `templates/` for `core / long-term / semantic`.
3. Start model service, run `compiler/extract.py` to compile diaries.
4. Install write side: `hooks/daily-autolog/`.
5. Install inject side: `adapters/openclaw/`.

Full steps in `docs/INTEGRATION.md`.

---

## Performance ceiling: model-swappable, no fixed limit

Stellamem's compile quality is not tied to any single model. The extraction layer is a standardized Claim extraction task. Any text model that outputs JSON can be plugged in.

| Tier | Model | Hardware | Per-diary | Notes |
|------|-------|----------|-----------|-------|
| Default | Qwen3-4B Q5_K_M | 8GB VRAM | ~7s | Local, zero cost |
| Light | Qwen3-1.7B Q5 | 4GB VRAM | ~3s | Low-end devices |
| Advanced | Qwen3-14B / 32B | 16–24GB VRAM | hardware-dependent | Quality first |
| Cloud | GPT-4o / Claude / DeepSeek | API | network-dependent | Highest quality |

No fixed ceiling. Stronger models mean more accurate extraction, cleaner deduplication, more reliable conflict resolution.
The architecture, schema, and routing logic are model-agnostic — swapping models does not require changing your memory structure.

---

## Current setup and measured data

- Default model: Qwen3-4B Q5_K_M (llama.cpp)
- Backend: llama.cpp + CUDA, n_ctx = 8192
- Measured speed: ~7s per diary, 73 tok/s
- VRAM: fully offloadable on 8GB
- Cost: zero (local)

---

## Hardware requirements

- Recommended: 8GB VRAM + llama.cpp, running Qwen3-4B Q5_K_M.
- Advanced: 16GB VRAM for larger models.
- No GPU: CPU works but is 10x+ slower; or use a cloud API.

---

## Benchmark

30 real diaries: recall 0.705, False Write 0.

> Note: early internal evaluation. Matching method is still being iterated. A reproducible benchmark will be published; feel free to verify.

---

## When to use / when not to

Use: local-first, auditable, version-controlled long-term memory for long-running AI assistants.

Avoid: if you want a turnkey SaaS, prefer no command line, or only need short-term context.

---

## FAQ

**Can I use Claude / GPT?**
Yes. The extraction layer is model-agnostic; any JSON-output model works.

**Where is memory stored? Is it uploaded?**
Local markdown files by default, Git-diffable, no network. Content is only sent to a service provider if you explicitly configure a cloud model API.

**Does a stronger model help?**
Yes. Stronger models mean higher recall, fewer false writes. 4B is the default starting point, not the ceiling.

**How is this different from Mem0 / Zep?**
Markdown-first, diffable, rollback-capable, white-box auditable — not a black-box database.

**Which agent frameworks are supported?**
OpenClaw currently; more adapters coming.

---

## Actively maintained

Stellamem is under active development. Features will keep growing, and the system will keep getting more complete and more polished. Please keep supporting us.

If you find it useful, please Star, open an Issue, or contribute an adapter or model config.

---

## License

MIT, see LICENSE.

---

Made by 宇枫

---

## Evaluation clarification (2026-09-22)

The current 30-diary, single-user benchmark contains 393 expected claims: 277 matched, 116 missed, and 272 classified as extra. Under the current gold set and one-pass LLM matcher, recall is 0.705 and precision is 0.505.

`False Write = 0` only means that no such failure was observed in the defined adversarial cases. It does not prove that every stored memory is correct or that the production false-write rate is zero. These results are suitable for internal version comparisons and should not be generalized.

Scope status: the schema and local compiler now use a four-part stable ID while retaining legacy three-field reads. Nine local safety tests pass; the first real scheduled compile after re-enabling the job remains unverified.
