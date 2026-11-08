// Negative-sample eval, second version (wave 2): an expired or
// superseded fact that still gets injected is a FAIL. Retrieval quality
// alone cannot catch this — the trap questions match the old fact
// word-for-word, so a retrieval-only check WOULD find it. The eval proves
// the projection gate blocks it anyway.
//
// Wave 2 hardens the eval itself so it cannot go quietly green:
// - Sentinel: a forbidden record that was retrieved AND injected trips
//   the sentinel. A tripped sentinel fails the run and the run's verdict
//   must not be published.
// - Versioned pool: the fixtures carry a version + changelog (see
//   negative_samples.json and negative_samples.md); the pool is rotated
//   on a schedule instead of being memorised by the retriever.
// - Seven-field exchange schema (XH-workbuddy schema): every per-case
//   result carries arm_executed, criteria_digest, injected_fault,
//   expected_verdict, observed_verdict, run_id and evidence_source.
//   expected_verdict is a verdict of its own ("检出故障" / "正常注入"),
//   never PASS/FAIL: a control that does nothing would PASS a PASS/FAIL
//   check, which is exactly what a negative control must not allow.
//   A run also emits a manifest of four elements (schema version,
//   criteria_digest, run_id, pool version) that binds every result to
//   the criteria and the pool it was measured against. A must-fail sample
//   without a matching run_id is not current evidence (see
//   isCurrentEvidence).
//
// Run: node benchmark/longmemeval/negative_samples.mjs
// Exit 0 = every case passes and the sentinel is quiet; exit 1 = at
// least one FAIL, or the sentinel tripped.
// Fixtures: negative_samples.json (fictional data only, no private memory).
// The same runner is imported by tests/negative-samples.test.mjs.
// Fixture path override (tests only): NEGATIVE_SAMPLES_FIXTURES.
import { mkdtemp, mkdir, writeFile, readFile } from "node:fs/promises";
import { createHash, randomBytes } from "node:crypto";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { project } from "../../adapters/openclaw/index.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIXTURES = process.env.NEGATIVE_SAMPLES_FIXTURES ||
  path.join(HERE, "negative_samples.json");

// Seven-field negative-control exchange schema, version 1.
export const SCHEMA_VERSION = 1;
export const ARM_EXECUTED = "negative-control";
// Verdict vocabulary. expected_verdict is deliberately NOT PASS/FAIL.
export const EXPECTED_FAULT_DETECTED = "检出故障";
export const EXPECTED_NORMAL_INJECTION = "正常注入";
export const OBSERVED_FAULT_DETECTED = "检出故障";
export const OBSERVED_NOT_DETECTED = "未检出故障";
export const OBSERVED_NORMAL_INJECTION = "正常注入";
export const OBSERVED_MISSING_INJECTION = "注入缺失";

export function canonical(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return "[" + value.map(canonical).join(",") + "]";
  const keys = Object.keys(value).sort();
  return "{" + keys.map((k) => JSON.stringify(k) + ":" + canonical(value[k])).join(",") + "}";
}

export function sha256Hex(text) {
  return createHash("sha256").update(text, "utf8").digest("hex");
}

export function faultOf(c) {
  if (typeof c.fault === "string" && c.fault) return c.fault;
  const match = /^neg-(expired|outdated|superseded|status|future|ambiguous|control)-/.exec(c.id || "");
  if (match) return match[1] === "control" ? "none" : match[1];
  return "unknown";
}

export function isNegativeCase(c) {
  return faultOf(c) !== "none";
}

export function expectedVerdict(c) {
  return isNegativeCase(c) ? EXPECTED_FAULT_DETECTED : EXPECTED_NORMAL_INJECTION;
}

export function observedVerdict(c, leaked, missing) {
  if (isNegativeCase(c)) {
    if (leaked.length) return OBSERVED_NOT_DETECTED;
    if (missing.length) return OBSERVED_MISSING_INJECTION;
    return OBSERVED_FAULT_DETECTED;
  }
  return missing.length ? OBSERVED_MISSING_INJECTION : OBSERVED_NORMAL_INJECTION;
}

// Digest of one case's criteria: the question, the must/must-not lists,
// the injected fault and the pool version. A verdict is only meaningful
// against the criteria it was measured with; the digest binds them.
export function criteriaDigest(c, poolVersion = 0) {
  return sha256Hex(canonical({
    id: c.id,
    question: c.question,
    must_contain: [...(c.must_contain || [])].sort(),
    must_not_contain: [...(c.must_not_contain || [])].sort(),
    injected_fault: faultOf(c),
    pool_version: poolVersion,
  }));
}

// Digest of the whole pool's criteria, for the run manifest.
export function poolCriteriaDigest(cases, poolVersion = 0) {
  const perCase = [...cases]
    .sort((a, b) => String(a.id).localeCompare(String(b.id)))
    .map((c) => ({ id: c.id, digest: criteriaDigest(c, poolVersion) }));
  return sha256Hex(canonical({ pool_version: poolVersion, criteria: perCase }));
}

// Normalise a parsed fixture file. Version 0 is the legacy bare-array
// format (no version field); a versioned pool is an object with
// {version, changelog, cases}. Old pools stay identifiable by version.
export function loadPoolData(parsed) {
  if (Array.isArray(parsed)) {
    return { version: 0, changelog: [], cases: parsed };
  }
  if (!parsed || typeof parsed !== "object" || !Array.isArray(parsed.cases)) {
    throw new Error("negative-sample pool must be an array of cases or " +
      "{version, changelog, cases}");
  }
  return {
    version: Number.isInteger(parsed.version) ? parsed.version : 0,
    changelog: Array.isArray(parsed.changelog) ? parsed.changelog : [],
    cases: parsed.cases,
  };
}

export function newRunId() {
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d+Z$/, "Z");
  return `neg-${stamp}-${randomBytes(3).toString("hex")}`;
}

// The run manifest: the four elements that bind every result in the run
// to the schema, the criteria, the run and the pool.
export function buildManifest({ schemaVersion, criteriaDigest: digest, runId, poolVersion }) {
  return {
    schema_version: schemaVersion,
    criteria_digest: digest,
    run_id: runId,
    pool_version: poolVersion,
  };
}

// True only when the record belongs to this run: its run_id matches AND
// it carries a criteria digest. A must-fail sample without a run_id is
// not current evidence — it may be a stale copy from an older run, an
// older pool or older criteria, and must not be quoted as this run's
// result.
export function isCurrentEvidence(record, runId) {
  return !!record && typeof record === "object" &&
    typeof record.run_id === "string" && record.run_id.length > 0 &&
    record.run_id === runId &&
    typeof record.criteria_digest === "string" && record.criteria_digest.length > 0;
}

export function defaultRecord(overrides = {}) {
  return {
    schema_version: 2, claim_id: "c-neg", revision_id: "r-neg", status: "active",
    kind: "fact", subject: "user", scope: "global", predicate: "p",
    value: "fictional", evidence: "fictional", evidence_verified: true, source: "user",
    sensitivity: "PRIVATE", write_policy: "allow", injection_policy: "recall",
    ...overrides,
  };
}

export async function runCase(c, ctx = {}) {
  const runId = ctx.runId || newRunId();
  const poolVersion = ctx.poolVersion ?? 0;
  const root = await mkdtemp(path.join(os.tmpdir(), "stellamem-neg-"));
  const file = path.join(root, "semantic/facts.md");
  await mkdir(path.dirname(file), { recursive: true });
  const records = c.records.map((r) => defaultRecord(r));
  await writeFile(file, "<!-- stellamem:v2 -->\n" +
    records.map((x) => "```stellamem\n" + JSON.stringify(x) + "\n```\n").join("\n"));
  const out = await project(root, c.question);
  const leaked = (c.must_not_contain || []).filter((s) => out.includes(s));
  const missing = (c.must_contain || []).filter((s) => !out.includes(s));
  const expected = expectedVerdict(c);
  const observed = observedVerdict(c, leaked, missing);
  // Sentinel: a forbidden string in the projection means the record was
  // retrieved AND injected despite the gate. That is the one failure
  // that must never be silent.
  const sentinelTripped = leaked.length > 0;
  return {
    id: c.id,
    pass: observed === expected,
    leaked, missing, reason: c.reason,
    sentinel_tripped: sentinelTripped,
    arm_executed: ARM_EXECUTED,
    criteria_digest: criteriaDigest(c, poolVersion),
    injected_fault: faultOf(c),
    expected_verdict: expected,
    observed_verdict: observed,
    run_id: runId,
    evidence_source: `negative_samples.json v${poolVersion}#${c.id}`,
  };
}

export async function runAll(cases, options = {}) {
  const runId = options.runId || newRunId();
  const poolVersion = options.poolVersion ?? 0;
  const results = [];
  for (const c of cases) results.push(await runCase(c, { runId, poolVersion }));
  const failed = results.filter((r) => !r.pass);
  const sentinelIds = results.filter((r) => r.sentinel_tripped).map((r) => r.id);
  const manifest = buildManifest({
    schemaVersion: SCHEMA_VERSION,
    criteriaDigest: poolCriteriaDigest(cases, poolVersion),
    runId,
    poolVersion,
  });
  return {
    run_id: runId,
    manifest,
    results,
    total: results.length,
    passed: results.length - failed.length,
    failed: failed.length,
    sentinel_tripped: sentinelIds.length > 0,
    sentinel_ids: sentinelIds,
  };
}

export async function runPool(pool, options = {}) {
  const data = loadPoolData(pool);
  return runAll(data.cases, { poolVersion: data.version, ...options });
}

async function main() {
  const pool = loadPoolData(JSON.parse(await readFile(FIXTURES, "utf8")));
  const run = await runPool(pool);
  for (const r of run.results) {
    const detail = r.pass ? "" :
      ` leaked=[${r.leaked.join(", ")}] missing=[${r.missing.join(", ")}]`;
    console.log(`${r.pass ? "PASS" : "FAIL"} ${r.id}` +
      ` expected=${r.expected_verdict} observed=${r.observed_verdict}${detail}`);
  }
  if (run.sentinel_tripped) {
    console.log(`SENTINEL TRIPPED: ${run.sentinel_ids.join(", ")} — ` +
      "forbidden record retrieved and injected; " +
      "this run's verdict must not be published.");
  }
  console.log(`manifest: ${JSON.stringify(run.manifest)}`);
  console.log(`negative samples: ${run.passed}/${run.total} pass` +
    `; sentinel: ${run.sentinel_tripped ? "TRIPPED" : "quiet"}`);
  if (run.failed || run.sentinel_tripped) process.exitCode = 1;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await main();
