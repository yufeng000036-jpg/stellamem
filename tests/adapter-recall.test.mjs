// 适配器召回 P2 回归测试：自然问句与多会话召回。
// 只用隔离临时目录与虚构数据，不碰生产记忆。
import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { project } from "../adapters/openclaw/index.js";

async function fixture() {
  const root = await mkdtemp(path.join(os.tmpdir(), "stellamem-adapter-recall-"));
  return root;
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

test("E2E doc case: natural question without the keyword still injects the title fact", async () => {
  // E2E_OPENCLAW_VERIFICATION.md P1：模型把标题标成 fact，问题说“项目”，
  // 旧门词面为 0、意图为 NEVER，什么都不注入，模型只好现编一个标题。
  const root = await fixture();
  await writeRecords(root, "semantic/profile.md", [
    record({ claim_id: "c-title", revision_id: "r-title", predicate: "has title",
      value: "《星球邮差》", evidence: "《星球邮差》" }),
    record({ claim_id: "c-name", revision_id: "r-name", predicate: "name",
      value: "林河", evidence: "林河" }),
  ]);
  const out = await project(root, "我的绘本项目叫什么名字？");
  assert.ok(out.includes("《星球邮差》"), "title fact must be injected: " + out);
});

test("bigram-only paraphrase injects the claim even with no intent word", async () => {
  // 问题只提到 claim 值的一部分（邮差绘本的画风），整词词面为 0，
  // 意图词也没有；共享二元组 6 个，靠 bigram 信号注入。
  const root = await fixture();
  await writeRecords(root, "long-term/lessons.md", [
    record({ claim_id: "c-lesson", revision_id: "r-lesson", kind: "lesson",
      value: "虚构：星球邮差绘本的画风是水彩", evidence: "虚构：星球邮差绘本的画风是水彩" }),
  ]);
  const out = await project(root, "邮差绘本的画风怎么定的");
  assert.ok(out.includes("水彩"), "bigram-overlap claim must be injected: " + out);
  assert.equal(await project(root, "今天天气怎么样"), "");
});

test("multi-session: gold claim survives same-kind distractors and the per-kind cap", async () => {
  // 5 条同类 lesson 干扰项先写，gold 最后写：若 gold 只是意图 0.05 分，
  // 与干扰项同分按文件顺序排，cap 3 会把它切掉；bigram 0.25 分让它排第一。
  const root = await fixture();
  const distractors = [1, 2, 3, 4, 5].map((i) => record({
    claim_id: `c-d${i}`, revision_id: `r-d${i}`, kind: "lesson",
    value: `虚构干扰${i}：每天要记得喝水`, evidence: `虚构干扰${i}：每天要记得喝水`,
  }));
  const gold = record({ claim_id: "c-gold", revision_id: "r-gold", kind: "lesson",
    value: "虚构：星球邮差绘本的画风是水彩", evidence: "虚构：星球邮差绘本的画风是水彩" });
  await writeRecords(root, "long-term/lessons.md", [...distractors, gold]);
  // “经验”让所有 lesson  claim 都过意图门（0.05），gold 靠 bigram 排第一。
  const out = await project(root, "邮差绘本的画风怎么定的，有什么经验");
  assert.ok(out.includes("水彩"), "gold claim must survive the cap: " + out);
});

test("bigram overlap is topical: it passes the technical carve-out for preferences", async () => {
  // carve-out 只拦“只有意图词”的技术问题；问题提到偏好本身的内容
  // （冰滴咖啡）时是 topical 命中，应当注入。
  const root = await fixture();
  await writeRecords(root, "core/preferences.md", [
    record({ claim_id: "c-pref", revision_id: "r-pref", kind: "preference",
      value: "虚构：只喝冰滴咖啡", evidence: "虚构：只喝冰滴咖啡" }),
  ]);
  const out = await project(root, "偏好 冰滴咖啡 8081 端口");
  assert.ok(out.includes("冰滴咖啡"), "topical bigram must pass the carve-out: " + out);
});

test("irrelevant prompts still inject nothing", async () => {
  const root = await fixture();
  await writeRecords(root, "semantic/profile.md", [
    record({ claim_id: "c-title", revision_id: "r-title", predicate: "has title",
      value: "《星球邮差》", evidence: "《星球邮差》" }),
  ]);
  assert.equal(await project(root, "今天天气怎么样"), "");
  assert.equal(await project(root, "法国的首都是哪里"), "");
});
