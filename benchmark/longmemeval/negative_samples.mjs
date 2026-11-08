// Negative-sample eval, first version (Hermes 4.3): an expired or
// superseded fact that still gets injected is a FAIL. Retrieval quality
// alone cannot catch this — the trap questions match the old fact
// word-for-word, so a retrieval-only check WOULD find it. The eval proves
// the projection gate blocks it anyway.
//
// Run: node benchmark/longmemeval/negative_samples.mjs
// Exit 0 = every case passes; exit 1 = at least one FAIL;
// exit 2 = at least one case could not be executed (no FAIL).
// Fixtures: negative_samples.json (fictional data only, no private memory).
// The same runner is imported by tests/negative-samples.test.mjs.
//
// Verdicts — exactly three, no fourth status:
//   PASS: the case ran and the gate behaved as required.
//   FAIL: the case ran and the gate misbehaved (leaked / missing).
//   NOT_EXECUTED: the case did not run at all — e.g. it declares a
//     contract_version this runner does not implement (the contract wants
//     a subcommand/capability the implementation version has not landed
//     yet), or the run itself crashed before a verdict could be produced.
//     A NOT_EXECUTED case is never folded into 0: it counts neither as a
//     pass nor as a fail, and the pass rate is computed over executed
//     cases only (null when nothing executed).
// Every result carries both versions: contract_version (what the case
// declares it needs; defaults to this runner's CONTRACT_VERSION) and
// actual_version (the version of the implementation that attempted it).
import { mkdtemp, mkdir, writeFile, readFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { project } from "../../adapters/openclaw/index.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIXTURES = path.join(HERE, "negative_samples.json");

// Version of the negative-sample contract this runner implements.
// A case declaring a different contract_version cannot be executed
// as specified and is recorded NOT_EXECUTED (see runCase).
export const CONTRACT_VERSION = "1";
export const VERDICTS = ["PASS", "FAIL", "NOT_EXECUTED"];

export function defaultRecord(overrides = {}) {
  return {
    schema_version: 2, claim_id: "c-neg", revision_id: "r-neg", status: "active",
    kind: "fact", subject: "user", scope: "global", predicate: "p",
    value: "fictional", evidence: "fictional", evidence_verified: true, source: "user",
    sensitivity: "PRIVATE", write_policy: "allow", injection_policy: "recall",
    ...overrides,
  };
}

export async function runCase(c, opts = {}) {
  const actual_version = opts.actual_version ?? CONTRACT_VERSION;
  const contract_version = c.contract_version ?? CONTRACT_VERSION;
  const notExecuted = (reason, error) => ({
    id: c.id, verdict: "NOT_EXECUTED", pass: null,
    leaked: [], missing: [], reason: c.reason,
    contract_version, actual_version, error: error ?? reason,
  });
  if (c.contract_version !== undefined && c.contract_version !== CONTRACT_VERSION) {
    return notExecuted(
      `contract version ${c.contract_version} not implemented by runner version ${actual_version}`,
    );
  }
  try {
    const root = await mkdtemp(path.join(os.tmpdir(), "stellamem-neg-"));
    const file = path.join(root, "semantic/facts.md");
    await mkdir(path.dirname(file), { recursive: true });
    const records = c.records.map((r) => defaultRecord(r));
    await writeFile(file, "<!-- stellamem:v2 -->\n" +
      records.map((x) => "```stellamem\n" + JSON.stringify(x) + "\n```\n").join("\n"));
    const out = await project(root, c.question);
    const leaked = (c.must_not_contain || []).filter((s) => out.includes(s));
    const missing = (c.must_contain || []).filter((s) => !out.includes(s));
    const pass = leaked.length === 0 && missing.length === 0;
    return {
      id: c.id, verdict: pass ? "PASS" : "FAIL", pass,
      leaked, missing, reason: c.reason, contract_version, actual_version,
    };
  } catch (error) {
    return notExecuted("run failed before a verdict could be produced",
      `${error?.name ?? "Error"}: ${error?.message ?? String(error)}`);
  }
}

export async function runAll(cases, opts = {}) {
  const results = [];
  for (const c of cases) results.push(await runCase(c, opts));
  return results;
}

// Summary math: NOT_EXECUTED is never folded into 0 — it counts neither
// as a pass nor as a fail, and pass_rate is over executed cases only
// (null when nothing executed, never 0/0).
export function summarize(results) {
  const executed = results.filter((r) => r.verdict !== "NOT_EXECUTED");
  const passed = results.filter((r) => r.verdict === "PASS").length;
  const failed = results.filter((r) => r.verdict === "FAIL").length;
  const not_executed = results.filter((r) => r.verdict === "NOT_EXECUTED").length;
  return {
    total: results.length,
    executed: executed.length,
    passed,
    failed,
    not_executed,
    pass_rate: executed.length ? passed / executed.length : null,
  };
}

async function main() {
  const cases = JSON.parse(await readFile(FIXTURES, "utf8"));
  const results = await runAll(cases);
  for (const r of results) {
    if (r.verdict === "NOT_EXECUTED") {
      console.log(`NOT_EXECUTED ${r.id} contract=${r.contract_version} actual=${r.actual_version} ${r.error}`);
    } else {
      const detail = r.pass ? "" :
        ` leaked=[${r.leaked.join(", ")}] missing=[${r.missing.join(", ")}]`;
      console.log(`${r.verdict} ${r.id}${detail}`);
    }
  }
  const s = summarize(results);
  if (s.executed === 0) {
    console.log(`negative samples: no cases executed (${s.not_executed} not executed)`);
  } else {
    const extra = s.not_executed ? ` (${s.not_executed} not executed)` : "";
    console.log(`negative samples: ${s.passed}/${s.executed} pass${extra}`);
  }
  if (s.failed) process.exitCode = 1;
  else if (s.not_executed) process.exitCode = 2;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await main();
