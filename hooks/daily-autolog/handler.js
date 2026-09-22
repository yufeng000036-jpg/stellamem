// No transcript duplication and no model call. Canonical sessions stay at host.
import { readFile, readdir, mkdir, open, unlink } from "node:fs/promises";
import path from "node:path";
import { atomicWrite, hash, within } from "../../shared/runtime.mjs";

const SESSIONS_DIR = "<YOUR_OPENCLAW_SESSIONS_DIR>";
const DAILY_DIR = "<YOUR_DAILY_DIR>";
const TIMEZONE_OFFSET = "<YOUR_TZ_OFFSET>";

function day(timestamp, offset) {
  return new Date(new Date(timestamp).getTime() + offset * 3600000).toISOString().slice(0, 10);
}
function content(entry) {
  const value = entry.message?.content;
  return Array.isArray(value) ? value.filter((v) => v && typeof v.text === "string").map((v) => v.text).join(" ")
    : typeof value === "string" ? value : "";
}
function pointerKey(pointer) {
  return hash(JSON.stringify([pointer.session_key, pointer.message_id, pointer.entry_index,
    pointer.timestamp, pointer.content_hash]));
}
export async function readPointers(date, sessionsDir, offset = 0) {
  const files = (await readdir(sessionsDir)).filter((f) =>
    /^[^.].*\.jsonl(?:\.reset\..+)?$/.test(f) && !f.includes("trajectory")).sort();
  const pointers = new Map();
  for (const file of files) {
    const lines = (await readFile(await within(sessionsDir, file), "utf8")).split(/\r?\n/);
    for (let i = 0; i < lines.length; i++) {
      if (!lines[i].trim()) continue;
      // A partial last line or malformed entry blocks this attempt, never skipped.
      const entry = JSON.parse(lines[i]);
      if (entry.type !== "message") continue;
      const speaker = entry.message?.role;
      if (!["user", "assistant"].includes(speaker)) continue;
      if (!entry.timestamp || !Number.isFinite(Date.parse(entry.timestamp))) throw new Error("invalid timestamp");
      if (day(entry.timestamp, offset) !== date) continue;
      const text = content(entry);
      if (!text) continue;
      const pointer = {
        provider: "openclaw", session_file: file, session_key: file.split(".jsonl")[0],
        timestamp: entry.timestamp, message_id: String(entry.id || entry.message?.id || i),
        entry_index: i, content_hash: hash(text), speaker, precision: "message",
      };
      pointers.set(pointerKey(pointer), pointer);
    }
  }
  return [...pointers.values()].sort((a, b) =>
    a.timestamp.localeCompare(b.timestamp) || a.session_key.localeCompare(b.session_key) || a.entry_index - b.entry_index);
}
export async function writeDaily(date, options = {}) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) throw new Error("invalid date");
  const sessionsDir = options.sessionsDir || SESSIONS_DIR;
  const dailyDir = options.dailyDir || DAILY_DIR;
  const offset = options.offset ?? Number(process.env.STELLAMEM_TZ_OFFSET || TIMEZONE_OFFSET);
  const timezone = Number.isFinite(offset) ? offset : 0;
  await mkdir(dailyDir, { recursive: true });
  const lockFile = path.join(dailyDir, ".autolog.lock");
  const lock = await open(lockFile, "wx");
  try {
    const manifestPath = path.join(dailyDir, date + ".sources.json");
    let previous = { schema_version: 2, pointers: [] };
    try {
      previous = JSON.parse(await readFile(await within(dailyDir, date + ".sources.json"), "utf8"));
      if (previous.schema_version !== 2 || !Array.isArray(previous.pointers)) throw new Error("invalid pointer manifest");
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
    const found = await readPointers(date, sessionsDir, timezone);
    const merged = new Map(previous.pointers.map((p) => [pointerKey(p), p]));
    const originalCount = merged.size;
    for (const pointer of found) merged.set(pointerKey(pointer), pointer);
    const pointers = [...merged.values()];
    if (!pointers.length) return { added: 0, total: 0 };
    // Manifest is the durable input. A failed summary write is repaired next run.
    await atomicWrite(manifestPath, JSON.stringify({ schema_version: 2, pointers }, null, 2) + "\n");
    const file = path.join(dailyDir, date + ".md");
    let existing = "";
    try { existing = await readFile(await within(dailyDir, date + ".md"), "utf8"); }
    catch (error) { if (error.code !== "ENOENT") throw error; }
    const marker = "<!-- stellamem:activity-summary -->";
    const preserved = existing.split(marker)[0].trimEnd();
    const summary = "# " + date + "\n\nActivity summary: " + pointers.length +
      " source messages indexed. Original text remains in host sessions.\n" +
      "Compile from the verified source manifest; this summary is not evidence.\n";
    await atomicWrite(file, preserved + "\n\n" + marker + "\n" + summary);
    return { added: merged.size - originalCount, total: merged.size };
  } catch (error) {
    await atomicWrite(path.join(dailyDir, ".autolog-status.json"),
      JSON.stringify({ status: "failed", error: error.name, code: error.code || "INVALID_SOURCE" })).catch(() => {});
    throw error;
  } finally {
    await lock.close();
    await unlink(lockFile);
  }
}
export default async function handler(event) {
  try {
    const offset = Number(process.env.STELLAMEM_TZ_OFFSET || TIMEZONE_OFFSET) || 0;
    if (event?.type === "command" && ["new", "reset"].includes(event.action)) {
      await writeDaily(day(Date.now(), offset));
    } else if (event?.type === "gateway" && event.action === "startup") {
      await writeDaily(day(Date.now() - 86400000, offset));
    }
  } catch (error) {
    // Never echo file paths, bodies, credentials or full stack traces.
    console.error("[daily-autolog] failed; retry required:", error.code || error.name);
  }
}
