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
    // H1 之后 source=assistant 的已批准记录可投影，ineligible:source 的
    // 覆盖改由 quoted 承担（quoted/unknown 仍被 source 门拒绝）。
    record({ claim_id: "c-asst", revision_id: "r-asst", source: "quoted", value: "虚构：星球邮差", evidence: "虚构：星球邮差" }),
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

test("kind-cap: fourth type-only same-kind claim is cut with a reason", async () => {
  const root = await fixture();
  // All four lessons pass the gate on lesson intent alone (their content
  // words share nothing with the prompt). The per-kind cap guards only
  // type-only claims, so the fourth is still cut.
  const lessons = ["出门要记得带伞", "浇花之前先看天气", "锁门之后再走远路", "买票要提前三天"].map((text, i) => record({
    claim_id: `c-l${i + 1}`, revision_id: `r-l${i + 1}`, kind: "lesson",
    value: `虚构教训${i + 1}：${text}`, evidence: `虚构教训${i + 1}：${text}`,
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

test("topical claims are bounded by the total budget, not the per-kind cap", async () => {
  const root = await fixture();
  const lessons = [1, 2, 3, 4, 5, 6, 7].map((i) => record({
    claim_id: `c-x${i}`, revision_id: `r-x${i}`, kind: "lesson",
    value: `虚构教训${i}：星球邮差要记得喝水`, evidence: `虚构教训${i}：星球邮差要记得喝水`,
  }));
  await writeRecords(root, "long-term/lessons.md", lessons);
  const prompt = "星球邮差有什么经验教训";
  await withDiagDir(async (dir) => {
    const out = await project(root, prompt);
    const diag = await readSidecar(dir, prompt);
    // All seven share wording with the prompt (topical), so none is cut
    // by the per-kind cap that guards type-only claims.
    assert.equal(diag.projected_n, 7);
    assert.equal(diag.records.filter((r) => r.reason === "kind-cap").length, 0);
  });
});

test("scope match: the scope label alone can make a claim topical", async () => {
  const root = await fixture();
  await writeRecords(root, "semantic/facts.md", [
    record({ claim_id: "c-gym", revision_id: "r-gym", scope: "gym membership",
      value: "user paid the annual fee in March", evidence: "user paid the annual fee in March" }),
    record({ claim_id: "c-other", revision_id: "r-other", scope: "car repair",
      value: "user paid the annual fee in March", evidence: "user paid the annual fee in March" }),
  ]);
  const prompt = "How much is my gym membership?";
  await withDiagDir(async (dir) => {
    const out = await project(root, prompt, { activeScopes: ["gym membership", "car repair"] });
    const diag = await readSidecar(dir, prompt);
    const byId = Object.fromEntries(diag.records.map((r) => [r.claim_id, r]));
    assert.equal(byId["c-gym"].stage, "projected");
    assert.equal(byId["c-other"].stage, "dropped");
    assert.equal(byId["c-other"].reason, "no-relevance");
  });
});

test("fill pass: a dated claim enters a temporal question, not a plain one", async () => {
  const root = await fixture();
  await writeRecords(root, "semantic/facts.md", [
    record({ claim_id: "c-cat", revision_id: "r-cat",
      value: "user adopted a cat last spring", evidence: "user adopted a cat last spring",
      provenance: { session_id: "s1", message_id: "m1", timestamp: "2023-03-15" } }),
  ]);
  const temporal = "How many days passed between the concert and the recital?";
  await withDiagDir(async (dir) => {
    const out = await project(root, temporal);
    const diag = await readSidecar(dir, temporal);
    const byId = Object.fromEntries(diag.records.map((r) => [r.claim_id, r]));
    assert.equal(byId["c-cat"].stage, "projected");
    assert.equal(byId["c-cat"].score, 0.04);
  });
  const plain = "What is the favorite color?";
  await withDiagDir(async (dir) => {
    const out = await project(root, plain);
    const diag = await readSidecar(dir, plain);
    const byId = Object.fromEntries(diag.records.map((r) => [r.claim_id, r]));
    assert.equal(byId["c-cat"].stage, "dropped");
    assert.equal(byId["c-cat"].reason, "no-relevance");
  });
});

test("stem-lite: an inflected claim word still matches the question", async () => {
  const root = await fixture();
  await writeRecords(root, "semantic/facts.md", [
    record({ claim_id: "c-tickets", revision_id: "r-tickets",
      value: "user bought tickets for the concert", evidence: "user bought tickets for the concert" }),
    record({ claim_id: "c-far", revision_id: "r-far",
      value: "user adopted a cat last spring", evidence: "user adopted a cat last spring" }),
  ]);
  const prompt = "How much did the ticket cost?";
  await withDiagDir(async (dir) => {
    const out = await project(root, prompt);
    const diag = await readSidecar(dir, prompt);
    const byId = Object.fromEntries(diag.records.map((r) => [r.claim_id, r]));
    assert.equal(byId["c-tickets"].stage, "projected");
    assert.equal(byId["c-far"].stage, "dropped");
    assert.equal(byId["c-far"].reason, "no-relevance");
  });
});

test("total-cap: claims past the twentieth are cut with a reason", async () => {
  const root = await fixture();
  const kinds = ["project", "preference", "lesson", "decision", "fact"];
  const claims = [];
  for (const kind of kinds) {
    for (let i = 1; i <= 5; i++) {
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
    assert.equal(diag.projected_n, 20);
    const cut = diag.records.filter((r) => r.reason === "total-cap");
    assert.equal(cut.length, 5, "five claims past the cap: " + JSON.stringify(diag.records.map((r) => [r.claim_id, r.stage, r.reason])));
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
