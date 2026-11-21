// Negative-sample eval, second version (wave 2): an expired or
// superseded fact that still gets injected is a FAIL. The same cases are
// runnable standalone via benchmark/longmemeval/negative_samples.mjs.
// Wave 2 adds: a sentinel that trips when a forbidden record is retrieved
// AND injected; a versioned, rotating pool; and a seven-field exchange
// schema (arm_executed / criteria_digest / injected_fault /
// expected_verdict / observed_verdict / run_id / evidence_source) with a
// four-element run manifest (schema version, criteria_digest, run_id,
// pool version).
// Wave 3 adds a third verdict, NOT_EXECUTED: a case that did not run at
// all (newer contract version, or a crash before any verdict) is recorded
// with pass: null and is never folded into 0 by summarize().
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
  CONTRACT_VERSION,
  REASON_CODES,
  SCHEMA_VERSION,
  armOf,
  buildManifest,
  criteriaDigest,
  isCurrentEvidence,
  loadPoolData,
  materializeCase,
  poolCriteriaDigest,
  randomMustFailCase,
  runAll,
  runCase,
  runPool,
  summarize,
  validateReasonBand,
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
  // Filter by verdict, not by !pass: a NOT_EXECUTED result carries
  // pass: null, which must never read as a failure.
  const failed = run.results.filter((r) => r.verdict === "FAIL");
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
  assert.equal(result.verdict, "FAIL");
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
  assert.equal(result.verdict, "FAIL");
  assert.deepEqual(result.leaked, ["枫叶红"]);
});

test("a case requiring a newer contract version is NOT_EXECUTED, not a fail", async () => {
  // The contract wants something the implementation version has not landed
  // yet: the case cannot run, so it must be recorded NOT_EXECUTED with
  // both versions — never folded into 0, never PASS/FAIL.
  const result = await runCase({
    id: "meta-future-contract",
    question: "我最喜欢什么颜色？",
    must_not_contain: [],
    must_contain: [],
    records: [],
    contract_version: "99",
    reason: "meta-test: contract version the runner does not implement",
  });
  assert.equal(result.verdict, "NOT_EXECUTED");
  assert.equal(result.pass, null);
  assert.equal(result.contract_version, "99");
  assert.equal(result.actual_version, CONTRACT_VERSION);
  assert.equal(result.error.length > 0, true);
});

test("a case that crashes before running is NOT_EXECUTED, not PASS/FAIL", async () => {
  const result = await runCase({
    id: "meta-broken-case",
    question: "我最喜欢什么颜色？",
    must_not_contain: [],
    must_contain: [],
    records: null, // malformed input: .map throws before any verdict exists
    reason: "meta-test: malformed case cannot execute",
  });
  assert.equal(result.verdict, "NOT_EXECUTED");
  assert.equal(result.pass, null);
  assert.equal(typeof result.error, "string");
  assert.ok(result.error.length > 0);
});

test("NOT_EXECUTED is never folded into 0 in the summary", async () => {
  const pool = await loadPool();
  const run = await runPool(pool);
  const results = run.results;
  const s = summarize(results);
  // Real fixtures: everything executed, everything passed.
  assert.equal(s.total, results.length);
  assert.equal(s.not_executed, 0);
  assert.equal(s.executed, s.total);
  assert.equal(s.passed, s.total);
  assert.equal(s.failed, 0);
  assert.equal(s.pass_rate, 1);
  // Mixed with a not-executed case: it counts in neither numerator nor
  // denominator, and the pass rate stays over executed cases only.
  const mixed = [
    ...results,
    { id: "x", verdict: "NOT_EXECUTED", pass: null },
  ];
  const ms = summarize(mixed);
  assert.equal(ms.total, s.total + 1);
  assert.equal(ms.executed, s.total);
  assert.equal(ms.passed, s.total);
  assert.equal(ms.failed, 0);
  assert.equal(ms.not_executed, 1);
  assert.equal(ms.pass_rate, 1);
  // Nothing executed at all: pass rate is null, never 0/0.
  assert.equal(summarize([{ id: "y", verdict: "NOT_EXECUTED", pass: null }]).pass_rate, null);
});

// ---- Wave-3 batch 1 (governance draft §18.2 / §18.3) ----

test("arms: baseline arm is pinned, rotation arm is the rest, counted apart", async () => {
  const pool = await loadPool();
  const baselineIds = pool.cases.filter((c) => armOf(c) === "baseline").map((c) => c.id);
  assert.deepEqual(baselineIds.sort(),
    ["neg-control-01", "neg-expired-01", "neg-superseded-01"]);
  const run = await runPool(pool);
  for (const r of run.results) {
    const source = pool.cases.find((c) => c.id === r.id);
    assert.equal(r.arm, armOf(source), `${r.id}: result must carry its arm`);
  }
  assert.equal(run.arms.baseline.executed, 3);
  assert.equal(run.arms.baseline.passed, 3);
  assert.equal(run.arms.rotation.executed, pool.cases.length - 3);
  assert.equal(run.arms.rotation.passed, pool.cases.length - 3);
});

test("rotation: round 0 is the identity, later rounds rotate fault and position", async () => {
  const pool = await loadPool();
  const status = pool.cases.find((c) => c.id === "neg-status-01");
  const round0 = materializeCase(status, 0);
  assert.equal(round0.fault, "status");
  assert.deepEqual(round0.records, status.records);
  // fault_rotation[1] for neg-status-01 is "expired": the trap record is
  // rewritten to the expired shape (valid_to in the past, status active).
  const round1 = materializeCase(status, 1);
  assert.equal(round1.fault, "expired");
  assert.equal(round1.records[0].valid_to, "2000-01-01T00:00:00Z");
  assert.equal(round1.records[0].status, undefined);
  // The cycle length is the fault_rotation length: round 4 == round 0 fault.
  assert.equal(materializeCase(status, 4).fault, "status");
  // The declared case is never mutated by materializing a round.
  assert.equal(status.fault, "status");
  assert.equal(status.records[0].status, "superseded");
  // Baseline cases never rotate, whatever the round.
  const baseline = pool.cases.find((c) => c.id === "neg-expired-01");
  assert.deepEqual(materializeCase(baseline, 3), baseline);
  // Perturbation position: a multi-record rotation case rotates its
  // record order by the round.
  const ambiguous = pool.cases.find((c) => c.id === "neg-ambiguous-01");
  const rotated = materializeCase(ambiguous, 1);
  assert.deepEqual(rotated.records.map((r) => r.revision_id),
    [...ambiguous.records.map((r) => r.revision_id)].reverse());
});

test("rotation: a rotated round still blocks every trap", async () => {
  const pool = await loadPool();
  const run = await runPool(pool, { round: 1 });
  const failed = run.results.filter((r) => r.verdict === "FAIL");
  assert.deepEqual(failed, [], "rotated-round failures: " + JSON.stringify(failed));
  const status = run.results.find((r) => r.id === "neg-status-01");
  assert.equal(status.injected_fault, "expired",
    "round 1 materializes neg-status-01 with the expired fault shape");
});

test("reason_code band: every result carries criteria, check id and hit", async () => {
  const pool = await loadPool();
  const run = await runPool(pool);
  for (const r of run.results) {
    assert.ok(Array.isArray(r.reason_code) && r.reason_code.length >= 1,
      `${r.id}: reason_code must be a non-empty array`);
    for (const entry of r.reason_code) {
      assert.equal(entry.criteria_digest, r.criteria_digest);
      assert.ok(entry.check_id.startsWith("negctl."), `${r.id}: check id missing`);
      assert.ok(REASON_CODES.includes(entry.code),
        `${r.id}: code ${entry.code} outside the allow-list`);
      assert.ok("hit" in entry, `${r.id}: band entry must carry the hit cell`);
    }
    assert.deepEqual(validateReasonBand(r.reason_code), []);
  }
  const blocked = run.results.find((r) => r.id === "neg-expired-01");
  assert.equal(blocked.reason_code[0].code, "FAULT_BLOCKED");
  assert.equal(blocked.reason_code[0].hit, null);
});

test("reason_code band: a leak names the leaked string as the hit", async () => {
  const result = await runCase({
    id: "meta-violation",
    question: "我最喜欢什么颜色？",
    must_not_contain: ["枫叶红"],
    must_contain: [],
    records: [{ claim_id: "c-meta", revision_id: "r-meta",
      value: "虚构：最喜欢的颜色是枫叶红", evidence: "虚构：最喜欢的颜色是枫叶红" }],
    reason: "meta-test: the band must name the specific leaked line",
  });
  const leak = result.reason_code.find((e) => e.code === "FAULT_LEAKED");
  assert.ok(leak, "leaked case must carry a FAULT_LEAKED band entry");
  assert.equal(leak.check_id, "negctl.must_not_contain");
  assert.equal(leak.hit, "枫叶红");
});

test("reason_code band: NOT_EXECUTED carries a named code, not only free text", async () => {
  const result = await runCase({
    id: "meta-future-contract",
    question: "q",
    must_not_contain: [],
    must_contain: [],
    records: [],
    contract_version: "99",
    reason: "meta-test: named NOT_EXECUTED reason",
  });
  assert.equal(result.verdict, "NOT_EXECUTED");
  assert.equal(result.reason_code[0].code, "CONTRACT_VERSION_UNSUPPORTED");
  assert.equal(result.reason_code[0].check_id, "negctl.contract_version");
});

test("reason_code band: unknown codes fail closed in validation", () => {
  const forged = [{ criteria_digest: "x".repeat(64),
    check_id: "negctl.must_not_contain", code: "SOMETHING_ELSE", hit: null }];
  const problems = validateReasonBand(forged);
  assert.ok(problems.length >= 1, "a code outside the allow-list must be flagged");
  assert.deepEqual(validateReasonBand("not-an-array").length >= 1, true);
});

test("families: negative failures get their own table and denominator", async () => {
  const pool = await loadPool();
  const run = await runPool(pool);
  const s = summarize(run.results);
  assert.equal(s.families.negative.executed, 8);
  assert.equal(s.families.negative.passed, 8);
  assert.equal(s.families.control.executed, 1);
  assert.equal(s.families.control.passed, 1);
  // A negative failure must not be diluted by the passing control arm:
  // the negative table shows it against the negative denominator alone.
  const mixed = run.results.map((r) =>
    r.id === "neg-expired-01" ? { ...r, verdict: "FAIL", pass: false } : r);
  const ms = summarize(mixed);
  assert.equal(ms.families.negative.failed, 1);
  assert.equal(ms.families.negative.pass_rate, 7 / 8);
  assert.equal(ms.families.control.pass_rate, 1);
});

test("random must-fail inputs: generated per run, seeded, and blocked", async () => {
  const pool = await loadPool();
  const a = await runAll(pool.cases, {
    poolVersion: pool.version, randomMustFail: 2, randomSeed: "seed-a",
  });
  const randomResults = a.results.filter((r) => r.id.startsWith("neg-random-"));
  assert.equal(randomResults.length, 2);
  assert.ok(randomResults.every((r) => r.verdict === "PASS"));
  assert.ok(randomResults.every((r) => r.arm === "rotation"));
  assert.equal(a.families.negative.executed, 10);
  // Same seed, same generated cases: a run is reproducible from its seed.
  const b = await runAll(pool.cases, {
    poolVersion: pool.version, randomMustFail: 2, randomSeed: "seed-a",
  });
  assert.deepEqual(b.results.filter((r) => r.id.startsWith("neg-random-")).map((r) => r.id),
    randomResults.map((r) => r.id));
  // A different seed generates different traps.
  const generated = randomMustFailCase("seed-a", 0);
  assert.equal(generated.fault, "expired");
  assert.notEqual(randomMustFailCase("seed-b", 0).id, generated.id);
  // Default runs generate nothing: the pool alone decides the results.
  const plain = await runPool(pool);
  assert.equal(plain.results.length, pool.cases.length);
});
