// Optional real-host smoke test. Set STELLAMEM_OPENCLAW_ENTRY to an installed
// OpenClaw's openclaw.mjs. The default suite does not download or start a host.
import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const entry = process.env.STELLAMEM_OPENCLAW_ENTRY;
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("isolated OpenClaw loads the adapter and registers its prompt hook", { skip: !entry }, async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "stellamem-host-smoke-"));
  try {
    const state = path.join(root, "state"), memory = path.join(root, "memory");
    await mkdir(state); await mkdir(memory);
    const config = path.join(root, "openclaw.json");
    await writeFile(config, JSON.stringify({ plugins: {
      allow: ["stellamem-adapter"],
      load: { paths: [path.join(repo, "adapters", "openclaw")] },
      entries: { "stellamem-adapter": { enabled: true,
        hooks: { allowConversationAccess: true }, config: { coreDir: memory } } },
    } }));
    const env = { PATH: process.env.PATH || "", OPENCLAW_STATE_DIR: state,
      OPENCLAW_CONFIG_PATH: config, OPENCLAW_DISABLE_BONJOUR: "1" };
    if (process.platform === "win32" && process.env.SystemRoot) env.SystemRoot = process.env.SystemRoot;
    const result = spawnSync(process.execPath, [entry, "plugins", "inspect", "stellamem-adapter", "--runtime", "--json"],
      { env, encoding: "utf8", timeout: 45_000, maxBuffer: 2_000_000 });
    assert.equal(result.status, 0, result.stderr || String(result.error));
    const inspection = JSON.parse(result.stdout);
    assert.equal(inspection.plugin.status, "loaded", JSON.stringify(inspection.diagnostics));
    assert.ok(inspection.typedHooks.some(hook => hook.name === "before_prompt_build"));
    assert.ok(!inspection.diagnostics.some(d => d.level === "error"));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
