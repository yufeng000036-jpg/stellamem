import { readFile, realpath, mkdir, open, rename, unlink } from "node:fs/promises";
import path from "node:path";
import { createHash, randomUUID } from "node:crypto";

export const RULES = JSON.parse(await readFile(new URL("./policy-rules.json", import.meta.url), "utf8"));
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
