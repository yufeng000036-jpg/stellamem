# Stellamem · 星忆 — Launch Copy

> 本文是项目对外发布文案合集。所有内容基于事实表，不包含未实现能力的承诺。
> 仓库：https://github.com/yufeng000036-jpg/stellamem ｜ 作者：宇枫 ｜ 许可：MIT

---

## A. 一句话版本 (One-Liner)

**中文**

> Stellamem · 星忆 —— 一个本地优先、可审计、可追溯、可回滚的 AI 长期记忆治理层。
> 给 AI 记忆一个 Git。

**English**

> Stellamem is a local-first, auditable, traceable, rollback-capable long-term memory
> governance layer for AI agents. Give AI memory a Git.

---

## B. 30 秒介绍 (30-Second Intro)

**中文**

> 现在的 AI「记忆」大多是把聊天记录存下来，下次原样塞回去。问题是：记的多不等于记得准，
> 改过之后说不清，而且不该记的东西也被记了。
>
> Stellamem（星忆）换了个思路——不存聊天，管记忆。对话先变成**来源指针**（只记在哪说的，
> 不复制原始转写），再抽成带出处的**主张（claim）**；密钥在任何模型调用之前就被拦截，
> 敏感和身份类记忆必须人工审核才写入；新旧记忆冲突时记 `CONTRADICTS` 而不是静默覆盖，
> 改动可以回滚。
>
> 记忆最终落地为本地 Markdown 文件——你能打开、能改、能删。核心只用 Python 标准库，
> 框架无关；OpenClaw 是目前唯一完整验证过的宿主。
>
> 现在是 **v0.2.0-alpha，不是生产可用**。已有 78 个 Python 测试、5 组 JS 测试、
> 关系引擎、崩溃恢复、密钥闸门、审核收件箱、适配器投影，以及一次真实模型跑通
> （只验证工程行为，不构成准确率声明）。
>
> MIT 许可，欢迎早期测试者。

**English**

> Most "AI memory" today means storing chat logs and pasting them back next time.
> That gives you three problems: more isn't the same as better, changes are
> untraceable, and things that shouldn't be remembered get remembered anyway.
>
> Stellamem takes a different route — it doesn't store conversations, it governs
> memory. A conversation becomes **source pointers** (a note of where something was
> said, not a copy of the transcript), which are extracted into **claims** that carry
> provenance. Secrets are blocked *before* any model call. Sensitive and identity
> claims are never auto-written — they go to a human review inbox. When new memory
> conflicts with old memory, it is logged as `CONTRADICTS` rather than silently
> overwritten, and changes can be rolled back.
>
> Memory lands as local Markdown files: you can read it, correct it, delete it.
> The core uses only the Python standard library and is framework-agnostic;
> OpenClaw is the only fully validated host so far.
>
> This is **v0.2.0-alpha — not production ready**. 78 Python tests, 5 JS test groups,
> a relation engine, crash recovery, a secret gate, an approval inbox, adapter
> projection, and one real-model run (engineering behaviour only — not an accuracy
> claim).
>
> MIT licensed. Early testers welcome.

---

## C. 长版项目介绍（800–1500 字）

### 为什么要做 Stellamem

AI 越来越会说话，却越来越不记得你。

这不是模型不够聪明的问题，而是**架构里从来没有「记忆」这个东西**。上下文窗口是临时工位，
不是长期档案。所谓「AI 记忆」的常见实现，本质是把历史对话存起来、下次原样塞回去。

这条路走不通，原因有三：

**第一，记的多不等于记得准。** 原始对话里混着事实、情绪、玩笑、一次性的猜测。
全部回灌上下文，噪音会淹没信号，模型反而更容易被无关信息带偏。

**第二，改过之后说不清。** 记忆被新信息覆盖后，没人知道原来是什么、为什么改、
能不能退回去。对工程决策、人际关系、长期偏好来说，这种「静默覆盖」是灾难。

**第三，不该记的也被记了。** 密钥、证件号、隐私细节，一旦进了记录就很难清理干净。

所以问题不是「怎么存更多」，而是「**怎么治理记忆**」。

### 思路：把记忆当成需要审计的资产

Stellamem（星忆）的出发点是：记忆应该是**可治理的**——可追溯、可审核、可回滚、
可删除。它的口号是「给 AI 记忆一个 Git」，因为 Git 恰好解决了同一类问题：
谁改的、改了什么、能不能退回去。

管线是这样走的：

```
对话
  → 来源指针（source pointer：记录「在哪儿说的」，不复制原始转写）
  → 主张抽取（claim extraction）
  → 安全策略（secret gate + 敏感/身份分级）
  → 关系引擎（relation engine）
  → 人工审核收件箱（review inbox）
  → 规范化 Markdown 记忆（canonical markdown memory）
  → 上下文投影（context projection，按任务相关性注入）
```

一条**主张（claim）**携带 `kind`、`subject`、`scope`、`predicate`、`value`、
`confidence`、`evidence`、`provenance` 八个字段。它不是「一句话」，而是
「一个有出处的断言」，可以被判断、被比较、被追溯。

**关系引擎**判断新主张与已有记忆的关系：
`NEW`（新增）、`DUPLICATE`（重复）、`REINFORCES`（强化）、`REFINES`（细化）、
`COMPATIBLE`（兼容）、`SUPERSEDES`（取代）、`CONTRADICTS`（冲突）、
`UNCERTAIN`（不确定）。

关键是 **`CONTRADICTS` 不删东西**。当新信息和旧记忆打架，系统记下冲突、
进入日志、等人裁决，而不是让模型自己决定谁对。配上事务回滚与崩溃恢复，
记忆状态可以退回上一个已知点。

### 安全设计：三道闸门

1. **密钥闸门在模型之前。** 密钥类内容在**任何模型调用发生之前**就被检测并拦截。
   不是「先给模型看再清理」，是根本不让它进入这条链路。
2. **敏感与身份类记忆从不自动写入。** 它们会进入人工审核收件箱，由人决定要不要记住。
3. **来源指针解析不了就抛错。** 系统宁可报错，也不编造一个看起来合理的出处。
   没有 provenance 的「记忆」不是记忆，是幻觉。

此外，上下文投影只注入**与当前任务相关的、已批准的主张**，并设有全局字符预算——
避免记忆把上下文窗口挤满。

### 技术结构

- **核心框架无关**，只用 Python 标准库。
- **适配器是插件式的**。OpenClaw 是目前**唯一完整验证过的宿主**，但架构上并不绑定它。
- **存储是 Markdown-first 的**：记忆就是本地文件，可读、可 diff、可版本控制。
- **仅支持 loopback（本机）模型服务**，不支持远程云模型。

### 当前状态（Alpha）

已通过验证的部分：**78 个 Python 测试、5 组 JS 测试**，涵盖关系引擎、崩溃恢复、
来源指针、密钥闸门、审核收件箱、适配器投影，以及一次真实模型运行
（**只说明工程行为跑通，不构成任何准确率声明**）。

已知限制，如实列出：

- OpenClaw 是唯一完整验证过的宿主。
- 不支持远程云模型，仅 loopback。
- 适配器检索是关键词匹配，**可能漏召回**。
- 本地 4B 模型存在 **kind 分类漂移**。
- 英文临时情绪识别**不完整**。
- **蒸馏与保留策略未实现**。
- **没有 GUI**。
- **尚无外部长期用户验证。**

### 未来方向

优先级大致是：把适配器检索做得更稳 → 补上蒸馏与保留策略 → 完善分类准确性 →
扩展更多本地后端与宿主适配器 → 再考虑 GUI。

### 如何参与

项目是 MIT 许可，正处于最需要「真实使用反馈」的阶段。最有价值的贡献包括：

- 报告具体案例：「这条不该被记住」或「这条被漏掉了」。
- 报告分类错误，尤其是本地模型下的 kind 判断。
- 参与关系引擎的边界讨论。
- 描述你的场景需求，帮助判断优先级。

仓库：https://github.com/yufeng000036-jpg/stellamem

---

## D. GitHub Release Notes — Stellamem v0.2.0-alpha

> **这是给早期测试者的 Alpha 版本。不是生产可用软件。**
> This is an Alpha for early testers. Not production ready.

### Highlights

- **Claim-based memory model.** 记忆以**主张**为单位，携带
  `kind` / `subject` / `scope` / `predicate` / `value` / `confidence` /
  `evidence` / `provenance`。
- **Relation Engine.** 支持 8 种关系：`NEW`、`DUPLICATE`、`REINFORCES`、
  `REFINES`、`COMPATIBLE`、`SUPERSEDES`、`CONTRADICTS`、`UNCERTAIN`。
- **Source pointers.** 对话进入管线时记录来源指针，而不是复制原始转写。
- **Human review inbox.** 敏感与身份类主张进入审核收件箱，由人批准后写入。
- **Context projection.** 按任务相关性把已批准主张投影回 Agent 上下文，
  并受全局字符预算约束。
- **Markdown-first canonical memory.** 记忆落地为规范化的本地 Markdown。

### Safety

- **Secret gate runs before any model call.** 密钥类内容在模型调用之前被检测并拦截。
- **Sensitive + identity claims are never auto-written.** 一律走人工审核。
- **Conflicts never silently overwrite.** 冲突被记录（`CONTRADICTS`），不静默覆盖。
- **Provenance or nothing.** 来源指针无法解析时**抛出异常**，不编造出处。
- **Transactions.** 支持回滚与崩溃恢复。

### Architecture

- Core is **framework-agnostic**, **Python standard library only**.
- **OpenClaw is the first fully validated host** — not the only possible host.
  适配层为插件式设计。
- **Loopback-only model support.** 不支持远程云模型。
- Pipeline: conversation → source pointers → claim extraction → security policy →
  relation engine → human review inbox → canonical markdown memory → context
  projection.

### Tests

- **78 Python tests**
- **5 JS test groups**
- Covered areas: relation engine, crash recovery, source pointers, secret gate,
  inbox approval, adapter projection.
- **One real-model run** — this validates **engineering behaviour only** and is
  **not an accuracy claim**.

### Current Limitations

- Only **OpenClaw** is fully validated as a host.
- **Remote cloud models are not supported** (loopback only).
- Adapter retrieval is **keyword-based** and can **under-recall**.
- The local **4B model shows kind-classification drift**.
- English **temporary-emotion detection is incomplete**.
- **Distillation / retention is not implemented.**
- **No GUI.**
- **No external long-term user validation yet.**

### What's next

- Improve adapter retrieval reliability.
- Implement distillation and retention policy.
- Address kind-classification drift.
- Additional local backends and host adapters.
- (Later) a GUI.

---

## E. 社区发布文案

### E1. 中文技术社区版（严肃技术口吻）

**标题：Stellamem 星忆 — 把 AI 记忆做成可审计、可回滚的治理层（v0.2.0-alpha）**

现在讲「AI 记忆」的项目不少，主流做法是把历史对话存起来、下次拼回上下文。
我们在项目里没走这条路，原因很具体：原始对话不适合直接作为长期记忆的存储单位。

原因有三点：一是对话里事实与噪音混在一起，回灌会稀释信号；二是覆盖式写入
让变更不可追溯、不可回滚；三是密钥与隐私内容会被无差别记录。

Stellamem 的做法是把「记忆」建模成一等对象——**claim（主张）**，
携带 `kind` / `subject` / `scope` / `predicate` / `value` / `confidence` /
`evidence` / `provenance`。新主张进入系统时，由**关系引擎**判断它与已有记忆的关系，
共 8 种：`NEW`、`DUPLICATE`、`REINFORCES`、`REFINES`、`COMPATIBLE`、
`SUPERSEDES`、`CONTRADICTS`、`UNCERTAIN`。关键约定是**冲突不静默覆盖**——
`CONTRADICTS` 会被记入冲突日志，交由人工裁决。

安全方面有三道机制：**密钥闸门在任何模型调用之前生效**（不是先给模型看再清理）；
**敏感与身份类主张从不自动写入**，一律进人工审核收件箱；**来源指针无法解析时抛错**
而不是编造 provenance。

架构上，核心**框架无关、只用 Python 标准库**，适配器为插件式。
**OpenClaw 是目前唯一完整验证过的宿主**——不是唯一可能的宿主。
存储 Markdown-first，便于 diff 与版本控制。仅支持 loopback 模型服务，不支持远程云模型。

**验证情况**：78 个 Python 测试、5 组 JS 测试，覆盖关系引擎、崩溃恢复、来源指针、
密钥闸门、审核收件箱、适配器投影；另有一次真实模型运行（仅验证工程行为，
不构成准确率声明）。

**已知限制（请认真读）**：宿主仅验证 OpenClaw；不支持远程云模型；适配器检索基于
关键词、**可能漏召回**；本地 4B 模型存在 **kind 分类漂移**；英文临时情绪识别不完整；
**蒸馏/保留策略未实现**；**无 GUI**；**尚无外部长期用户验证**。

**当前是 v0.2.0-alpha，明确不是生产可用。** 欢迎早期测试者提交反面案例，
MIT 许可。

### E2. 普通 AI 用户版（少术语，「为什么 AI 总记不住我」）

**标题：AI 为什么总记不住我？我们做了个小工具试着解决**

有个体验你可能也有过：

你昨天跟 AI 讲过的事情，今天它又忘了。或者更烦的——它记倒是"记住"了，
但记的是错的、过时的，你还改不了。再或者，它把你随口一提的玩笑当成了真事。

为什么？因为现在大部分 AI 的「记忆」，其实就是把聊天记录存起来，
下次原样读回去。聊天记录里什么都有：认真说的话、随口说的话、开玩笑、
一时的情绪……全塞回去，它就分不清哪些是重要的。

**Stellamem（星忆）** 换了个做法。它不存聊天，而是**从聊天里提炼出「你是什么样的人」**
这一条条小结论，每一条都带着出处——这句话是什么时候说的、在什么情况下说的。
这样它比较容易分得清「你今天心情不好」和「你讨厌这个工作」的区别。

它还有三个特点，是给普通人的：

- **能看见，能改。** 记忆存在你电脑上的普通文件里，你能打开看，能修改，能删掉。
- **敏感的东西有保护。** 密码、身份证这类内容在还没送进 AI 之前就被拦下来了，
  不会被记住。涉及隐私的事情，AI 不会自己偷偷记，得你同意。
- **记错了能退回去。** 如果它记错了或者记旧了，可以改，也可以回退到之前的状态，
  不是一改就永远改不回来。

说实话的话：**现在还是早期测试版（Alpha），不好用，别指望它能完美工作。**
比如它有时候会判断错「这句话是哪类记忆」，而且目前只在一款软件上完整验证过。

但如果你也烦「AI 记不住我」这件事，可以来看看，提提意见。
早一点的反馈，对这类工具特别有用。

作者：宇枫 ｜ 许可：MIT ｜ 仓库：https://github.com/yufeng000036-jpg/stellamem

### E3. 开发者版

**标题：Stellamem — local-first memory governance for AI agents (claim / provenance / relation / rollback)**

If you've built agent memory before, you know the default pattern: persist
transcripts, replay them into context, hope for the best. It breaks in ways that
are hard to debug — stale facts, silent overwrites, secrets that end up in the
store.

Stellamem models memory as first-class, auditable objects.

**Claims.** Each memory unit is a claim with `kind`, `subject`, `scope`,
`predicate`, `value`, `confidence`, `evidence`, `provenance`. Not a sentence —
an assertion with an origin.

**Provenance is mandatory.** Conversations enter the pipeline as **source
pointers** (a record of where something was said, not a copy of the raw
transcript). If a source pointer can't be resolved, it **raises** — no fabricated
origins, ever.

**Relation engine.** Incoming claims are compared against existing memory across
8 relations: `NEW`, `DUPLICATE`, `REINFORCES`, `REFINES`, `COMPATIBLE`,
`SUPERSEDES`, `CONTRADICTS`, `UNCERTAIN`. The important one is `CONTRADICTS`:
conflicts are **logged, never silently overwritten**.

**Secret gate before model calls.** Secret-class content is detected and blocked
*before* any model invocation. Sensitive and identity claims are never
auto-written — they route to a human review inbox.

**Transactions.** Rollback and crash recovery are supported.

**Local-first, framework-agnostic.** Core is Python standard library only.
Adapters are plug-in. **OpenClaw is the first fully validated host**, not the
only possible one. Storage is Markdown-first. **Loopback models only** — remote
cloud models are not supported.

**Verified:** 78 Python tests, 5 JS test groups — relation engine, crash
recovery, source pointers, secret gate, inbox approval, adapter projection —
plus one real-model run (engineering behaviour only, not an accuracy claim).

**Known limitations, stated plainly:** OpenClaw is the only fully validated host;
loopback-only; adapter retrieval is keyword-based and can **under-recall**; the
local 4B model shows **kind-classification drift**; English temporary-emotion
detection is incomplete; **distillation/retention not implemented**; **no GUI**;
**no external long-term user validation yet**.

`v0.2.0-alpha` — **Alpha, not production ready.** MIT. Feedback and counter-
examples are the most useful contribution right now.

https://github.com/yufeng000036-jpg/stellamem

### E4. AI Companion 方向版（长期认识一个人）

**标题：如果 AI 要长期认识一个人，它需要的不只是「记住」**

注：**这只是 Stellamem 的一个使用方向，不是它唯一或全部的目标场景。**

我们在做的项目 Stellamem（星忆）是一个 AI 记忆治理层。最近想得比较多的，
是「AI 陪伴」这个方向——如果 AI 要长期认识一个人，技术上到底缺什么。

缺的不是「存得下」。存得下很容易。缺的是三件事：

**一是分得清。** 你今天心情不好，和你本质上讨厌这份工作，是两回事。
原始对话里这两句话看起来很像，直接存下来就会混。Stellamem 的做法是
把它们变成不同的**主张**，带上出处和置信度，让「一时的情绪」和「持续的偏好」
不在同一个抽屉里。

**二是变得了。** 人会变。三个月前的想法，现在可能已经不一样了。
系统不能把旧的那条直接删掉——那等于把这个人走过的路抹了。
它应该记录「新的取代了旧的」，同时保留旧的和当时的原因。
Stellamem 用 `SUPERSEDES` / `REFINES` 和版本链做这件事，改动可以回滚。

**三是交得出控制权。** 陪伴关系里，记忆是很私密的东西。
所以 Stellamem 的约定是：记忆存在本地普通文件里，**可查看、可修正、可删除**；
密钥类内容在送进模型之前就被拦下；涉及隐私和身份的部分**不会自动记住**，
要经过人同意。用不用、记不记、删不删，决定权在人手里。

还有一条设计原则，在「陪伴」语境里格外重要：
**冲突不许消失**。当 AI 的新理解和老记忆打架，它应该记下这个矛盾、
甚至带着不确定继续和你相处，而不是自己挑一个答案、把另一个抹掉。
会犹豫的陪伴，比假装无所不知的陪伴更值得信任。

诚实说明现状：项目是 **v0.2.0-alpha，早期测试版**。
「共同经历」这类叙事化的长期沉淀**还没有实现**，目前是主张级的治理，
不是叙事级的记忆。而且它现在只在一款软件上完整验证过。

但方向是清楚的：**记忆要能被治理，关系才有连续性。**
如果你也在想 AI 陪伴这件事，欢迎来聊。

作者：宇枫 ｜ 许可：MIT ｜ 仓库：https://github.com/yufeng000036-jpg/stellamem
