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

test("isolated host passes only relevant synthetic memory to a local fixture model",
  { skip: !entry, timeout: 120_000 }, async () => {
    const { createServer } = await import("node:http");
    const { spawn } = await import("node:child_process");
    const root = await mkdtemp(path.join(os.tmpdir(), "stellamem-host-turn-"));
    let requests = [];
    const server = createServer((req, res) => {
      const chunks = [];
      req.on("data", chunk => chunks.push(chunk));
      req.on("end", () => {
        requests.push(Buffer.concat(chunks).toString("utf8"));
        res.writeHead(200, { "content-type": "text/event-stream" });
        const frame = (delta, finish_reason) => ({ id: "fixture", object: "chat.completion.chunk",
          created: 0, model: "fixture", choices: [{ index: 0, delta, finish_reason }] });
        res.write(`data: ${JSON.stringify(frame({ role: "assistant", content: "fixture response" }, null))}\n\n`);
        res.write(`data: ${JSON.stringify(frame({}, "stop"))}\n\n`);
        res.end("data: [DONE]\n\n");
      });
    });
    await new Promise((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
    try {
      const memory = path.join(root, "memory"), workspace = path.join(root, "workspace");
      await mkdir(path.join(memory, "semantic"), { recursive: true }); await mkdir(workspace);
      const claim = { schema_version: 2, claim_id: "c-fixture", revision_id: "r-fixture", status: "active",
        kind: "fact", subject: "user", scope: "global", predicate: "favorite fictional beverage",
        value: "fictional nebula tea", evidence: "fictional nebula tea", evidence_verified: true,
        source: "user", sensitivity: "PRIVATE", write_policy: "allow", injection_policy: "recall" };
      await writeFile(path.join(memory, "semantic", "facts.md"),
        `<!-- stellamem:v2 -->\n\`\`\`stellamem\n${JSON.stringify(claim)}\n\`\`\`\n`);
      const config = path.join(root, "openclaw.json");
      await writeFile(config, JSON.stringify({
        agents: { defaults: { workspace, model: { primary: "localfixture/fixture" } } },
        models: { mode: "replace", providers: { localfixture: {
          baseUrl: `http://127.0.0.1:${server.address().port}/v1`, apiKey: "fixture-local-only",
          api: "openai-completions", models: [{ id: "fixture", name: "fixture", reasoning: false,
            input: ["text"], cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
            contextWindow: 32000, maxTokens: 256 }] } } },
        plugins: { allow: ["stellamem-adapter"], load: { paths: [path.join(repo, "adapters", "openclaw")] },
          entries: { "stellamem-adapter": { enabled: true, hooks: { allowConversationAccess: true },
            config: { coreDir: memory } } } },
      }));
      async function turn(name, prompt) {
        const env = { PATH: process.env.PATH || "", OPENCLAW_STATE_DIR: path.join(root, name),
          OPENCLAW_CONFIG_PATH: config, OPENCLAW_DISABLE_BONJOUR: "1" };
        if (process.platform === "win32" && process.env.SystemRoot) env.SystemRoot = process.env.SystemRoot;
        requests = [];
        const child = spawn(process.execPath, [entry, "agent", "--local", "--agent", "main",
          "--message", prompt, "--json", "--timeout", "20"], { env, stdio: ["ignore", "pipe", "pipe"] });
        let stderr = ""; child.stderr.on("data", data => { stderr += data; });
        child.stdout.resume();
        const code = await new Promise((resolve, reject) => {
          const timer = setTimeout(() => { child.kill(); reject(new Error("isolated host timed out")); }, 50_000);
          child.once("error", error => { clearTimeout(timer); reject(error); });
          child.once("close", value => { clearTimeout(timer); resolve(value); });
        });
        assert.equal(code, 0, stderr);
        assert.equal(requests.length, 1, "fixture model must receive exactly one request");
        return requests[0];
      }
      assert.ok((await turn("state-relevant", "What is my favorite fictional beverage?")).includes("fictional nebula tea"));
      assert.ok(!(await turn("state-unrelated", "What is the capital of France?")).includes("fictional nebula tea"));
    } finally {
      await new Promise(resolve => server.close(resolve));
      await rm(root, { recursive: true, force: true });
    }
  });
