// Negative-sample eval, first version (Hermes 4.3): an expired or
// superseded fact that still gets injected is a FAIL. The same cases are
// runnable standalone via benchmark/longmemeval/negative_samples.mjs.
import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { runAll, runCase } from "../benchmark/longmemeval/negative_samples.mjs";

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
  assert.deepEqual(result.leaked, ["枫叶红"]);
});
