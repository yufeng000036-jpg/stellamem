// Evaluator sanity: a known pending-memory leak must turn the contract red.
import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, writeFile, cp, rm } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import os from "node:os";
import path from "node:path";

const root = path.resolve(".");
const evaluator = path.join(root, "benchmark/reliability/evaluate_h1.mjs");

test("versioned H1 contract passes main and detects pending-memory leak", async () => {
  const run = adapter => spawnSync(process.execPath, [evaluator, adapter], { encoding: "utf8", maxBuffer: 4_000_000 });
  const baseline = run(path.join(root, "adapters/openclaw/index.js"));
  assert.equal(baseline.status, 0, baseline.stderr);
  const good = JSON.parse(baseline.stdout);
  assert.equal(good.contract_version, "h1-v2");
  assert.equal(good.overall.cases, 320);
  assert.equal(good.overall.passed, 320);
  const tmp = await mkdtemp(path.join(os.tmpdir(), "stellamem-h1-mutant-"));
  try {
    await mkdir(path.join(tmp, "adapters/openclaw"), { recursive: true });
    await cp(path.join(root, "shared"), path.join(tmp, "shared"), { recursive: true });
    const original = await readFile(path.join(root, "adapters/openclaw/index.js"), "utf8");
    const guard = 'if (c.status !== "active") return "ineligible:status";';
    assert.ok(original.includes(guard), "mutation must target the live status gate");
    const mutant = original.replace(guard, 'if (!["active", "pending"].includes(c.status)) return "ineligible:status";');
    const target = path.join(tmp, "adapters/openclaw/index.mjs");
    await writeFile(target, mutant);
    const bad = run(target);
    assert.equal(bad.status, 1, bad.stderr);
    const failures = JSON.parse(bad.stdout).results.filter(item => !item.passed);
    assert.equal(failures.filter(item => item.id.startsWith("H1-pending-")).length, 20);
  } finally {
    await rm(tmp, { recursive: true, force: true });
  }
});
