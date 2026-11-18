// Version-superseding mark tests (lever A). Only isolated temp dirs
// and fictional data; no production memory.
import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { project } from "../adapters/openclaw/index.js";

async function fixture() {
  return mkdtemp(path.join(os.tmpdir(), "stellamem-version-marks-"));
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
function lines(out) {
  return out.split("\n").filter((l) => l.startsWith("{")).map((l) => JSON.parse(l));
}

const OLD = record({
  claim_id: "c-old", revision_id: "r-old", predicate: "works at",
  value: "NovaTech", evidence: "I work at NovaTech.",
  provenance: { timestamp: "2023-01-10T10:00:00+00:00", session_id: "s1", message_id: "s1:0" },
});
const NEW = record({
  claim_id: "c-new", revision_id: "r-new", predicate: "works at",
  value: "Google", evidence: "I now work at Google.",
  provenance: { timestamp: "2023-06-10T10:00:00+00:00", session_id: "s2", message_id: "s2:0" },
});

test("marks: older value stays projected and is marked superseded", async () => {
  const root = await fixture();
  await writeRecords(root, "semantic/facts.md", [OLD, NEW]);
  const out = await project(root, "Where do I work now, at Google or elsewhere?");
  const ls = lines(out);
  assert.equal(ls.length, 2);
  const old = ls.find((l) => l.id === "c-old");
  const cur = ls.find((l) => l.id === "c-new");
  assert.equal(old.superseded_by, "c-new");
  assert.deepEqual(cur.supersedes, ["c-old"]);
  assert.equal(cur.superseded_by, undefined);
});

test("marks: equal timestamps are not orderable, no marks", async () => {
  const root = await fixture();
  const same = { ...NEW, claim_id: "c-new2", revision_id: "r-new2",
    provenance: { timestamp: "2023-01-10T10:00:00+00:00", session_id: "s2", message_id: "s2:0" } };
  await writeRecords(root, "semantic/facts.md", [OLD, same]);
  const out = await project(root, "Where do I work now, at Google or elsewhere?");
  for (const l of lines(out)) {
    assert.equal(l.superseded_by, undefined);
    assert.equal(l.supersedes, undefined);
  }
});

test("marks: same normalized value is a restatement, not a version", async () => {
  const root = await fixture();
  const restated = { ...NEW, value: "novatech" };
  await writeRecords(root, "semantic/facts.md", [OLD, restated]);
  const out = await project(root, "Where do I work, at NovaTech?");
  for (const l of lines(out)) {
    assert.equal(l.superseded_by, undefined);
    assert.equal(l.supersedes, undefined);
  }
});

test("marks: different predicate is a different attribute, no marks", async () => {
  const root = await fixture();
  const other = { ...NEW, predicate: "visits" };
  await writeRecords(root, "semantic/facts.md", [OLD, other]);
  const out = await project(root, "Where do I work now, at Google or elsewhere?");
  for (const l of lines(out)) {
    assert.equal(l.superseded_by, undefined);
    assert.equal(l.supersedes, undefined);
  }
});

test("marks: ambiguous newest (two values tied latest) leaves group unmarked", async () => {
  const root = await fixture();
  const rival = record({
    claim_id: "c-rival", revision_id: "r-rival", predicate: "works at",
    value: "Meta", evidence: "I now work at Meta.",
    provenance: { timestamp: "2023-06-10T10:00:00+00:00", session_id: "s3", message_id: "s3:0" },
  });
  await writeRecords(root, "semantic/facts.md", [OLD, NEW, rival]);
  const out = await project(root, "Where do I work now, at Google or Meta?");
  for (const l of lines(out)) {
    assert.equal(l.superseded_by, undefined);
    assert.equal(l.supersedes, undefined);
  }
});

test("marks: when the newest is dropped as revision-ambiguous, no dangling mark", async () => {
  const root = await fixture();
  const newRev2 = { ...NEW, revision_id: "r-new-b" };
  await writeRecords(root, "semantic/facts.md", [OLD, NEW, newRev2]);
  const out = await project(root, "Where do I work now?");
  const ls = lines(out);
  // Both revisions of c-new are ambiguous and dropped (pre-existing
  // rule); the older claim still projects, unmarked.
  assert.deepEqual(ls.map((l) => l.id), ["c-old"]);
  assert.equal(ls[0].superseded_by, undefined);
});

test("marks: revision ambiguity inside the older claim is unchanged", async () => {
  const root = await fixture();
  const oldRev2 = { ...OLD, revision_id: "r-old-b" };
  await writeRecords(root, "semantic/facts.md", [OLD, oldRev2, NEW]);
  const out = await project(root, "Where do I work now?");
  const ls = lines(out);
  assert.deepEqual(ls.map((l) => l.id), ["c-new"]);
  assert.equal(ls[0].supersedes, undefined);
});
