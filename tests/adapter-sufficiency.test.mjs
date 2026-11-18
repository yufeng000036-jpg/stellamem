// Evidence-sufficiency signal tests (step 1 of the abstention /
// reading-side pair). Only isolated temp dirs and fictional data.
import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile, readFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import os from "node:os";
import path from "node:path";
import { project, evidenceSufficiency } from "../adapters/openclaw/index.js";

async function fixture() {
  return mkdtemp(path.join(os.tmpdir(), "stellamem-sufficiency-"));
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

const PORSCHE_Q = "Which project did I start first, the Ferrari model or the Porsche 991 Turbo S model?";

test("sufficiency: partial when a proper subset of anchors is uncovered", () => {
  // The stage-2 measured shape: Ferrari covered, Porsche absent.
  const suff = evidenceSufficiency(PORSCHE_Q, ["user is building a ferrari 288 gto model"]);
  assert.equal(suff.status, "partial");
  assert.ok(suff.covered.includes("ferrari"));
  assert.ok(suff.uncovered.includes("porsche"));
});

test("sufficiency: sufficient / insufficient / unknown grades", () => {
  assert.equal(
    evidenceSufficiency("Where did I attend the Imagine Dragons concert?",
      ["i attended the imagine dragons concert"]).status, "sufficient");
  assert.equal(
    evidenceSufficiency("How long have I been working before I started my current job at Google?",
      ["worked at novatech for years"]).status, "insufficient");
  // No capitalized anchors at all: inert, whatever the texts say.
  assert.equal(
    evidenceSufficiency("how many plants did i plant?", ["5 tomato plants"]).status, "unknown");
  assert.deepEqual(evidenceSufficiency("how many plants did i plant?", []).uncovered, []);
});

test("project: partial coverage appends the advisory line, evidence kept", async () => {
  const root = await fixture();
  await writeRecords(root, "semantic/facts.md", [
    record({ claim_id: "c-ferrari", revision_id: "r-ferrari", scope: "models",
      predicate: "is building", value: "Ferrari 288 GTO model",
      evidence: "I am building a Ferrari 288 GTO model kit" }),
  ]);
  const out = await project(root, PORSCHE_Q,
    { requesterSubject: "user", activeScopes: ["models"], maxInjectChars: 16000 });
  assert.ok(out.includes("c-ferrari"), "covered evidence must stay: " + out);
  assert.ok(out.includes("stellamem:evidence-sufficiency"), "advisory line expected: " + out);
  assert.ok(out.includes("porsche"), "advisory must name the uncovered anchor: " + out);
  assert.ok(!out.includes("stellamem:insufficient-evidence"),
    "gate must not fire on partial coverage: " + out);
  // The advisory is one JSON line after the evidence lines: the
  // benchmark worker's per-line JSON parse keeps working.
  const lines = out.split("\n").filter((l) => l.startsWith("{"));
  assert.equal(JSON.parse(lines[lines.length - 1]).id, "stellamem:evidence-sufficiency");
});

test("project: full coverage appends no advisory line", async () => {
  const root = await fixture();
  await writeRecords(root, "semantic/facts.md", [
    record({ claim_id: "c-concert", revision_id: "r-concert", scope: "concerts",
      predicate: "attended", value: "Imagine Dragons at Xfinity Center",
      evidence: "I attended the Imagine Dragons concert at Xfinity Center" }),
  ]);
  const out = await project(root, "Where did I attend the Imagine Dragons concert?",
    { requesterSubject: "user", activeScopes: ["concerts"], maxInjectChars: 16000 });
  assert.ok(out.includes("c-concert"));
  assert.ok(!out.includes("evidence-sufficiency"), "no sufficiency line expected: " + out);
});

test("project: zero coverage keeps the withholding gate, sidecar grades it", async () => {
  const root = await fixture();
  const diagDir = await fixture();
  await writeRecords(root, "semantic/facts.md", [
    record({ claim_id: "c-years", revision_id: "r-years", scope: "career",
      predicate: "has worked professionally for", value: "9 years",
      evidence: "I've been working professionally for 9 years at NovaTech" }),
  ]);
  const prompt = "How long have I been working before I started my current job at Google?";
  process.env.STELLAMEM_PROJECT_DIAGNOSTICS_DIR = diagDir;
  try {
    const out = await project(root, prompt,
      { requesterSubject: "user", activeScopes: ["career"], maxInjectChars: 16000 });
    assert.ok(out.includes("stellamem:insufficient-evidence"), "gate must fire: " + out);
    assert.ok(!out.includes("c-years"), "near-miss line must be withheld: " + out);
    const name = createHash("sha256").update(prompt).digest("hex").slice(0, 16) + ".json";
    const sidecar = JSON.parse(await readFile(path.join(diagDir, name), "utf8"));
    assert.equal(sidecar.evidence_sufficiency.status, "insufficient");
  } finally {
    delete process.env.STELLAMEM_PROJECT_DIAGNOSTICS_DIR;
  }
});

test("project: diagnostics sidecar carries the partial grade", async () => {
  const root = await fixture();
  const diagDir = await fixture();
  await writeRecords(root, "semantic/facts.md", [
    record({ claim_id: "c-ferrari", revision_id: "r-ferrari", scope: "models",
      predicate: "is building", value: "Ferrari 288 GTO model",
      evidence: "I am building a Ferrari 288 GTO model kit" }),
  ]);
  process.env.STELLAMEM_PROJECT_DIAGNOSTICS_DIR = diagDir;
  try {
    await project(root, PORSCHE_Q,
      { requesterSubject: "user", activeScopes: ["models"], maxInjectChars: 16000 });
    const name = createHash("sha256").update(PORSCHE_Q).digest("hex").slice(0, 16) + ".json";
    const sidecar = JSON.parse(await readFile(path.join(diagDir, name), "utf8"));
    assert.equal(sidecar.evidence_sufficiency.status, "partial");
    assert.ok(sidecar.evidence_sufficiency.uncovered.includes("porsche"));
  } finally {
    delete process.env.STELLAMEM_PROJECT_DIAGNOSTICS_DIR;
  }
});
