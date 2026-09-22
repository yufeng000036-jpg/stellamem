// Stellamem Adapter — index.js（A+B 混合注入）
// 每轮注入 core/ 三文件 + 精简索引；命中关键词时额外注入对应档案全文。
// 实测确认的 OpenClaw 格式：export default { register(api) { api.on("before_prompt_build", ...) } }
//
// 记忆根目录解析优先级（2026-09-20 依据 docs/plugins/hooks.md:87）：
//   1) plugin config 的 coreDir —— 官方路径 event.context.pluginConfig
//   2) 安装器写入的 ROOT（兜底，保证旧版/未配置时仍可用）

import { readFile } from "node:fs/promises";

const ROOT = "<YOUR_MEMORIES_PATH>";

// 解析记忆根目录：优先 plugin config 的 coreDir，其次安装时写入的 ROOT。
// 注意目录分隔符：config 里可能用反斜杠，统一成正斜杠后再拼接。
function resolveRoot(event) {
  const cfg = (event && event.context && event.context.pluginConfig) || {};
  const coreDir = cfg.coreDir;
  if (typeof coreDir === "string" && coreDir.trim()) {
    // coreDir 可能指向 <记忆根> 或 <记忆根>/core，都兼容
    const normalized = coreDir.trim().replace(/\\/g, "/").replace(/\/+$/, "");
    if (normalized.endsWith("/core")) {
      return normalized.slice(0, -5);
    }
    return normalized;
  }
  return ROOT;
}

// 精简索引（各档案一行，让 AI 有全局观）
const INDEX_TEXT = [
  "- 项目：进行中的项目状态",
  "- 决策：关键决策、方向选择",
  "- 教训：踩坑经验（弹窗、路径坑、编码坑等）",
  "- 画像：用户基本信息 / 人格 / 关系模式",
  "- 数据画像：聊天数据规模 / 风格",
  "- 收藏：收藏分类",
  "- 技术档案：技术文档索引（工作流 / 拆解 / 工具链）",
].join("\n");

// ── 注入保护参数（可用 plugin config 覆盖）──
const DEFAULT_MAX_CHARS = 4000; // 单个文件最大注入字符数
const DEFAULT_SENSITIVE_POLICY = "summary"; // summary | full
const DEFAULT_SUMMARY_LINES = 10; // summary 模式下注入前 N 行
// 敏感文件识别（画像 / 数据画像 / 收藏 等个人深度档案）
const SENSITIVE_RE = /profile|data-profile|favorites/i;

// 读取注入策略（plugin config 优先，其次默认值）
function resolvePolicy(event) {
  const cfg = (event && event.context && event.context.pluginConfig) || {};
  const maxChars =
    Number.isFinite(cfg.maxInjectChars) && cfg.maxInjectChars > 0
      ? cfg.maxInjectChars
      : DEFAULT_MAX_CHARS;
  const policy =
    cfg.sensitiveFilesPolicy === "full" ? "full" : DEFAULT_SENSITIVE_POLICY;
  const summaryLines =
    Number.isFinite(cfg.summaryLines) && cfg.summaryLines > 0
      ? cfg.summaryLines
      : DEFAULT_SUMMARY_LINES;
  return { maxChars, policy, summaryLines };
}

// 按策略预处理注入内容：敏感文件截摘要 + 统一长度上限。
// 任何情况下都不静默丢内容：截断处显式打 [truncated] 标记，提示 AI 去读原文件。
function prepareContent(text, filePath, { maxChars, policy, summaryLines }) {
  let out = String(text == null ? "" : text);
  // 1) 敏感文件：默认只注入前 N 行
  if (SENSITIVE_RE.test(filePath) && policy !== "full") {
    const lines = out.split("\n");
    if (lines.length > summaryLines) {
      out =
        lines.slice(0, summaryLines).join("\n") +
        `\n\n[truncated: 敏感档案仅注入前 ${summaryLines} 行，完整内容请用 read 读原文件 ${filePath}]`;
    }
  }
  // 2) 统一长度上限
  if (out.length > maxChars) {
    out =
      out.slice(0, maxChars) +
      `\n\n[truncated: 超出 ${maxChars} 字符上限，完整内容请用 read 读原文件 ${filePath}]`;
  }
  return out;
}

// 关键词映射（命中才读全文）
const KEYWORD_MAP = {
  "项目|进展|开发|在做|完成|active": ["long-term/projects.md"],
  "决策|决定|选了|版本|迁移|阈值": ["long-term/decisions.md"],
  "教训|踩坑|报错|错误|避免|以后别|经验": ["long-term/lessons.md"],
  "用户|我是谁|关于我|了解我|人格|关系|身体": ["semantic/profile.md"],
  "聊天数据|消息|画像数据": ["semantic/data-profile.md"],
  "收藏|存档": ["semantic/favorites.md"],
  "技术|工作流|拆解|工具链|插件": ["semantic/tech-index.md"],
};

const plugin = {
  id: "stellamem-adapter",
  name: "Stellamem Adapter",
  description: "把 core/ 记忆 + 索引 + 关键词命中档案 结构性注入 AI 上下文",
  register(api) {
    api.on(
      "before_prompt_build",
      async (event) => {
        try {
          // 记忆根目录：优先 plugin config 的 coreDir，其次安装器写入值
          const root = resolveRoot(event);
          const core = root + "/core";
          const policy = resolvePolicy(event);

          // 1) core 三文件（常驻）
          const identity = prepareContent(
            await readFile(core + "/identity.md", "utf8"),
            "core/identity.md",
            policy
          );
          const userCore = prepareContent(
            await readFile(core + "/user-core.md", "utf8"),
            "core/user-core.md",
            policy
          );
          const prefs = prepareContent(
            await readFile(core + "/preferences.md", "utf8"),
            "core/preferences.md",
            policy
          );

          // 2) 关键词触发（读用户消息，命中才读对应档案）
          const userMsg = (event && event.prompt) || "";
          const extra = [];
          for (const [pattern, files] of Object.entries(KEYWORD_MAP)) {
            if (new RegExp(pattern).test(userMsg)) {
              for (const f of files) {
                try {
                  extra.push(
                    prepareContent(await readFile(root + "/" + f, "utf8"), f, policy)
                  );
                } catch (e) {
                  // 单个档案失败不阻断主流程，但必须留下不含正文的诊断。
                  try {
                    (api.log?.warn || console.error)(
                      `[stellamem-adapter] 读取档案失败，已跳过 ${f}: ${e?.message || e}`
                    );
                  } catch {}
                }
              }
            }
          }

          return {
            prependSystemContext: identity,
            prependContext: [
              userCore,
              prefs,
              "--- 记忆索引 ---",
              INDEX_TEXT,
              extra.length ? "--- 相关档案 ---\n" + extra.join("\n\n") : "",
            ]
              .filter(Boolean)
              .join("\n\n"),
          };
        } catch (e) {
          // fail-soft：注入失败返回空、不阻塞 prompt；但绝不能静默吞错。
          try {
            (api.log?.warn || console.error)(
              `[stellamem-adapter] before_prompt_build 失败，已降级跳过注入: ${e?.stack || e?.message || e}`
            );
          } catch {}
          return {};
        }
      },
      { timeoutMs: 3000 }
    );
  },
};

export default plugin;
