// Negative-sample eval, first version (Hermes 4.3): an expired or
// superseded fact that still gets injected is a FAIL. The same cases are
// runnable standalone via benchmark/longmemeval/negative_samples.mjs.
import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { runAll, runCase, summarize, CONTRACT_VERSION } from "../benchmark/longmemeval/negative_samples.mjs";

const FIXTURES = path.join(path.dirname(fileURLToPath(import.meta.url)),
  "..", "benchmark/longmemeval/negative_samples.json");

test("negative samples: no expired or superseded fact is injected", async () => {
  const cases = JSON.parse(await readFile(FIXTURES, "utf8"));
  assert.ok(cases.length >= 7, "first version must have at least 7 cases");
  const results = await runAll(cases);
  const failed = results.filter((r) => !r.pass);
  assert.deepEqual(failed, [],
    "negative-sample failures: " + JSON.stringify(failed));
});

test("the runner detects a violation instead of always passing", async () => {
  // An active fact that matches the question MUST inject; if the runner
  // is pointed at it as forbidden, it must report FAIL. This proves the
  // eval is not trivially green.
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
  const cases = JSON.parse(await readFile(FIXTURES, "utf8"));
  const results = await runAll(cases);
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
