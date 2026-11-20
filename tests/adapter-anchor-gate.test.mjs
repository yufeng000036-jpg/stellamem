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

test("anchors: edge punctuation is stripped before checks and matching", () => {
  // Measured false fire (fixed-set positive control 0a34ad58): the
  // sentence-final name arrives as "Tokyo." and never matched the claim
  // text's "tokyo", so a fully covered projection was withheld.
  assert.deepEqual(
    questionAnchors("I’m a bit anxious about getting around Tokyo. Do you have any helpful tips?"),
    ["tokyo"]);
  // Multi-word anchor whose last word carries the sentence period.
  assert.deepEqual(
    questionAnchors("Tell me about my stay at the Park Hyatt Tokyo."),
    ["park hyatt tokyo"]);
  // The stopword check sees the stripped form: "May." is the month.
  assert.deepEqual(questionAnchors("The concert was in May."), []);
  // A closing quote rides along in the token and is stripped.
  assert.deepEqual(
    questionAnchors("I visited the 'Google' campus last year"), ["google"]);
  // Internal punctuation is not touched.
  assert.deepEqual(
    questionAnchors("I flew to the U.S. last spring"), ["u.s"]);
  // A punctuation-carrying anchor covered by claim text: no gate.
  assert.deepEqual(
    uncoveredAnchors("getting around Tokyo.", ["will stay at park hyatt tokyo"]), []);
  // True zero coverage still fires, with the normalized anchor reported.
  assert.deepEqual(
    uncoveredAnchors("getting around Tokyo.", ["i stayed in osaka last year"]),
    ["tokyo"]);
});

test("punctuation-carrying anchor covered end to end: projection passes", async () => {
  // The 0a34ad58 shape through project(): the answering claim names
  // Tokyo without a trailing period; the question carries "Tokyo.".
  const root = await fixture();
  await writeRecords(root, "semantic/facts.md", [
    record({ claim_id: "c-hotel", revision_id: "r-hotel", scope: "travel",
      predicate: "will stay at", value: "Park Hyatt Tokyo",
      evidence: "the Park Hyatt Tokyo, where I'll be staying" }),
  ]);
  const out = await project(root,
    "I’m a bit anxious about getting around Tokyo. Do you have any helpful tips?",
    { requesterSubject: "user", activeScopes: ["travel"], maxInjectChars: 16000 });
  assert.ok(out.includes("c-hotel"), "covered claim must project: " + out);
  assert.ok(!out.includes("insufficient-evidence"), "gate must stay inert: " + out);
});

test("punctuation-carrying anchor with zero coverage: gate still fires", async () => {
  // Normalization must not loosen the gate: a near-miss line about
  // getting around (no Tokyo anywhere) is still withheld.
  const root = await fixture();
  await writeRecords(root, "semantic/facts.md", [
    record({ claim_id: "c-card", revision_id: "r-card", scope: "travel",
      predicate: "holds transit card", value: "a Suica card for getting around",
      evidence: "I just got a Suica card for getting around the city" }),
  ]);
  const out = await project(root,
    "I’m a bit anxious about getting around Tokyo. Do you have any helpful tips?",
    { requesterSubject: "user", activeScopes: ["travel"], maxInjectChars: 16000 });
  assert.ok(out.includes("stellamem:insufficient-evidence"), "gate must fire: " + out);
  assert.ok(!out.includes("c-card"), "near-miss line must be withheld: " + out);
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

test("anchors: pronoun contractions are never anchors", () => {
  assert.deepEqual(
    questionAnchors("I've been thinking about a get-together for my friends. I'm not sure what to serve. Any suggestions for a signature cocktail?"),
    []);
  assert.deepEqual(
    questionAnchors("I'm planning a trip to Tokyo next spring."),
    ["tokyo"]);
});

test("coverage: possessive anchor covered by the bare name", () => {
  assert.deepEqual(
    uncoveredAnchors("What is the total cost of Lola's vet visit?",
      ["user paid for lola's flea medication at the vet"]),
    []);
  assert.deepEqual(
    uncoveredAnchors("What is the total cost of Lola's vet visit?",
      ["user paid for the flea medication at the vet"]),
    ["lola's"]);
});

test("coverage: multi-word anchor covered by its words in any order in one text", () => {
  assert.deepEqual(
    uncoveredAnchors("What size is the Samsung TV I bought?",
      ["user bought a samsung 55-inch 4k smart tv last year"]),
    []);
  // A genuinely absent anchor still fires: "yorker" appears in no text.
  assert.deepEqual(
    uncoveredAnchors("What did The New Yorker say about it?",
      ["user read the new york times every morning"]),
    ["the new yorker"]);
});

test("possessive anchor: claims naming the bare entity project end to end", async () => {
  const root = await fixture();
  await writeRecords(root, "semantic/facts.md", [
    record({ claim_id: "c-vet", revision_id: "r-vet", scope: "pets",
      predicate: "paid for vet visit", value: "$120 for Lola's checkup",
      evidence: "The vet visit for Lola cost $120 in total" }),
  ]);
  const out = await project(root, "What is the total cost of Lola's vet visit?",
    { requesterSubject: "user", activeScopes: ["pets"], maxInjectChars: 16000 });
  assert.ok(out.includes("c-vet"), "bare-name claim must project: " + out);
  assert.ok(!out.includes("insufficient-evidence"), "gate must stay inert: " + out);
});
