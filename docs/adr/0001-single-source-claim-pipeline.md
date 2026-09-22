# ADR 0001: Shared pipeline and policy
Status: accepted, 2026-09-22.
Keep Python standard library and Markdown canonical storage. extract and
sleep_purify share source_reader, pipeline, normalizer, security, model_client,
relation and store modules; entrypoints only orchestrate.
Rules live once in shared/policy-rules.json for Python and JavaScript.
Reject truncated/ambiguous JSON instead of guessing completed extraction.
Scope IDs use canonical tuple SHA-256 (24 hex digits), revision adds exact value.
Legacy lossy IDs remain readable, require review before schema-v2 projection.
Confidence is never increased by emotion or importance. Sensitive/identity and
unverified provenance require review regardless of confidence. Secrets never
enter model input, inbox, canonical output, diagnostic text or adapter context.
Loopback-only client ignores proxies and refuses redirects. Remote providers
remain disabled until separate user-approved egress design.
