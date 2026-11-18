// Reading-side contract: the diagnostics sidecar exposes the projected
// claims as structured evidence ({claim_id, ..., note: ""}) plus the
// reading scaffold, and the OpenClaw host hook appends the scaffold as
// one system line for the live reader — while project()'s own string
// stays free of it (its per-line contract belongs to other callers).
import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile, readFile, readdir } from "node:fs/promises";
import { createHash } from "node:crypto";
import os from "node:os";
import path from "node:path";
import adapter, { project, READING_SCAFFOLD } from "../adapters/openclaw/index.js";

async function fixture() {
  return mkdtemp(path.join(os.tmpdir(), "stellamem-reading-"));
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
const CONFIG = { requesterSubject: "user", activeScopes: ["models"], maxInjectChars: 16000 };
const KIT_Q = "What model kit is the user building?";

async function withDiag(fn) {
  const diagDir = await mkdtemp(path.join(os.tmpdir(), "stellamem-reading-diag-"));
  const prev = process.env.STELLAMEM_PROJECT_DIAGNOSTICS_DIR;
  process.env.STELLAMEM_PROJECT_DIAGNOSTICS_DIR = diagDir;
  try {
    return await fn(diagDir);
  } finally {
    if (prev === undefined) delete process.env.STELLAMEM_PROJECT_DIAGNOSTICS_DIR;
    else process.env.STELLAMEM_PROJECT_DIAGNOSTICS_DIR = prev;
  }
}
async function sidecar(diagDir, prompt) {
  const name = createHash("sha256").update(prompt).digest("hex").slice(0, 16) + ".json";
  return JSON.parse(await readFile(path.join(diagDir, name), "utf8"));
}

test("sidecar carries structured evidence with note slots and the scaffold", async () => {
  await withDiag(async (diagDir) => {
    const root = await fixture();
    await writeRecords(root, "semantic/facts.md", [
      record({ claim_id: "c-ferrari", revision_id: "r-ferrari", scope: "models",
        predicate: "is building", value: "Ferrari 288 GTO model",
        evidence: "I am building a Ferrari 288 GTO model kit" }),
    ]);
    const out = await project(root, KIT_Q, CONFIG);
    assert.ok(out.includes("c-ferrari"), "projection contains the claim: " + out);
    assert.ok(!out.includes("reading_scaffold"), "project() string itself carries no scaffold line");
    const sc = await sidecar(diagDir, KIT_Q);
    assert.equal(sc.reading_scaffold, READING_SCAFFOLD);
    assert.equal(sc.evidence.length, 1);
    assert.equal(sc.evidence[0].claim_id, "c-ferrari");
    assert.equal(sc.evidence[0].value, "Ferrari 288 GTO model");
    assert.equal(sc.evidence[0].note, "");
  });
});

test("host hook appends the scaffold line after a normal projection", async () => {
  const root = await fixture();
  await writeRecords(root, "semantic/facts.md", [
    record({ claim_id: "c-ferrari", revision_id: "r-ferrari", scope: "models",
      predicate: "is building", value: "Ferrari 288 GTO model",
      evidence: "I am building a Ferrari 288 GTO model kit" }),
  ]);
  let handler = null;
  const api = {
    pluginConfig: { coreDir: root, ...CONFIG },
    on(event, fn) { if (event === "before_prompt_build") handler = fn; },
  };
  adapter.register(api);
  assert.ok(handler, "hook registered");
  const res = await handler({ prompt: KIT_Q });
  assert.ok(res.prependContext, "hook returns a projection: " + JSON.stringify(res));
  const lines = res.prependContext.trimEnd().split("\n");
  const last = JSON.parse(lines[lines.length - 1]);
  assert.equal(last.id, "stellamem:reading-scaffold");
  assert.equal(last.value, READING_SCAFFOLD);
});

test("host hook does not append the scaffold when the anchor gate withholds", async () => {
  const root = await fixture();
  await writeRecords(root, "semantic/facts.md", [
    record({ claim_id: "c-years", revision_id: "r-years", scope: "career",
      predicate: "has worked professionally for", value: "9 years",
      evidence: "I've been working professionally for 9 years at NovaTech" }),
  ]);
  let handler = null;
  const api = {
    pluginConfig: { coreDir: root, requesterSubject: "user", activeScopes: ["career"], maxInjectChars: 16000 },
    on(event, fn) { if (event === "before_prompt_build") handler = fn; },
  };
  adapter.register(api);
  const res = await handler({ prompt: "How long have I been working before I started my current job at Google?" });
  assert.ok(res.prependContext.includes("stellamem:insufficient-evidence"), "gate fired: " + res.prependContext);
  assert.ok(!res.prependContext.includes("reading-scaffold"), "no scaffold on a gated projection");
});
