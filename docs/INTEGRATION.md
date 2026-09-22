# 接入指南：让 AI 真正读写这套记忆系统

> **最重要的提醒（先读这条）**

这套系统有**两个独立的方向**，缺一个都不会工作：

| 方向 | 组件 | 作用 | 位置 |
|------|------|------|------|
| 📥 **写入** | daily-autolog hook | 把 AI 的对话**自动落盘**成日记 | `hooks/daily-autolog/` |
| 🧠 **编译** | compiler | 把日记**编译**成结构化记忆 | `compiler/` |
| 📤 **注入** | stellamem-adapter | 把记忆**喂回** AI 的上下文 | `adapters/openclaw/` |

**只装 adapter（注入）不装 hook（写入），你的 AI 能"读到"记忆，但它的对话永远不会自动写进来。** 你需要手动写日记，否则整条流水线是断的。

完整流水线（⚠️ 中间「编译」一步目前需人工 / 定时触发，**不是全自动**）：

```
AI 对话 ──(daily-autolog hook)──▶ daily/*.md 日记        [自动]
                                        │
                                        ▼
                              (compiler 编译)            [需手动 / 定时触发]
                                        │
                                        ▼
                        core/ long-term/ semantic/ 记忆  [自动落盘]
                                        │
                                        ▼
                        (adapter 注入) ◀── AI 下一次对话
```

---

## 一、写入侧：装 daily-autolog hook

让 AI 每天聊完自动写日记，不用你手动记。

**文件**：`hooks/daily-autolog/`（`handler.js` + `HOOK.md`）

**步骤**：

1. 编辑 `handler.js` 顶部三个常量：
   - `SESSIONS_DIR`：OpenClaw 会话目录（如 `C:/Users/你/.openclaw/agents/main/sessions`）
   - `DAILY_DIR`：记忆根目录下的 daily 子目录（如 `C:/Users/你/<你的记忆根>/daily`）
   - `TIMEZONE_OFFSET`：时区偏移小时数（如中国 `8`）
   （`handler.js` **不调用模型**，也**不复制对话原文**：它只写 `daily/YYYY-MM-DD.sources.json` 来源指针 + 简短摘要。）

2. 把 `daily-autolog/` 复制到 `~/.openclaw/hooks/`

3. 在 `openclaw.json` 启用：

```json
{
  "hooks": {
    "internal": {
      "enabled": true,
      "entries": {
        "daily-autolog": { "enabled": true }
      }
    }
  }
}
```

4. 重启 OpenClaw。

**验证**：发一次 `/new`，看 `daily/YYYY-MM-DD.md` 有没有被写出来。

---

## 二、编译侧：跑 compiler

把日记编译成结构化记忆（偏好/决策/教训/项目/身份）。

```bash
python compiler/sleep_purify.py --mode review   # 读取来源 → 编译 → 记忆/收件箱
python compiler/inbox.py list                   # 查看待人工确认的候选
```

（`extract.py` 是一个**单独的调试 CLI**，把单篇来源的 Claim 打到 stdout，不参与主流程；主流程统一走 `sleep_purify.py`。）

详见 `docs/QUICKSTART.md`。

---

## 三、注入侧：装 adapter

让 AI 启动就"记得"，每次对话自动读到记忆。

**文件**：`adapters/openclaw/`（`index.js` + `openclaw.plugin.json`）

**步骤**：

1. 编辑 `index.js` 顶部的 `ROOT` 为你的记忆根目录
2. 把 `adapters/openclaw/` 复制到 OpenClaw 的 extensions 目录
3. 在 `openclaw.json` 的 plugins 里启用 `stellamem-adapter`
4. 重启 OpenClaw

**验证**：问 AI「你记得我之前说过什么偏好吗？」——它应该能答出已批准入库的偏好 Claim（未批准的候选在收件箱里，不会注入）。

---

## 常见坑（实测踩过的）

1. **hook 事件名用冒号**：`command:new` / `command:reset` / `gateway:startup`，不是下划线 `gateway_start`（那是 plugin 的 `api.on()` 格式）。
2. **hook 放 `~/.openclaw/hooks/`，不是 `extensions/`**：extensions 是 plugin 目录，hook pack 放错地方永远不触发。
3. **归档文件**：`/new` 后旧 session 变 `xxx.jsonl.reset.<时间戳>`，读会话必须用 `includes(".jsonl")` 而非 `endsWith(".jsonl")`，否则漏读历史消息。
4. **必须开总开关**：gateway 加载 internal hook 的前提是 `hooks.internal.enabled = true`，没有这一项，连内置 hook 都不加载。

---

## 目录总览

```
stellamem/
├── compiler/            编译脚本（日记 → 记忆）
├── hooks/
│   └── daily-autolog/   写入侧：自动日记（handler.js + HOOK.md）
├── adapters/
│   └── openclaw/        注入侧：记忆 → AI 上下文
├── templates/           空记忆结构模板
├── demo/                虚构用户 7 天日记 + 一键演示
├── benchmark/           量化评测
└── docs/                文档
```
