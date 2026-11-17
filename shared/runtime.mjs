import { createRequire } from "node:module";
import { realpath, mkdir, open, rename, unlink } from "node:fs/promises";
import path from "node:path";
import { createHash, randomUUID } from "node:crypto";

// Load the single canonical rules file synchronously. A literal require lets
// OpenClaw's plugin bundler include the JSON asset; a dynamic readFileSync URL
// pointed at a file absent from its isolated package capture. createRequire
// also works on Node 18 and avoids top-level await in older host loaders.
const require = createRequire(import.meta.url);
export const RULES = require("./policy-rules.json");
export function matches(group, text) {
  return RULES[group].some((pattern) => new RegExp(pattern, "i").test(String(text)));
}
export function hasSecret(value) {
  return matches("secret", typeof value === "string" ? value : JSON.stringify(value));
}
export function hash(value) {
  return createHash("sha256").update(value, "utf8").digest("hex");
}
export async function atomicWrite(file, content) {
  await mkdir(path.dirname(file), { recursive: true });
  const tmp = file + ".tmp-" + randomUUID();
  const handle = await open(tmp, "wx");
  try {
    await handle.writeFile(content, "utf8");
    await handle.sync();
  } finally {
    await handle.close();
  }
  try {
    await rename(tmp, file);
  } finally {
    await unlink(tmp).catch((error) => { if (error.code !== "ENOENT") throw error; });
  }
}
export async function within(root, relative) {
  if (path.isAbsolute(relative) || relative.split(/[\\/]/).includes("..")) {
    throw new Error("unsafe relative path");
  }
  const base = await realpath(root);
  const file = path.resolve(base, relative);
  const actual = await realpath(file);
  const rel = path.relative(base, actual);
  if (rel.startsWith("..") || path.isAbsolute(rel)) throw new Error("path leaves root");
  return actual;
}
