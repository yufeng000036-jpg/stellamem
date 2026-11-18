// Date-key matching tests (lever B). Only isolated temp dirs and
// fictional data; no production memory.
import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { project } from "../adapters/openclaw/index.js";

async function fixture() {
  return mkdtemp(path.join(os.tmpdir(), "stellamem-date-keys-"));
}
function record(updates = {}) {
  return { schema_version: 2, claim_id: "c-test", revision_id: "r-test", status: "active",
    kind: "fact", subject: "user", scope: "global", predicate: "p",
    value: "fictional", evidence: "fictional", evidence_verified: true, source: "user",
    sensitivity: "PRIVATE", write_policy: "allow", injection_policy: "recall", ...updates };
}
async function writeRecords(root, rel, records) {
  const file = path.join(root, rel);
  await mkdir(path.dirname(file), { recursive: true });
  await writeFile(file, "<!-- stellamem:v2 -->\n" +
    records.map((c) => "```stellamem\n" + JSON.stringify(c) + "\n```\n").join("\n"));
}

// The claim's own words share nothing with the questions below; only a
// derived date key can connect them. Source message: Wed 2023-03-15,
// "last week" = the ISO week of 2023-03-06..2023-03-12.
const HIKE = record({
  claim_id: "c-hike", revision_id: "r-hike", predicate: "went",
  value: "went for a walk in the hills",
  evidence: "I went hiking last week.",
  date_keys: ["2023-03-06", "2023-03-07", "2023-03-08", "2023-03-09",
    "2023-03-10", "2023-03-11", "2023-03-12"],
  date_keys_derivation: "relative-time-v1",
});

test("date key: a day inside the resolved week projects the claim", async () => {
  const root = await fixture();
  await writeRecords(root, "semantic/facts.md", [HIKE]);
  const out = await project(root, "What did I do on March 8, 2023?");
  assert.ok(out.includes("c-hike"), out);
});

test("date key: without date_keys the same claim stays unprojected", async () => {
  const root = await fixture();
  const bare = { ...HIKE };
  delete bare.date_keys;
  delete bare.date_keys_derivation;
  await writeRecords(root, "semantic/facts.md", [bare]);
  const out = await project(root, "What did I do on March 8, 2023?");
  assert.equal(out, "");
});

test("date key: a day outside the resolved week does not project", async () => {
  const root = await fixture();
  await writeRecords(root, "semantic/facts.md", [HIKE]);
  const out = await project(root, "What did I do on April 8, 2023?");
  assert.equal(out, "");
});

test("date key: month keys need month and year", async () => {
  const root = await fixture();
  const claim = record({
    claim_id: "c-sub", revision_id: "r-sub", predicate: "began",
    value: "began a trial of the streaming service",
    evidence: "I began the trial last month.",
    date_keys: ["2023-02"], date_keys_derivation: "relative-time-v1",
  });
  await writeRecords(root, "semantic/facts.md", [claim]);
  const hit = await project(root, "What did I start in February 2023?");
  assert.ok(hit.includes("c-sub"), hit);
  const miss = await project(root, "What did I start in February 2024?");
  assert.equal(miss, "");
});

test("date key: Chinese question dates match too", async () => {
  const root = await fixture();
  await writeRecords(root, "semantic/facts.md", [HIKE]);
  const out = await project(root, "我在2023年3月8日做了什么？");
  assert.ok(out.includes("c-hike"), out);
});
