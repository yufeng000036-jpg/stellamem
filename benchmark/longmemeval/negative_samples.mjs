// Negative-sample eval, first version (Hermes 4.3): an expired or
// superseded fact that still gets injected is a FAIL. Retrieval quality
// alone cannot catch this — the trap questions match the old fact
// word-for-word, so a retrieval-only check WOULD find it. The eval proves
// the projection gate blocks it anyway.
//
// Run: node benchmark/longmemeval/negative_samples.mjs
// Exit 0 = every case passes; exit 1 = at least one FAIL.
// Fixtures: negative_samples.json (fictional data only, no private memory).
// The same runner is imported by tests/negative-samples.test.mjs.
import { mkdtemp, mkdir, writeFile, readFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { project } from "../../adapters/openclaw/index.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIXTURES = path.join(HERE, "negative_samples.json");

export function defaultRecord(overrides = {}) {
  return {
    schema_version: 2, claim_id: "c-neg", revision_id: "r-neg", status: "active",
    kind: "fact", subject: "user", scope: "global", predicate: "p",
    value: "fictional", evidence: "fictional", evidence_verified: true, source: "user",
    sensitivity: "PRIVATE", write_policy: "allow", injection_policy: "recall",
    ...overrides,
  };
}

export async function runCase(c) {
  const root = await mkdtemp(path.join(os.tmpdir(), "stellamem-neg-"));
  const file = path.join(root, "semantic/facts.md");
  await mkdir(path.dirname(file), { recursive: true });
  const records = c.records.map((r) => defaultRecord(r));
  await writeFile(file, "<!-- stellamem:v2 -->\n" +
    records.map((x) => "```stellamem\n" + JSON.stringify(x) + "\n```\n").join("\n"));
  const out = await project(root, c.question);
  const leaked = (c.must_not_contain || []).filter((s) => out.includes(s));
  const missing = (c.must_contain || []).filter((s) => !out.includes(s));
  return { id: c.id, pass: leaked.length === 0 && missing.length === 0,
    leaked, missing, reason: c.reason };
}

export async function runAll(cases) {
  const results = [];
  for (const c of cases) results.push(await runCase(c));
  return results;
}

async function main() {
  const cases = JSON.parse(await readFile(FIXTURES, "utf8"));
  const results = await runAll(cases);
  for (const r of results) {
    const detail = r.pass ? "" :
      ` leaked=[${r.leaked.join(", ")}] missing=[${r.missing.join(", ")}]`;
    console.log(`${r.pass ? "PASS" : "FAIL"} ${r.id}${detail}`);
  }
  const failed = results.filter((r) => !r.pass);
  console.log(`negative samples: ${results.length - failed.length}/${results.length} pass`);
  if (failed.length) process.exitCode = 1;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await main();
