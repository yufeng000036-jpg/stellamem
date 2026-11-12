// H1 回归测试：人工批准过的 assistant 历史记录可以投影，其余非 user 来源仍不行。
// 只用隔离临时目录与虚构数据，不碰生产记忆。
import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { project } from "../adapters/openclaw/index.js";

async function fixture() {
  return mkdtemp(path.join(os.tmpdir(), "stellamem-adapter-h1-"));
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

const BEER = {
  claim_id: "c-beer", revision_id: "r-beer", predicate: "beer recommended for seco de cordero",
  value: "虚构：清淡酒体的啤酒配羊肉炖菜", evidence: "虚构：清淡酒体的啤酒配羊肉炖菜",
};

test("approved assistant history projects with attribution preserved", async () => {
  const root = await fixture();
  await writeRecords(root, "semantic/facts.md", [
    record({ ...BEER, source: "assistant" }),
  ]);
  const out = await project(root, "羊肉炖菜配什么啤酒");
  assert.ok(out.includes("清淡酒体"), "assistant history must be injected: " + out);
});

test("assistant record without verified evidence still does not project", async () => {
  const root = await fixture();
  await writeRecords(root, "semantic/facts.md", [
    record({ ...BEER, source: "assistant", evidence_verified: false }),
  ]);
  const out = await project(root, "羊肉炖菜配什么啤酒");
  assert.equal(out, "");
});

test("quoted and unknown sources still do not project", async () => {
  const root = await fixture();
  await writeRecords(root, "semantic/facts.md", [
    record({ ...BEER, claim_id: "c-q", revision_id: "r-q", source: "quoted" }),
    record({ ...BEER, claim_id: "c-u", revision_id: "r-u", source: "unknown" }),
  ]);
  const out = await project(root, "羊肉炖菜配什么啤酒");
  assert.equal(out, "");
});

test("pending assistant record does not project (approval is the gate)", async () => {
  const root = await fixture();
  await writeRecords(root, "semantic/facts.md", [
    record({ ...BEER, source: "assistant", status: "pending" }),
  ]);
  const out = await project(root, "羊肉炖菜配什么啤酒");
  assert.equal(out, "");
});
