// Negative-sample eval, second version (wave 2): an expired or
// superseded fact that still gets injected is a FAIL. The same cases are
// runnable standalone via benchmark/longmemeval/negative_samples.mjs.
// Wave 2 adds: a sentinel that trips when a forbidden record is retrieved
// AND injected; a versioned, rotating pool; and a seven-field exchange
// schema (arm_executed / criteria_digest / injected_fault /
// expected_verdict / observed_verdict / run_id / evidence_source) with a
// four-element run manifest (schema version, criteria_digest, run_id,
// pool version).
import test from "node:test";
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import {
  ARM_EXECUTED,
  SCHEMA_VERSION,
  buildManifest,
  criteriaDigest,
  isCurrentEvidence,
  loadPoolData,
  poolCriteriaDigest,
  runAll,
  runCase,
  runPool,
} from "../benchmark/longmemeval/negative_samples.mjs";

const execFileAsync = promisify(execFile);
const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIXTURES = path.join(HERE, "..", "benchmark/longmemeval/negative_samples.json");
const RUNNER = path.join(HERE, "..", "benchmark/longmemeval/negative_samples.mjs");

const SEVEN_FIELDS = [
  "arm_executed", "criteria_digest", "injected_fault", "expected_verdict",
  "observed_verdict", "run_id", "evidence_source",
];

async function loadPool() {
  return loadPoolData(JSON.parse(await readFile(FIXTURES, "utf8")));
}

test("negative samples: no expired or superseded fact is injected", async () => {
  const pool = await loadPool();
  assert.ok(pool.cases.length >= 9, "second version must have at least 9 cases");
  const run = await runPool(pool);
  const failed = run.results.filter((r) => !r.pass);
  assert.deepEqual(failed, [],
    "negative-sample failures: " + JSON.stringify(failed));
});

test("seven-field schema: every result carries all seven fields", async () => {
  const pool = await loadPool();
  const run = await runPool(pool);
  for (const r of run.results) {
    for (const field of SEVEN_FIELDS) {
      assert.ok(r[field] !== undefined && r[field] !== "",
        `${r.id} is missing seven-field value: ${field}`);
    }
    assert.equal(r.arm_executed, ARM_EXECUTED);
    assert.match(r.criteria_digest, /^[0-9a-f]{64}$/);
    assert.equal(r.criteria_digest, criteriaDigest(
      pool.cases.find((c) => c.id === r.id), pool.version));
    assert.match(r.run_id, /^neg-/);
    assert.equal(r.run_id, run.run_id, "all results share one run_id");
    assert.ok(r.evidence_source.includes(`v${pool.version}#${r.id}`));
  }
});

test("expected_verdict is a verdict of its own, never PASS/FAIL", async () => {
  const pool = await loadPool();
  const run = await runPool(pool);
  for (const r of run.results) {
    assert.ok(!["PASS", "FAIL"].includes(r.expected_verdict),
      `${r.id}: expected_verdict must not be PASS/FAIL`);
    assert.ok(!["PASS", "FAIL"].includes(r.observed_verdict),
      `${r.id}: observed_verdict must not be PASS/FAIL`);
    assert.ok(["检出故障", "正常注入"].includes(r.expected_verdict));
  }
  const faults = run.results.filter((r) => r.injected_fault !== "none");
  assert.ok(faults.every((r) => r.expected_verdict === "检出故障"));
  const controls = run.results.filter((r) => r.injected_fault === "none");
  assert.equal(controls.length, 1, "exactly one positive control");
  assert.equal(controls[0].expected_verdict, "正常注入");
  assert.equal(controls[0].observed_verdict, "正常注入");
});

test("run manifest binds schema version, criteria digest, run_id, pool version", async () => {
  const pool = await loadPool();
  const run = await runPool(pool);
  assert.equal(run.manifest.schema_version, SCHEMA_VERSION);
  assert.equal(run.manifest.run_id, run.run_id);
  assert.equal(run.manifest.pool_version, pool.version);
  assert.equal(run.manifest.criteria_digest,
    poolCriteriaDigest(pool.cases, pool.version));
  assert.deepEqual(Object.keys(run.manifest).sort(),
    ["criteria_digest", "pool_version", "run_id", "schema_version"]);
  assert.deepEqual(buildManifest({
    schemaVersion: 1, criteriaDigest: "x".repeat(64),
    runId: "neg-test", poolVersion: 2,
  }), {
    schema_version: 1, criteria_digest: "x".repeat(64),
    run_id: "neg-test", pool_version: 2,
  });
});

test("a must-fail sample without a matching run_id is not current evidence", async () => {
  const pool = await loadPool();
  const run = await runPool(pool);
  const sample = run.results.find((r) => r.injected_fault !== "none");
  assert.ok(isCurrentEvidence(sample, run.run_id));
  assert.equal(isCurrentEvidence({ ...sample, run_id: undefined }, run.run_id), false);
  assert.equal(isCurrentEvidence({ ...sample, run_id: "neg-stale-run" }, run.run_id), false);
  assert.equal(isCurrentEvidence({ ...sample, run_id: run.run_id,
    criteria_digest: undefined }, run.run_id), false);
  assert.equal(isCurrentEvidence(null, run.run_id), false);
});

test("sentinel is quiet when the guards hold", async () => {
  const pool = await loadPool();
  const run = await runPool(pool);
  assert.equal(run.sentinel_tripped, false);
  assert.deepEqual(run.sentinel_ids, []);
  assert.ok(run.results.every((r) => r.sentinel_tripped === false));
});

test("sentinel trips when a forbidden record is retrieved and injected", async () => {
  // An active fact that matches the question MUST inject; if the runner
  // is pointed at it as forbidden, the sentinel must fire. This is the
  // same outcome as removing the eligible()/project() guards: the gate
  // no longer blocks the record, so the forbidden string lands in the
  // projection and the sentinel trips.
  const result = await runCase({
    id: "meta-violation",
    question: "我最喜欢什么颜色？",
    must_not_contain: ["枫叶红"],
    must_contain: [],
    records: [{ claim_id: "c-meta", revision_id: "r-meta",
      value: "虚构：最喜欢的颜色是枫叶红", evidence: "虚构：最喜欢的颜色是枫叶红" }],
    reason: "meta-test: the sentinel must catch an injected forbidden string",
  });
  assert.equal(result.pass, false);
  assert.deepEqual(result.leaked, ["枫叶红"]);
  assert.equal(result.sentinel_tripped, true);
  assert.equal(result.expected_verdict, "检出故障");
  assert.equal(result.observed_verdict, "未检出故障");
});

test("sentinel trips at the CLI: exit 1 and an explicit warning", async () => {
  const dir = await mkdtemp(path.join(os.tmpdir(), "stellamem-neg-cli-"));
  const fixture = path.join(dir, "pool.json");
  await writeFile(fixture, JSON.stringify({
    version: 99, changelog: [],
    cases: [{
      id: "neg-trip-01", fault: "expired",
      question: "我最喜欢喝什么咖啡？",
      must_not_contain: ["冰滴咖啡"], must_contain: [],
      reason: "cli sentinel test: active fact listed as forbidden",
      records: [{ claim_id: "c-trip", revision_id: "r-trip",
        value: "虚构：喜欢喝冰滴咖啡", evidence: "虚构：喜欢喝冰滴咖啡" }],
    }],
  }), "utf8");
  let caught = null;
  try {
    await execFileAsync(process.execPath, [RUNNER],
      { env: { ...process.env, NEGATIVE_SAMPLES_FIXTURES: fixture } });
  } catch (error) {
    caught = error;
  }
  assert.ok(caught, "tripping run must exit non-zero");
  assert.equal(caught.code, 1);
  assert.match(caught.stdout, /SENTINEL TRIPPED: neg-trip-01/);
  assert.match(caught.stdout, /must not be published/);
  assert.match(caught.stdout, /"pool_version":99/);
});

test("pool rotation: versioned pool with changelog; legacy pools identifiable", async () => {
  const pool = await loadPool();
  assert.ok(Number.isInteger(pool.version) && pool.version >= 2,
    "pool must carry a version >= 2");
  assert.ok(Array.isArray(pool.changelog) && pool.changelog.length > 0,
    "pool must carry a changelog");
  const latest = pool.changelog[pool.changelog.length - 1];
  assert.equal(latest.version, pool.version,
    "latest changelog entry must match the pool version");
  for (const entry of pool.changelog) {
    assert.ok(Number.isInteger(entry.version), "changelog entry needs a version");
    assert.ok(entry.date && entry.change, "changelog entry needs a date and a change note");
  }
  const ids = pool.cases.map((c) => c.id);
  assert.equal(new Set(ids).size, ids.length, "case ids must be unique");
  assert.ok(pool.cases.every((c) => typeof c.fault === "string" && c.fault),
    "every case must declare its injected fault");

  // Legacy bare-array pools (version 1) are still readable and are
  // identifiable as version 0, so an old pool can never pass as current.
  const legacy = loadPoolData([{ id: "neg-old-01", question: "q",
    must_contain: [], must_not_contain: [], records: [], reason: "old" }]);
  assert.equal(legacy.version, 0);
  assert.deepEqual(legacy.changelog, []);
  assert.equal(legacy.cases.length, 1);

  // runAll binds the pool version into every result and the manifest.
  const run = await runAll(legacy.cases, { poolVersion: legacy.version, runId: "neg-legacy" });
  assert.equal(run.manifest.pool_version, 0);
  assert.equal(run.results[0].evidence_source, "negative_samples.json v0#neg-old-01");
});

test("the runner detects a violation instead of always passing", async () => {
  // Same trap as the sentinel test, kept as the original regression:
  // the runner must report FAIL when a forbidden string is injected.
  const result = await runCase({
    id: "meta-violation",
    question: "我最喜欢什么颜色？",
    must_not_contain: ["枫叶红"],
    must_contain: [],
    records: [{ claim_id: "c-meta", revision_id: "r-meta",
      value: "虚构：最喜欢的颜色是枫叶红", evidence: "虚构：最喜欢的颜色是枫叶红" }],
    reason: "meta-test: the runner must catch an injected forbidden string",
  });
  assert.equal(result.pass, false);
  assert.deepEqual(result.leaked, ["枫叶红"]);
});
