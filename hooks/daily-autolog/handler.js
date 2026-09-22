// daily-autolog —— 自动把会话总结成 daily 日记（A+C）
// A：command:new / command:reset → 写今天 daily
// C：gateway:startup → 检查昨天 daily，缺失则补
// 幂等：只写"时间晚于 daily 已覆盖最后时间"的新会话
// 总结失败 → 降级为原始转录；时区可配置（默认 UTC+8）
//
// ═══════════ 安装必读（4 个地方要改）═══════════
// 1. 改 AI_NAME 为你自己的 AI 名字（日记以谁的第一人称写）
// 2. 改 SESSIONS_DIR 为你 OpenClaw 的会话目录（真实路径，
//    形如 <openclaw状态目录>/agents/main/sessions）
// 3. 改 DAILY_DIR 为你记忆根目录下的 daily 子目录
// 4. 改 MODEL_URL 为你本地模型服务的 chat 接口地址
//    （llama.cpp 默认 :8081，Ollama 用 :11434，LM Studio 用 :1234）
//    也可用环境变量 STELLAMEM_MODEL_URL 覆盖，无需改文件。
// ═══════════════════════════════════════════════════
//
// 实测确认的 OpenClaw hook-pack 事件（见 bundled/session-memory、boot-md）：
//   - command 事件：event.type === "command"，event.action === "new" | "reset"
//   - gateway 启动：event.type === "gateway"，event.action === "startup"
// 导出：export default async function handler(event) {}
//
// 日志策略：只在出错时打日志（console.error + error.log），正常路径静默。

import { readFile, writeFile, mkdir, readdir, appendFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

// ═══════════ 改成你自己的值 ═══════════
const AI_NAME = "你的AI名字"; // ← 日记的第一人称主体，改成你的 AI 名字
const SESSIONS_DIR = "<YOUR_OPENCLAW_SESSIONS_DIR>"; // ← 例：C:/Users/你/.openclaw/agents/main/sessions
const DAILY_DIR = "<YOUR_DAILY_DIR>"; // ← 例：C:/Users/你/<你的记忆根>/daily
const TIMEZONE_OFFSET = "<YOUR_TZ_OFFSET>"; // ← UTC 偏移小时数，例："8"=北京，"-5"=纽约，"0"=伦敦
// ═══════════════════════════════════════

// 模型服务地址：优先环境变量 STELLAMEM_MODEL_URL，其次安装器写入的值。
// 支持 llama.cpp(:8081) / Ollama(:11434) / LM Studio(:1234) 等任意 OpenAI 兼容端点。
const MODEL_URL =
  process.env.STELLAMEM_MODEL_URL || "<YOUR_MODEL_URL>";
// 错误日志：写到 handler 自己目录下，不碰任何记忆文件
const ERROR_LOG = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  "error.log"
);

// 只在出错时打日志：console.error 进 gateway stderr，同时写 error.log 兜底
function logError(msg, err) {
  const line = `[daily-autolog] ERROR ${msg}${err ? `: ${err?.stack || err?.message || String(err)}` : ""}`;
  try {
    console.error(line);
  } catch {}
  try {
    appendFile(ERROR_LOG, `${new Date().toISOString()} ${line}\n`).catch(() => {});
  } catch {}
}

// 时区偏移（小时）：优先环境变量 STELLAMEM_TZ_OFFSET，其次安装器写入值。
// 默认 8（Asia/Shanghai）。支持小数（如 5.5 = 印度）。
const TZ_OFFSET = (() => {
  const raw = process.env.STELLAMEM_TZ_OFFSET || TIMEZONE_OFFSET;
  const n = parseFloat(raw);
  return Number.isFinite(n) ? n : 8;
})();
const TZ_MS = TZ_OFFSET * 3600 * 1000;

// UTC 时间戳/字符串 → 本地日期字符串 YYYY-MM-DD
function getLocalDateStr(t) {
  return new Date(new Date(t).getTime() + TZ_MS).toISOString().slice(0, 10);
}

// UTC 时间戳/字符串 → 本地 HH:MM
function getLocalHHMM(t) {
  return new Date(new Date(t).getTime() + TZ_MS).toISOString().slice(11, 16);
}

// 去掉 user 消息里的 untrusted metadata 块，只留真实对话文本
function stripUntrustedMeta(text) {
  if (typeof text !== "string") return "";
  let t = text;
  t = t.replace(
    /(?:Conversation info|Sender) \(untrusted metadata\):\s*```[\s\S]*?```/g,
    ""
  );
  t = t.replace(/\[message_id:[^\]]*\]/g, "");
  t = t.replace(/\n{3,}/g, "\n\n");
  return t.trim();
}

// 读某天（本地日期）的所有会话消息
async function readSessionsForDate(dateStr) {
  const files = await readdir(SESSIONS_DIR).catch(() => []);
  // 注意：/new 或 /reset 后，OpenClaw 会把旧 session 重命名为
  // "xxx.jsonl.reset.<时间戳>" 这种归档文件，历史消息都在这些归档里。
  // 必须用 includes(".jsonl") 而非 endsWith，否则会漏掉所有归档文件。
  const jsonlFiles = files.filter(
    (f) => f.includes(".jsonl") && !f.includes("trajectory")
  );
  const msgs = [];
  for (const f of jsonlFiles) {
    try {
      const text = await readFile(path.join(SESSIONS_DIR, f), "utf8");
      for (const line of text.trim().split("\n")) {
        try {
          const entry = JSON.parse(line);
          if (entry.type !== "message") continue;
          const ts = entry.timestamp;
          if (!ts || getLocalDateStr(ts) !== dateStr) continue;
          const role = entry.message?.role || "?";
          // 过滤工具输出（toolResult/tool），只保留真实对话 user/assistant
          if (role === "toolResult" || role === "tool") continue;
          const content = stripUntrustedMeta(
            (entry.message?.content || [])
              .map((c) => c.text || "")
              .join(" ")
          ).slice(0, 500);
          if (content) msgs.push({ time: getLocalHHMM(ts), role, content });
        } catch {}
      }
    } catch {}
  }
  msgs.sort((a, b) => a.time.localeCompare(b.time));
  return msgs;
}

// 从 daily 文件解析"已覆盖的最后时间"。
// 优先读 handler 自己写的隐藏进度标记（可靠），
// 读不到再回退解析模型输出的 [HH:MM]（不可靠，仅作兼容）。
const COVER_MARK = "daily-autolog:last=";
async function getLastCoveredTime(dailyPath) {
  try {
    const text = await readFile(dailyPath, "utf8");
    // 1) 优先读进度标记
    const markMatch = text.match(
      /<!--\s*daily-autolog:last=(\d{1,2}:\d{2})\s*-->/
    );
    if (markMatch) {
      return markMatch[1].padStart(5, "0").replace(/^(\d):/, "0$1:");
    }
    // 2) 回退：最后一个 [HH:MM]
    const times = [...text.matchAll(/\[(\d{1,2}):(\d{2})\]/g)].map(
      (m) => m[1].padStart(2, "0") + ":" + m[2]
    );
    return times.length ? times[times.length - 1] : null;
  } catch {
    return null;
  }
}

// 调本地模型总结（失败抛错，由调用方降级）
async function summarize(dateStr, msgs) {
  const prompt =
    `以下是 ${dateStr} 的会话记录。请以「${AI_NAME}」的第一人称，把内容整理成日记，` +
    `每条前面加 [HH:MM] 时间戳（不加日期），时间按升序。保留关键事件、偏好、决定、情绪；` +
    `去掉寒暄和"Conversation info / Sender (untrusted metadata)"等系统元数据。\n\n` +
    `会话记录：\n` +
    msgs.map((m) => `[${m.time}] ${m.role}: ${m.content}`).join("\n");
  const body = JSON.stringify({
    messages: [{ role: "user", content: prompt }],
    temperature: 0.3,
    max_tokens: 2048,
    stream: false,
  });
  const resp = await fetch(MODEL_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
    signal: AbortSignal.timeout(60000),
  });
  const data = await resp.json();
  return data.choices?.[0]?.message?.content || "";
}

// 降级：原始转录
function rawTranscript(msgs) {
  return msgs.map((m) => `[${m.time}] ${m.role}: ${m.content}`).join("\n");
}

// 写 daily（幂等：只写新时间段）
async function write_daily(dateStr) {
  const dailyPath = path.join(DAILY_DIR, `${dateStr}.md`);
  let msgs;
  try {
    msgs = await readSessionsForDate(dateStr);
  } catch (e) {
    logError(`write_daily(${dateStr}) 读会话失败`, e);
    return { skipped: true, reason: "读会话失败" };
  }
  if (msgs.length === 0) return { skipped: true, reason: "无该日会话" };

  const lastTime = await getLastCoveredTime(dailyPath);
  let diff = lastTime ? msgs.filter((m) => m.time > lastTime) : msgs;
  if (diff.length === 0) return { skipped: true, reason: "已覆盖" };

  // 消息数上限保护（2026-09-22）：不再均匀抽样（会丢中间内容）。
  // 改为保留「最后 N 条」—— 保留最近上下文，且明确记录丢弃了多少条。
  // 若模型 context 不足，可调小 RAW_LIMIT。
  const RAW_LIMIT = 400;
  if (diff.length > RAW_LIMIT) {
    const dropped = diff.length - RAW_LIMIT;
    diff = diff.slice(-RAW_LIMIT);
    logError(`write_daily(${dateStr}) 消息过多，已保留最后 ${RAW_LIMIT} 条（丢弃 ${dropped} 条早期消息）`);
  }

  let text;
  try {
    text = await summarize(dateStr, diff);
  } catch (e) {
    // 模型不可用/超时 → 降级原始转录（预期内降级，静默）
    text = "";
  }
  if (!text) text = rawTranscript(diff);

  const header = `# ${dateStr} 日记\n`;
  // 本次覆盖到的最后消息时间（用真实消息时间，不依赖模型输出）
  const coveredUntil = diff[diff.length - 1].time;
  let existing = "";
  try {
    existing = await readFile(dailyPath, "utf8");
  } catch {}
  // 去掉旧的进度标记，再追加本次内容 + 新标记
  const cleanedExisting = existing.replace(
    /<!--\s*daily-autolog:last=\d{1,2}:\d{2}\s*-->/g,
    ""
  );
  const mark = `<!-- ${COVER_MARK}${coveredUntil} -->`;
  const body = cleanedExisting
    ? `${cleanedExisting.trimEnd()}\n\n${text}\n\n${mark}\n`
    : `${header}${text}\n\n${mark}\n`;
  try {
    await mkdir(DAILY_DIR, { recursive: true });
    await writeFile(dailyPath, body, "utf8");
  } catch (e) {
    logError(`write_daily(${dateStr}) 写文件失败`, e);
    return { skipped: true, reason: "写文件失败" };
  }
  return { written: true, lines: text.split("\n").length };
}

// handler（hook pack 默认导出）
async function handler(event) {
  try {
    if (
      event?.type === "command" &&
      (event?.action === "new" || event?.action === "reset")
    ) {
      // A：写今天
      await write_daily(getLocalDateStr(Date.now()));
    } else if (event?.type === "gateway" && event?.action === "startup") {
      // C：补昨天
      const yesterday = getLocalDateStr(Date.now() - 86400000);
      await write_daily(yesterday);
    }
  } catch (e) {
    logError("handler 执行异常", e);
  }
}

export default handler;
