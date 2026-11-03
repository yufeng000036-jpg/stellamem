---
name: daily-autolog
description: "Record source pointers for the day's conversations on /new or /reset, and backfill yesterday on gateway startup. No model call, no transcript copy."
homepage: https://docs.openclaw.ai/automation/hooks
metadata:
  {
    "openclaw":
      {
        "emoji": "📓",
        "events": ["command:new", "command:reset", "gateway:startup"],
        "install": [{ "id": "local", "kind": "managed", "label": "Local managed hook" }],
      },
  }
---

# Daily Autolog Hook（来源指针记录）

**Legacy / opt-in:** the installer leaves this hook disabled. It reads JSONL
files only; SQLite-backed OpenClaw sessions are unsupported. Directory presence
or successful plugin injection does not prove source capture. Enable only after
checking a fresh host message against its source pointer. Existing public exports
and legacy recovery behavior remain available; no host database is modified.

在 `/new` / `/reset` 时，把当天会话的**来源指针**写入 `daily/YYYY-MM-DD.sources.json`，并写一份简短活动摘要到 `daily/YYYY-MM-DD.md`。**这是「写入侧」——没有它，对话就不会进入记忆系统的来源清单。**

## What It Does

- **A（记今天）**：`/new` 或 `/reset` 时，扫描 `SESSIONS_DIR` 下的会话文件，把当天每条 user/assistant 消息记为一个来源指针。
- **C（补昨天）**：网关启动（`gateway:startup`）时，同样处理昨天的日期。
- **不复制原文**：原始对话**不**写进记忆库，只存足以重新定位并校验的指针：
  `session_file`、`entry_index`、`message_id`、`timestamp`、`speaker`、`content_hash`。原文仍留在宿主会话文件里（唯一真相）。
- **幂等**：指针按内容 hash 去重；重复运行只增不改，已存在的指针不会重复写入。
- **不调用模型**：本 hook 不发起任何模型请求，因此不会外发任何内容。

## Output Format

`daily/YYYY-MM-DD.sources.json`（`schema_version: 2`）：

```json
{
  "schema_version": 2,
  "pointers": [
    { "provider": "openclaw", "session_file": "<uuid>.jsonl", "session_key": "<uuid>",
      "message_id": "...", "entry_index": 4, "timestamp": "2026-09-22T09:31:00.201Z",
      "content_hash": "...", "speaker": "user", "precision": "message" }
  ]
}
```

`daily/YYYY-MM-DD.md` 只是一份人读摘要（“N 条来源已索引”），**不是证据**；编译以 `.sources.json` 为准。

## Requirements

- `SESSIONS_DIR`：宿主会话目录（安装器会按 `--sessions-dir` 替换）。
- `DAILY_DIR`：记忆根下的 `daily` 子目录（安装器会替换）。
- **不需要**本地模型服务：本 hook 不做总结。

## 注意

- **事件名用冒号格式**：`command:new` / `command:reset` / `gateway:startup`（不是下划线 `gateway_start`）。
- **归档文件**：`/new` 或 `/reset` 后 OpenClaw 会把旧 session 重命名为 `xxx.jsonl.reset.<时间戳>`，历史消息都在归档里；过滤器用 `\.jsonl(?:\.reset\..+)?$`，否则漏读。
- **来源缺失时**：编译阶段若指针无法解析（文件被移除或内容改变），`source_reader` 会抛 `SourceError` 并且**不消费**该来源；不会伪造原文。
