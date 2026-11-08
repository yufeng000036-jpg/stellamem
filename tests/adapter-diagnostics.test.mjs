// 适配器 claim 级诊断：project() 的逐条去向（注入/丢弃 + 原因）。
// 只用隔离临时目录与虚构数据，不碰生产记忆。诊断默认关闭，
// 开启时只多写一个 sidecar JSON，返回的投影字符串必须逐字节不变。
import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile, readFile, readdir, rm } from "node:fs/promises";
import { createHash } from "node:crypto";
import os from "node:os";
import path from "node:path";
import { project } from "../adapters/openclaw/index.js";

async function fixture() {
  return await mkdtemp(path.join(os.tmpdir(), "stellamem-adapter-diag-"));
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
async function withDiagDir(fn) {
  const dir = await mkdtemp(path.join(os.tmpdir(), "stellamem-diag-out-"));
  const prev = process.env.STELLAMEM_PROJECT_DIAGNOSTICS_DIR;
  process.env.STELLAMEM_PROJECT_DIAGNOSTICS_DIR = dir;
  try {
    return await fn(dir);
  } finally {
    if (prev === undefined) delete process.env.STELLAMEM_PROJECT_DIAGNOSTICS_DIR;
    else process.env.STELLAMEM_PROJECT_DIAGNOSTICS_DIR = prev;
    await rm(dir, { recursive: true, force: true });
  }
}
async function readSidecar(dir, prompt) {
  const name = createHash("sha256").update(prompt).digest("hex").slice(0, 16) + ".json";
  return JSON.parse(await readFile(path.join(dir, name), "utf8"));
}

test("diagnostics off by default: no sidecar, output unchanged", async () => {
  const root = await fixture();
  await writeRecords(root, "semantic/facts.md", [
    record({ claim_id: "c-a", revision_id: "r-a", value: "虚构：星球邮差", evidence: "虚构：星球邮差" }),
  ]);
  delete process.env.STELLAMEM_PROJECT_DIAGNOSTICS_DIR;
  const out = await project(root, "星球邮差是什么");
  assert.ok(out.includes("星球邮差"));
  // Nothing was written anywhere: the cwd has no sidecar dir to check, but
  // the call must not throw and must not create files under the store root.
  assert.deepEqual(await readdir(root), ["semantic"]);
});

test("projected + ineligible + no-relevance reasons are recorded", async () => {
  const root = await fixture();
  await writeRecords(root, "semantic/facts.md", [
    record({ claim_id: "c-gold", revision_id: "r-gold", value: "虚构：星球邮差", evidence: "虚构：星球邮差" }),
    record({ claim_id: "c-asst", revision_id: "r-asst", source: "assistant", value: "虚构：星球邮差", evidence: "虚构：星球邮差" }),
    record({ claim_id: "c-far", revision_id: "r-far", value: "虚构：每天要记得喝水", evidence: "虚构：每天要记得喝水" }),
  ]);
  const prompt = "星球邮差是什么";
  await withDiagDir(async (dir) => {
    const out = await project(root, prompt);
    const diag = await readSidecar(dir, prompt);
    assert.equal(diag.context, out, "sidecar context must equal the returned string");
    assert.equal(diag.projected_n, 1);
    assert.equal(diag.candidates_n, 3);
    const byId = Object.fromEntries(diag.records.map((r) => [r.claim_id, r]));
    assert.equal(byId["c-gold"].stage, "projected");
    assert.equal(byId["c-gold"].reason, null);
    assert.equal(byId["c-asst"].stage, "dropped");
    assert.equal(byId["c-asst"].reason, "ineligible:source");
    assert.equal(byId["c-far"].stage, "dropped");
    assert.equal(byId["c-far"].reason, "no-relevance");
  });
});

test("kind-cap: fourth same-kind claim is cut with a reason", async () => {
  const root = await fixture();
  const lessons = [1, 2, 3, 4].map((i) => record({
    claim_id: `c-l${i}`, revision_id: `r-l${i}`, kind: "lesson",
    value: `虚构教训${i}：星球邮差要记得喝水`, evidence: `虚构教训${i}：星球邮差要记得喝水`,
  }));
  await writeRecords(root, "long-term/lessons.md", lessons);
  const prompt = "星球邮差有什么经验教训";
  await withDiagDir(async (dir) => {
    const out = await project(root, prompt);
    const diag = await readSidecar(dir, prompt);
    assert.equal(diag.projected_n, 3);
    const byId = Object.fromEntries(diag.records.map((r) => [r.claim_id, r]));
    assert.equal(byId["c-l4"].stage, "dropped");
    assert.equal(byId["c-l4"].reason, "kind-cap");
  });
});

test("total-cap: claims past the twelfth are cut with a reason", async () => {
  const root = await fixture();
  const kinds = ["project", "preference", "lesson", "decision", "fact"];
  const claims = [];
  for (const kind of kinds) {
    for (let i = 1; i <= 3; i++) {
      claims.push(record({
        claim_id: `c-${kind}${i}`, revision_id: `r-${kind}${i}`, kind,
        value: `虚构：星球邮差${kind}${i}`, evidence: `虚构：星球邮差${kind}${i}`,
      }));
    }
  }
  await writeRecords(root, "semantic/facts.md", claims);
  const prompt = "星球邮差项目";
  await withDiagDir(async (dir) => {
    const out = await project(root, prompt);
    const diag = await readSidecar(dir, prompt);
    assert.equal(diag.projected_n, 12);
    const cut = diag.records.filter((r) => r.reason === "total-cap");
    assert.equal(cut.length, 3, "three claims past the cap: " + JSON.stringify(diag.records.map((r) => [r.claim_id, r.stage, r.reason])));
  });
});

test("ambiguous-revision: same claim_id with two revisions is never projected", async () => {
  const root = await fixture();
  await writeRecords(root, "semantic/facts.md", [
    record({ claim_id: "c-amb", revision_id: "r-amb1", value: "虚构：星球邮差", evidence: "虚构：星球邮差" }),
    record({ claim_id: "c-amb", revision_id: "r-amb2", value: "虚构：星球邮差", evidence: "虚构：星球邮差" }),
  ]);
  const prompt = "星球邮差是什么";
  await withDiagDir(async (dir) => {
    const out = await project(root, prompt);
    assert.equal(out, "", "ambiguous slot must project nothing");
    const diag = await readSidecar(dir, prompt);
    assert.equal(diag.empty_reason, "all-dropped");
    assert.ok(diag.records.every((r) => r.reason === "ambiguous-revision"),
      JSON.stringify(diag.records));
  });
});

test("char-cap: a long line is cut while a short one still fits", async () => {
  const root = await fixture();
  await writeRecords(root, "semantic/facts.md", [
    record({ claim_id: "c-long", revision_id: "r-long", value: "虚构：星球邮差" + "水".repeat(800), evidence: "虚构：星球邮差" }),
    record({ claim_id: "c-short", revision_id: "r-short", kind: "project", value: "虚构：每天要记得喝水", evidence: "虚构：每天要记得喝水" }),
  ]);
  const prompt = "星球邮差项目";
  await withDiagDir(async (dir) => {
    const out = await project(root, prompt, { maxInjectChars: 600 });
    const diag = await readSidecar(dir, prompt);
    const byId = Object.fromEntries(diag.records.map((r) => [r.claim_id, r]));
    assert.equal(byId["c-long"].stage, "dropped");
    assert.equal(byId["c-long"].reason, "char-cap");
    assert.equal(byId["c-short"].stage, "projected");
    assert.ok(out.includes("喝水"));
  });
});

test("diagnostics on: returned string is byte-identical to diagnostics off", async () => {
  const root = await fixture();
  await writeRecords(root, "semantic/facts.md", [
    record({ claim_id: "c-a", revision_id: "r-a", value: "虚构：星球邮差", evidence: "虚构：星球邮差" }),
    record({ claim_id: "c-b", revision_id: "r-b", kind: "lesson", value: "虚构：星球邮差教训", evidence: "虚构：星球邮差教训" }),
  ]);
  const prompt = "星球邮差项目有什么教训";
  delete process.env.STELLAMEM_PROJECT_DIAGNOSTICS_DIR;
  const off = await project(root, prompt);
  await withDiagDir(async () => {
    const on = await project(root, prompt);
    assert.equal(on, off);
  });
});
