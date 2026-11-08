// 2^53 id 精度检查（3c）的 JS 一侧：字符串 id 在适配器解析路径上
// 精确往返；数字 id 会被 JSON.parse 静默舍入，所以仓库内 id 一律走字符串。
// 见 tests/test_id_precision_2pow53.py 里的调查结论：没发现实测问题，
// 本文件把边界钉死——以后任何写路径敢产数字 id，测试先红。
//
// 注意：JS 源码里的数字字面量 9007199254740993 本身在解析时就被舍入，
// 所以本文件一律用字符串比较，不用数字字面量断言——这正是该隐患
// 最难察觉的地方。
import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { records } from "../adapters/openclaw/index.js";

const BIG_TEXT = "9007199254740993"; // 2^53 + 1，精确值只能用字符串表达

test("JSON.parse silently rounds integers above 2^53", () => {
  assert.equal(String(JSON.parse(BIG_TEXT)), "9007199254740992");
  assert.notEqual(String(JSON.parse(BIG_TEXT)), BIG_TEXT);
});

test("string ids round-trip exactly through the adapter record parser", () => {
  const text =
    "<!-- stellamem:v2 -->\n" +
    "```stellamem\n" +
    JSON.stringify({ claim_id: "c-9007199254740993", revision_id: "r-1", status: "active" }) +
    "\n```\n";
  const parsed = records(text);
  assert.equal(parsed.length, 1);
  assert.equal(parsed[0].claim_id, "c-9007199254740993");
  // 同一串数字当 JSON number 走解析路径就会变——所以 id 只能是字符串。
  const numeric = records(
    "<!-- stellamem:v2 -->\n```stellamem\n" +
      `{"claim_id": ${BIG_TEXT}, "revision_id": "r-1", "status": "active"}` +
      "\n```\n",
  );
  assert.equal(String(numeric[0].claim_id), "9007199254740992");
  assert.notEqual(String(numeric[0].claim_id), BIG_TEXT);
});

test("ids flowing through JS JSON paths are strings", async () => {
  const root = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
  const corpus = await readFile(path.join(root, "benchmark/reliability/corpus.jsonl"), "utf8");
  for (const line of corpus.trim().split("\n")) {
    const item = JSON.parse(line);
    assert.equal(typeof item.id, "string");
    for (const id of item.expected_ids) assert.equal(typeof id, "string");
    for (const r of item.records) {
      assert.equal(typeof r.claim_id, "string");
      assert.equal(typeof r.revision_id, "string");
    }
  }
});
