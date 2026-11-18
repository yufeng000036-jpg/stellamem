// Evidence-sufficiency anchor gate regression tests (WS-E).
// Only isolated temp dirs and fictional data; no production memory.
import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { project, questionAnchors, uncoveredAnchors } from "../adapters/openclaw/index.js";

async function fixture() {
  return mkdtemp(path.join(os.tmpdir(), "stellamem-anchor-gate-"));
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

test("anchors: capitalized non-sentence-initial phrases, stopwords excluded", () => {
  assert.deepEqual(
    questionAnchors("How long have I been working before I started my current job at Google?"),
    ["google"]);
  assert.deepEqual(
    questionAnchors("Where did I attend the Imagine Dragons concert in March?"),
    ["imagine dragons"]);
  assert.deepEqual(questionAnchors("我的绘本项目叫什么名字？"), []);
  assert.deepEqual(
    uncoveredAnchors("job at Google?", ["worked at novatech for years"]),
    ["google"]);
  assert.deepEqual(
    uncoveredAnchors("job at Google?", ["worked at google for years"]), []);
});

test("near-miss projection is replaced by the insufficiency line", async () => {
  // The measured failure shape: a duration claim about a different
  // employer is projected for a question anchored on an absent entity.
  const root = await fixture();
  await writeRecords(root, "semantic/facts.md", [
    record({ claim_id: "c-years", revision_id: "r-years", scope: "career",
      predicate: "has worked professionally for", value: "9 years",
      evidence: "I've been working professionally for 9 years at NovaTech" }),
  ]);
  const out = await project(root,
    "How long have I been working before I started my current job at Google?",
    { requesterSubject: "user", activeScopes: ["career"], maxInjectChars: 16000 });
  assert.ok(out.includes("stellamem:insufficient-evidence"), "gate must fire: " + out);
  assert.ok(!out.includes("c-years"), "near-miss line must be withheld: " + out);
});

test("anchor covered by a projected claim: projection passes through", async () => {
  const root = await fixture();
  await writeRecords(root, "semantic/facts.md", [
    record({ claim_id: "c-concert", revision_id: "r-concert", scope: "concerts",
      predicate: "attended", value: "Imagine Dragons at Xfinity Center",
      evidence: "I attended the Imagine Dragons concert at Xfinity Center" }),
  ]);
  const out = await project(root, "Where did I attend the Imagine Dragons concert?",
    { requesterSubject: "user", activeScopes: ["concerts"], maxInjectChars: 16000 });
  assert.ok(out.includes("c-concert"), "covered claim must project: " + out);
  assert.ok(!out.includes("insufficient-evidence"), "gate must stay inert: " + out);
});

test("anchor covered only in the claim's verbatim evidence: no gate", async () => {
  // The answering claim's paraphrased value need not repeat the name;
  // its evidence is the source message and carries it.
  const root = await fixture();
  await writeRecords(root, "semantic/facts.md", [
    record({ claim_id: "c-shop", revision_id: "r-shop", scope: "work",
      predicate: "works at", value: "a coffee shop downtown",
      evidence: "Sophia works at a coffee shop downtown" }),
  ]);
  const out = await project(root, "Where does Sophia work?",
    { requesterSubject: "user", activeScopes: ["work"], maxInjectChars: 16000 });
  assert.ok(out.includes("c-shop"), "evidence-covered claim must project: " + out);
  assert.ok(!out.includes("insufficient-evidence"), "gate must stay inert: " + out);
});

test("no Latin anchors in the prompt: gate is inert", async () => {
  const root = await fixture();
  await writeRecords(root, "semantic/profile.md", [
    record({ claim_id: "c-title", revision_id: "r-title", predicate: "has title",
      value: "《星球邮差》", evidence: "《星球邮差》" }),
  ]);
  const out = await project(root, "我的绘本项目叫什么名字？");
  assert.ok(out.includes("《星球邮差》"), "claim must project unchanged: " + out);
  assert.ok(!out.includes("insufficient-evidence"), "gate must stay inert: " + out);
});
