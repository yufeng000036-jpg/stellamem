// 注入探针：给定投影模块、记忆根、提问，输出实际会注入多少条 claim。
// 用法：node benchmark/projection_probe.mjs <projection.js> <root> <prompt>
const [, , modulePath, root, prompt] = process.argv;
if (!modulePath || !root) {
  console.error("usage: node projection_probe.mjs <projection.js> <root> <prompt>");
  process.exit(2);
}
const url = new URL("file:///" + modulePath.replace(/\\/g, "/"));
const mod = await import(url.href);
const out = await mod.project(root, prompt || "我有哪些项目", { maxInjectChars: 4000 });
const lines = out.split("\n").filter((line) => line.trim().startsWith("{"));
const claims = lines.map((line) => {
  try {
    return JSON.parse(line);
  } catch {
    return { raw: line };
  }
});
console.log(JSON.stringify({
  prompt,
  chars: out.length,
  claims: claims.length,
  kinds: claims.reduce((acc, c) => {
    acc[c.kind || "unknown"] = (acc[c.kind || "unknown"] || 0) + 1;
    return acc;
  }, {}),
  ids: claims.map((c) => c.id || null),
}, null, 2));
