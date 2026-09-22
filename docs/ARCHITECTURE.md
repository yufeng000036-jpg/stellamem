# 架构说明（Architecture）

## 一句话

把 AI 的对话日记，编译成结构化的长期记忆，白盒存储、可审查、可回滚。

## 核心概念：Claim（原子声明）

记忆的权威形态是结构化 Claim，Markdown 是它的可读呈现。

一条自然语言 → 先编译成 Claim → 再决定写进哪个 Markdown 文件。

Claim 字段：`kind / subject / scope / predicate / value / confidence / evidence / source`

## 三阶段流水线

```
┌─────────┐    ┌──────────┐    ┌─────────┐
│ extract │ →  │  judge   │ →  │  store  │
│ 编译    │    │ 判关系   │    │ 路由入库│
└─────────┘    └──────────┘    └─────────┘
 日记→Claim      NEW/强化/矛盾    core/ long-term/ semantic/
```

1. **extract**：把日记编译成 Claim（9 种类型：preference/decision/lesson/project/fact/relationship/procedure/identity/user-core）
2. **judge**：判断新 Claim 与已有记忆的关系（NEW / DUPLICATE / REINFORCES / REFINES / COMPATIBLE / SUPERSEDES / CONTRADICTS / UNCERTAIN）
3. **store**：按类型路由到对应文件（preference→core/preferences.md，lesson→long-term/lessons.md 等）

## 记忆分层

| 目录 | 权限 | 内容 |
|---|---|---|
| core/ | 严格（人工确认） | identity / user-core / preferences |
| long-term/ | 中等 | projects / decisions / lessons |
| semantic/ | 中等 | 深度画像 / 数据档案 |
| episodic/ | 宽松 | 日记索引 / 月度摘要 |

## 三条宪法

1. **Canonical owner 唯一**：每个事实只有一个权威文件。
2. **Raw source 不改**：原始日记只读，永不修改。
3. **派生可重建**：所有派生记忆可从 raw source 重建。

## 框架无关

编译器（compiler/）不依赖任何具体 AI 框架。接入不同框架靠 adapter：

- `adapters/openclaw/`：OpenClaw 的 before_prompt_build hook 注入
- 其他框架（Claude Code 等）：写对应 adapter

Adapter 契约：`inject_core / inject_recall / healthcheck`。

---

## 2026-09-22 架构更正（追加）

- Claim 的现行字段增加 `scope`；稳定 ID 与去重键均必须包含 scope。
- `core/identity.md` 和 `core/user-core.md` 是人工确认区，编译器不得直接写入。
- confidence 不是经过校准的正确概率，必须与来源、安全、时效和目标敏感度规则共同决定路由。
- 本地代码已加入四字段去重和 legacy 读取并通过 78 项 Python / 3 组 JS 测试（合成数据）；真实模型仅完成工程性验证，真实宿主端到端仍未验证。
- 模型客户端仅允许环回 endpoint（`127.0.0.1` / `localhost`），拒绝远程 API、重定向与系统代理。
- 来源不复制原文，只保存指针与 hash（`daily/*.sources.json`）；指针解析失败时报 `SourceError`，不伪造来源。
- 人工审批闭环：`compiler/inbox.py`（list / show / approve / reject / edit）。
