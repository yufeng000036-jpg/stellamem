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

test("evidence rescues a claim whose value was translated away from the question language", async () => {
  // LongMemEval 重跑 2026-11-06 的头号损失：提取常把英文原文的值写成中文
  // （82% 的英文源 claim 值是中文），而问题是英文，值面词面为 0、bigram
  // 为 0，gold claim 被相关性门拦掉，reader 直接 abstain。evidence 是源
  // 消息的原文引用，带着问题自己的词，应当参与相关性打分。
  const root = await fixture();
  await writeRecords(root, "semantic/facts.md", [
    record({ claim_id: "c-gold", revision_id: "r-gold", kind: "fact",
      predicate: "used coupon",
      value: "在沃尔玛用 Luvs 纸尿裤买一送一券",
      evidence: "I used a Buy One Get One Free coupon on Luvs diapers at Walmart today" }),
  ]);
  const out = await project(root, "What coupon did I use on Luvs diapers at Walmart?");
  assert.ok(out.includes("在沃尔玛"), "evidence-overlap claim must be injected: " + out);
  // 同一条 claim，问无关问题时 evidence 也不应把它拖进来。
  assert.equal(await project(root, "今天天气怎么样"), "");
  assert.equal(await project(root, "法国的首都是哪里"), "");
});

test("a value match still outranks an evidence-only match", async () => {
  // evidence 只补信号、不抢分：值面直接命中的 claim 保持原来的高分，
  // 排在只靠 evidence 命中的 claim 前面；两者都注入，但顺序不能反。
  const root = await fixture();
  await writeRecords(root, "semantic/facts.md", [
    record({ claim_id: "c-ev", revision_id: "r-ev", kind: "fact",
      predicate: "used coupon",
      value: "在沃尔玛用券",
      evidence: "I used a coupon at Walmart" }),
    record({ claim_id: "c-val", revision_id: "r-val", kind: "fact",
      predicate: "used coupon",
      value: "Walmart coupon",
      evidence: "Walmart coupon" }),
  ]);
  const out = await project(root, "What coupon did I use at Walmart?");
  assert.ok(out.includes("Walmart coupon"), "value match must inject: " + out);
  assert.ok(out.includes("在沃尔玛用券"), "evidence match must inject: " + out);
  assert.ok(out.indexOf("Walmart coupon") < out.indexOf("在沃尔玛用券"),
    "value match must rank above evidence-only match: " + out);
});

test("projected lines carry the claim date when provenance has a timestamp", async () => {
  // LongMemEval 重跑：时间推理 127 题判分只有 0.079；投影行没有日期，reader
  // 排不了事件顺序（直接塞原文臂每行自带 date）。provenance.timestamp 是源
  // 消息的时间戳，有就带上，没有就不出 date 字段、不报错。
  const root = await fixture();
  await writeRecords(root, "semantic/facts.md", [
    record({ claim_id: "c-dated", revision_id: "r-dated", kind: "fact",
      predicate: "used coupon", value: "Walmart coupon",
      evidence: "I used a coupon at Walmart",
      provenance: { precision: "message", session_id: "s1", message_id: "s1:0",
        timestamp: "2023-04-10T17:50:00+00:00" } }),
    record({ claim_id: "c-undated", revision_id: "r-undated", kind: "fact",
      predicate: "bought", value: "Walmart gift card", evidence: "Walmart gift card" }),
  ]);
  const out = await project(root, "What coupon did I use at Walmart?");
  const lines = out.split("\n").slice(1).filter(Boolean).map((l) => JSON.parse(l));
  assert.equal(lines.length, 2, "both matching claims must project: " + out);
  const dated = lines.find((l) => l.id === "c-dated");
  const undated = lines.find((l) => l.id === "c-undated");
  assert.equal(dated.date, "2023-04-10T17:50:00+00:00");
  assert.ok(!("date" in undated), "claim without provenance must not gain a date field");
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
