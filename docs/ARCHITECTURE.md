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

1. **extract**：把日记编译成 Claim（10 种类型：preference/preference_summary/decision/lesson/project/fact/relationship/procedure/identity/user-core；`preference_summary` 为 2026-11-03 新增的一等偏好类型，每条陈述强制带证据）。提取经 Skill Bank（`compiler/skills.py`）按上下文选择记忆技能，安全网默认带上，每次选用记审计
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
- 人工审批闭环：`compiler/inbox.py`（list / show / approve / reject / edit；另有 `auto-approve`：干净的 user 主体主张在宽限期（默认 72 小时）后自动批准，`withdraw`：撤回自动批准的记录）。

---

## 护栏回执与桥访问审计（2026-11-08 追加）

- **Guardrail receipt（`compiler/guardrail_receipt.py`）**：每次护栏判定出一张回执，三件套——护栏 id、判定 class（allow/block/partial/defer）、规则摘要 digest。digest 绑定护栏 id、规则（版本、摘要、输入参数）与判定本身：把 `block` 改标成 `allow` 同样会破 digest。`verify_receipt()` 可由回执自身字段重算验证，无需外部输入；重跑同一判定比对回执即复核。规则改动时 bump `rule_version`，旧规则的回执无法冒充新规则的判定。回执只记录判定，不授权任何动作。目前接在注入门（`compiler/gate.py` 的 `InjectionGate.decide()`）上：返回值、JSONL 日志条目、审计行都带回执。
- **桥双向审计行（`compiler/bridge.py` 的 `BridgeStore.access_audit`）**：桥是主体间唯一通道；每次访问（建请求=写、读出批准副本=读、查请求/列队列=读）都记一行：谁 / 哪个主体 / 读还是写 / 命中还是拒绝。访问日志是追加式、永不回滚：事务保护只快照 `requests` 与生命周期审计，不碰访问日志，被拒绝的访问尝试同样留痕。生命周期审计（创建/批准/拒绝/撤回/过期）记的是决定，访问审计记的是越界与读取；批准本身是决定，只进生命周期审计。`actor` 未显式给出时按角色记：建请求记来源主体、读副本记目标主体、查请求与列队列记 reviewer（GUI 读队列时不记令牌持有人身份，令牌永不进日志）。
