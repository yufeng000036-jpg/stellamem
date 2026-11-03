# Real isolated OpenClaw hook — 2026-11-02

> Status note (2026-11-04): current main is `e732da5` (includes PR #15, #28,
> and #29). The transport evidence below still does not pass Stage E and does
> not open the Stage F release gate. "Release gate open" later in this file
> means the gate was not passed, not that a release shipped. Merge is not a
> production deployment. SQLite session intake remains unadapted. The 421
> Python tests below are this note's own run.



Official npm OpenClaw 2026.10.9 (8b18b55), Node 24.22.1, Linux x86_64.
The package installs and starts. An exact npm lock is retained under benchmark/host
for this opt-in verification only; the product gains no npm dependency.

Actual entry: `openclaw agent --local --agent main --session-id <new UUID>`.
This is the real embedded OpenClaw host, plugin loader and before_prompt_build
hook. A loopback HTTP provider records the actual final model request and sends
an SSE fixture answer. It tests transport/injection, NOT model intelligence.
No direct project() invocation is used in this harness.

## Confirmed compatibility defect

Old setup snippet enabled the adapter but did not grant conversation access.
With an approved memory, the real host made its provider call without memory.
Enabling `plugins.entries.stellamem-adapter.hooks.allowConversationAccess` made
that memory appear in the real provider request. The negative control removes
that grant again and proves absence. Reference: the installed 2026.10.9
`docs/plugins/hooks.md` permissions section, also
https://docs.openclaw.ai/plugins/hooks .

`setup.py` now emits this explicit grant in its reviewable snippet only. It
never reads/writes an existing host config, restarts a host, enables a channel,
or installs a service. Existing users must review the new snippet separately;
upgrading a generated snippet follows the existing backed-up --upgrade flow.

## Reproduction

```sh
# Copy benchmark/host/{package.json,package-lock.json} to a NEW scratch directory.
# Node must satisfy the pinned host engine (24.22.1 in this run).
npm ci --prefix /scratch/isolated-host --ignore-scripts --no-audit --no-fund
python tools/verify_openclaw_host.py --repo . \
  --host-cli /scratch/isolated-host/node_modules/openclaw/openclaw.mjs \
  --out /scratch/real-host-results.json
```

The script makes a fresh temporary host home/state/config/workspace, synthetic
memory and fake local provider marker. Its child environment is allowlisted,
no account credentials inherited, tools denied, other plugins disabled, no
--deliver or real channel. The default profile and production services are not
accessed. No paid API. Every recall uses a fresh actual host process/session.

## Evidence and boundaries

`real-openclaw-results.json` records 18 passing cases and per-call host receipts:
first/repeated install; pending blocked; permission negative control; explicitly
approved recall; restart in fresh host process; approval rollback; two authorized
scopes; denied scope; user/persona/viewer separation; unresolved conflict; expired
fact; legacy v2 subject namespace plus noninjectable unmanaged prose; malformed
configuration rejected before provider call; actual writer child killed mid-
transaction, projection denied with journal present, recovery twice then actual
hook recall restored. Pending/approval use real compiler/store/transaction code;
the extraction callback is an explicit deterministic fixture, not an LLM.

Local regressions: 421 Python / 8 Node passed, offline demo passed, Ruff lint and
121-file format passed, own history secret scan passed. Exact staged export is
Gitleaks 8.24.3 scanned before upload. New installation test asserts the grant's
correct nesting AND byte-for-byte preservation of an existing host config.
Real-host tests are opt-in and NOT part of the seven standard CI jobs.

Known limits: one Linux host version; embedded CLI, not a long-running Gateway;
no OS reboot or soak test; no daily-autolog hook capture in this run; no real LLM
answer-quality claim. OpenClaw reports a manually copied plugin as provenance
unverified; it is explicitly allowlisted only in this temporary test profile.
These limits keep broader integration acceptance and release gate open.

## Rollback

`git revert <host-stage-commit>` removes this snippet repair/test assets without
changing memory. To reproduce the old isolated behavior remove only the temporary
entry's `hooks.allowConversationAccess`; the negative control verifies no recall.
The harness deletes its temporary state when complete; remove its explicit
scratch npm prefix to uninstall the test runtime. Do not run these commands
against production. No optional public module removed or D7–D13 repair claimed.
