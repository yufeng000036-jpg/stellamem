import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile, readFile, rename, readdir } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { writeDaily } from "../hooks/daily-autolog/handler.js";
import { project } from "../adapters/openclaw/index.js";

async function fixture() {
  const root = await mkdtemp(path.join(os.tmpdir(), "stellamem-js-"));
  const sessionsDir = path.join(root, "sessions");
  const dailyDir = path.join(root, "daily");
  await mkdir(sessionsDir); await mkdir(dailyDir);
  return { root, sessionsDir, dailyDir, offset: 0 };
}
function message(id, time, text = "fictional message") {
  return JSON.stringify({ type: "message", id, timestamp: "2026-01-01T15:32:" + time + "Z",
    message: { role: "user", content: [{ type: "text", text }] } });
}
test("same-minute and late arrivals retained, duplicates idempotent, no transcript copy", async () => {
  const f = await fixture();
  const file = path.join(f.sessionsDir, "session.jsonl");
  await writeFile(file, message("1", "01") + "\n");
  assert.equal((await writeDaily("2026-01-01", f)).added, 1);
  await writeFile(file, [message("1", "01"), message("2", "20"), message("3", "59")].join("\n"));
  assert.equal((await writeDaily("2026-01-01", f)).added, 2);
  assert.equal((await writeDaily("2026-01-01", f)).added, 0);
  await writeFile(file, [message("1", "01"), message("2", "20"), message("3", "59"), message("4", "10")].join("\n"));
  assert.equal((await writeDaily("2026-01-01", f)).added, 1);
  const manifest = await readFile(path.join(f.dailyDir, "2026-01-01.sources.json"), "utf8");
  assert.equal(JSON.parse(manifest).pointers.length, 4);
  assert.ok(!manifest.includes("fictional message"));
  assert.ok(!(await readFile(path.join(f.dailyDir, "2026-01-01.md"), "utf8")).includes("fictional message"));
});
test("500 messages, huge message, rotation and malformed partial line", async () => {
  const f = await fixture();
  const file = path.join(f.sessionsDir, "session.jsonl");
  const rows = Array.from({ length: 500 }, (_, i) => message(String(i), "01", "x".repeat(i === 250 ? 20000 : 10)));
  await writeFile(file, rows.join("\n"));
  assert.equal((await writeDaily("2026-01-01", f)).total, 500);
  await rename(file, file + ".reset.2026");
  assert.equal((await writeDaily("2026-01-01", f)).added, 0);
  await writeFile(file, '{"partial":');
  await assert.rejects(writeDaily("2026-01-01", f));
  assert.equal(JSON.parse(await readFile(path.join(f.dailyDir, "2026-01-01.sources.json"))).pointers.length, 500);
});
function record(updates = {}) {
  return { schema_version: 2, claim_id: "c-test", revision_id: "r-test", status: "active",
    kind: "preference", subject: "user", scope: "global", predicate: "response_length",
    value: "concise", evidence: "concise", evidence_verified: true, source: "user",
    sensitivity: "PRIVATE", write_policy: "allow", injection_policy: "recall", ...updates };
}
test("projection denies sensitive, secrets, unrelated tasks, legacy and oversized claims", async () => {
  const f = await fixture();
  await mkdir(path.join(f.root, "core"));
  const data = [record(), record({ revision_id: "r-private", sensitive: true, value: "private" }),
    record({ revision_id: "r-secret", value: "sk-" + "A".repeat(30) }),
    record({ revision_id: "r-pending", status: "pending", value: "pending" })];
  const text = "legacy personal details\n<!-- stellamem:v2 -->\n" +
    data.map((c) => "```stellamem\n" + JSON.stringify(c) + "\n```\n").join("\n");
  await writeFile(path.join(f.root, "core/preferences.md"), text);
  assert.equal(await project(f.root, "weather today"), "");
  const output = await project(f.root, "response preference");
  assert.ok(output.includes("concise"));
  assert.ok(!output.includes("private"));
  assert.ok(!output.includes("sk-" + "A".repeat(30)));
  assert.ok(!output.includes("legacy personal"));
  assert.equal(await project(f.root, "response preference", { maxInjectChars: 5 }), "");
  await mkdir(path.join(f.root, "system"));
  await writeFile(path.join(f.root, "system/transaction.json"), "{}");
  assert.equal(await project(f.root, "response preference"), "");
});
