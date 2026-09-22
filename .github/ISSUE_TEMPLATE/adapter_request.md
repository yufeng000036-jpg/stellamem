---
name: Adapter request
about: Request support for a host, framework or agent platform
title: "[Adapter] "
labels: ["adapter"]
---

## Which host / framework / agent platform

<!-- e.g. Claude Code, Cursor, a custom agent, another chat platform -->

## Why Stellamem for it

<!-- What memory governance problem would this solve for you? -->

## How the host exposes conversation and prompt hooks

<!--
If you know:
- How does it store conversation history?
- Is there a pre-prompt / pre-model hook?
- Where would a memory projection hook into the system prompt?
-->

## Willing to contribute it

- [ ] I can help build the adapter
- [ ] I can test the adapter
- [ ] I am only requesting it

> The core compiler is framework-agnostic and depends only on the Python
> standard library, so a new adapter is usually a thin integration layer.
> See `docs/INTEGRATION.md`.
