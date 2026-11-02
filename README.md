# Stellamem · 星忆

> **Reliability development branch (not a deployed release):** see [verification evidence](docs/verification/BASELINE.md), [C safety repairs](docs/verification/STAGE_C.md), [simulated-host lifecycle](docs/verification/STAGE_D.md), and [E/F acceptance limits](docs/verification/STAGES_E_F_STATUS.md). Final local regression: 410 Python / 8 Node; CI evidence is attached to the draft PR for the tested SHA. Older counts below describe historical milestones. Memory-quality acceptance and production validation remain incomplete.
>
> **Compatibility:** projection defaults to requester `user` and scope `global`; use explicit `requesterSubject` / `activeScopes` for other namespaces/tasks. An explicit invalid namespace fails closed. Untagged topic-style legacy subjects need deliberate namespace tagging; no automatic migration runs. Generic inbox approval rejects occupied active slots and unresolved conflicts do not project. GUI, nightly, semantic-health, pending-batch and persona-sync modules remain candidate/experimental; they are not wired into production.

> **Give AI memory a Git.** ｜ **给 AI 记忆一个 Git。**

Stellamem 是一个**本地优先、可审计的 AI 长期记忆治理层**。
它让 AI 的长期记忆可以被**查看、溯源、版本化、复核、回滚** —— 而不是变成一个不可见的黑箱。

<p align="center">
  <b>🚧 Alpha（v0.2.0-alpha）</b> ·
  🦞 首个一等公民宿主：OpenClaw ·
  🧩 核心与框架无关 ·
  🔒 本地优先 ·
  📝 Markdown 优先
</p>

<p align="center">
  <a href="README.en.md">English</a> ·
  <a href="docs/QUICKSTART.md">Quick Start</a> ·
  <a href="docs/ARCHITECTURE.md">架构</a> ·
  <a href="docs/PHILOSOPHY.md">理念</a> ·
  <a href="SECURITY.md">安全</a> ·
  <a href="docs/USE_CASES.md">应用场景</a> ·
  <a href="CONTRIBUTING.md">参与贡献</a>
</p>
---

## 本版是什么（v0.2.0-alpha，2026-11-02）

本版是公开版，相对旧主分支 v0.1.0-alpha 的变化全部来自 staging 链路与后续合并：
生产三项版 187 测试 → 改进版 226 测试 → PATCH1 242 测试 → PATCH2 266 测试 →
D4/D5 与步骤 2–7 合并后 338 测试 → D1/D3 合并后 347 测试 →
本可靠性轮次（夜间 scope 分组、桥接令牌加固）后重跑为 **357 / 357 通过**，清单 **203 条**。
第二轮可靠性轮次（Windows 中文输出、密钥扫描误报、桥接安全边界测试）后重跑为 **372 / 372 通过**，清单 **206 条**。
**不是生产部署包**：不含任何真实记忆内容、真实记忆文件、生产索引、生产备份、生产配置。

### 现在已有的功能

**记忆治理核心（沿用 v0.1.0-alpha，并经回归验证）：**
- Claim 结构化断言：`kind / subject / scope / predicate / value / confidence / evidence / provenance`；
- 来源可溯：每条 Claim 带来源指针与内容 hash，来源不可验证时抛错、不伪造；
- 作用域 scope：同一话题在不同场景可有不同偏好，不互相污染；
- 版本演化：`SUPERSEDES` 更新旧值，旧版本保留在版本链里；
- 关系引擎：`NEW / DUPLICATE / REINFORCES / REFINES / COMPATIBLE / SUPERSEDES / CONTRADICTS / UNCERTAIN` 共 8 种；
- 人工复核收件箱（CLI）：敏感、身份、低置信度、可疑内容进入收件箱，人工 `approve / reject / edit`；
- 密钥门禁 Secret Gate：密钥在任何模型调用之前被拦截，不写库、不进日志、不外发；
- 冲突治理：冲突写进冲突日志并保留双方证据，不静默覆盖；
- 事务与回滚：记忆写入是事务，有日志、可恢复、可回滚；
- 上下文投影：只向 Agent 注入当前任务相关的已批准 Claim，并有全局字符预算；
- 只读体检：索引同步、冲突检测、健康检查、版本链校验（`compiler/memory.py doctor`）。

**时间与过期（生产三项版新增）：**
- 事实带 `valid_to / expired_at`，支持 `as_of()` 时间回放；
- `as_of()` 默认不再静默截断，新增 `as_of_paged()`，返回总数与是否截断。

**注入安全（改进版 + PATCH1）：**
- 注入门拦截低分非弱项：字面重叠 0 且语义低于阈值，或综合分极低时不注入；弱信号默认不注入；
- `gate_log.jsonl` 轮转：大小达到 1 MiB（1,048,576 字节）或行数达到 10000 行时轮转，最多保留 3 个旧文件；
- `snapshot()` 打包前重名检测，重复时报错而不是静默覆盖；
- append-only 内容保护：已写入内容不被静默改写。

**主体隔离与桥（改进版 + PATCH1 + PATCH2）：**
- 三个主体 `user / persona / viewer`，默认仅同主体检索，跨主体默认拒绝；
- 桥默认拒绝，仅关系、方法、结果三种结构化模板可走，全文人工批准，带来源与审批人；
- 桥批量批准失败整批不通过，单条可撤回，72 小时过期（该值为低置信猜测，需真实使用数据重定）；
- `namespace` 字段为权威主体字段，无效值 fail-closed，不回退到旧 `subject` 字段；
- 主体标签补标接口只在内存干跑，不写盘、不改现有 `subject` 字段。

**人格事件与承诺（改进版 + PATCH2）：**
- 人格适配器：事件先待确认，确认真正说出口后才可见，新增可选持久化 store；
- 人格事件持久化：SQLite sidecar 只追加事件日志，人工复核后才可导出，不自动写用户库；
  `export_pack()` 只返回 reviewed 数据；
- 承诺表：独立 SQLite sidecar，不混进记忆正文，可创建、凭证据完成、过期。

**筛查与宿主（改进版 + PATCH1）：**
- 只读 pending 筛查报告：只统计、不改状态、不激活；
- OpenClaw Adapter：隔离真实宿主 E2E 通过（首个一等公民宿主）；
- AI 自举安装：`AI_BOOTSTRAP.md` + `INSTALL.md` + `setup.py`，支持 `--dry-run`、幂等安装，
  不覆盖已有自定义文件；离线演示用虚构数据，无需模型。

### 现在没有的功能 / 已知问题（合并前请先读）

- **D4、D5 已合并到 main（经 PR #11 累积分支；单列 PR #5 已被其覆盖，不再单独合并）：** `blocked_weak` 现在只计已标记弱信号的被拦项，新增 `blocked_low_signal / blocked_total` 计数，空结果提示按弱标记/低信号分别表述；缺 `text` 的注入项不再抛 KeyError，缺失按空文本处理。
  但 gate 当前仍无生产调用方，接线到任何生产调用方之前，调用方与回滚方式仍需单独说明，本分支不擅自接线。
- **D1、D3 已合并到 main（PR #12，合并提交 e47b4297…）：** `as_of_paged(limit=None, offset)` 不再丢弃 offset；gate 轮转阈值统一为“达到即轮转”。**D7–D13 仍未修：** 仓库内无其具体审计内容，无法在无细节的情况下修复，见公开日志新节。
- **主体标签只到干跑：** `namespace` 尚未写入任何真实数据；hold 规则已实现并经干跑验证（角色/系统类与含人格/观众字样记录暂缓自动归类），写入真实数据前仍需要全量备份、干跑清单核对、回滚方案与批准人，缺一不可。
- **人格事件同步到用户库未实现：** 同步候选包准备已实现（仅 reviewed 事件、批准要求、批量全有或全无），`export_pack()` 只返回 reviewed 数据；实际写库仍未实现，需另行人工批准的任务才能消费。
- **桥接图形界面已实现并加固：** 环回绑定 + 每服务器随机令牌 + POST 来源校验，终端文本审批仍为兜底；72 小时 TTL 为低置信猜测；关键词黑名单可被改写绕过，真正边界是人工看全文批准。
- **语义检索生产接线未做：** 健康检查、向量缓存、查询后重排、用自有题集评测已实现；生产语义服务曾因端口占用离线是生产环境配置问题，本包不含生产配置修复，生产接线仍需单独确认。
- **生产侧 pending 未激活：** 生产库 3969 条 pending 记录在本版不激活，批量 keep / task / drop
  清理需单独批准，不能顺手激活；5 个核心身份候选未获批准，不得写入。
- **通用 Alpha 限制（沿用旧版）：** 无 GUI、无同步、远程云模型仅环回地址、其他宿主适配未完成、
  Adapter 召回仍偏关键词、本地 4B 模型存在分类漂移、英文临时情绪检测不完整、
  蒸馏 / 保留释放流程尚未实现、尚未经过大规模外部用户长期验证。
- **量化口径：** benchmark 仅为 30 篇日记、单一用户的内部实验数据（recall ≈ 0.705 / precision ≈ 0.505），
  不构成准确率承诺；`False Write = 0` 仅表示在已定义的对抗样本中未观察到该类失败。


## 它解决什么问题

今天 AI Memory 的核心问题**不是「AI 记不住」**。

而是：

> **AI 记住之后，用户不知道它为什么记住、从哪里来的、是不是过期、是不是误解、能不能撤销。**

一个真实场景：

```text
用户：我喜欢简洁回答。

几个月后：
用户：技术问题请详细回答。

传统 Memory：可能覆盖、重复、矛盾，或者静默出错。
```

Stellamem 的处理方式：

```text
Conversation
   ↓
Claim（结构化断言）
   ↓  scope        —— 这条偏好属于哪个场景
   ↓  evidence     —— 原话是什么
   ↓  relation     —— 和已有记忆是什么关系
   ↓  revision     —— 产生哪个新版本
Memory
   ↓
Version history —— 旧版本保留，可回滚
```

> **Memory should be inspectable infrastructure, not invisible magic.**
> 记忆应该是可检查的基础设施，而不是看不见的魔法。

---

## Why Stellamem（为什么值得一看）

- **不是聊天记录备份**：对话是「原始素材」，长期记忆是「提炼结果」，两者不能混为一谈。
- **每条记忆都有来源**：Claim 带 `evidence` + `provenance`；来源无法验证时**报错，不伪造**。
- **支持 scope（作用域）**：同一个话题在不同场景可以有不同偏好，不互相污染。
- **支持版本演化**：`SUPERSEDES` 更新旧值，但**旧版本保留在版本链里**。
- **冲突不消失**：`CONTRADICTS` 会写进冲突日志，**不静默覆盖**。
- **密钥不进门**：密钥在任何模型调用**之前**被拦截。
- **敏感内容不自动写**：敏感 / 身份类记忆进入人工复核收件箱。
- **用户拥有自己的记忆**：看得见、改得动、能拒绝、能删除、能回滚。

---

## 核心理念

| 原则 | 含义 |
|---|---|
| **Memory is not a dump** | 聊天记录不是长期记忆。 |
| **Secret is not Memory** | 凭证永远不应成为长期记忆。 |
| **Sensitive is not Auto-write** | 值得保留 ≠ 自动允许写入。 |
| **Memory must have provenance** | AI 应该知道「为什么相信这件事」。 |
| **Conflict must not disappear** | 冲突不能静默覆盖。 |
| **Users own their memory** | 用户应能看见 / 修改 / 拒绝 / 删除 / 回滚 / 导出 AI 的记忆。 |

> 展开说明见 [`docs/PHILOSOPHY.md`](docs/PHILOSOPHY.md)。

---

## 核心架构

```mermaid
flowchart TD
    A[Agent Conversation] --> B[Source Pointer]
    B --> C[Claim Extraction]
    C --> D[Security Policy]
    D --> E[Relation Engine]
    E --> F[Human Review Inbox]
    F --> G[Canonical Memory]
    G --> H[Context Projection]
    H --> I[AI Agent]
```

记忆单元的生命周期：

```text
Conversation → Claim → Evidence → Scope → Revision → Relation → Memory
```

> 完整说明见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

---

## Stellamem 能做什么

### Claim-based memory（基于断言，而非整段对话）
长期记忆被提炼成结构化 Claim：`kind / subject / scope / predicate / value / confidence / evidence / provenance`。

### Provenance（来源可溯）
每条 Claim 都带来源指针与内容 hash。来源解析失败时**抛错，不编造**。

### Scope（作用域）
不同场景可以有不同偏好：`global` 的「简洁」与 `technical` 的「详细」并存。

### Revision（版本演化）
同一事实可以演进；旧版本进入版本链，不直接消失。

### Relation Engine（关系引擎）
支持 8 种关系：`NEW` / `DUPLICATE` / `REINFORCES` / `REFINES` / `COMPATIBLE` / `SUPERSEDES` / `CONTRADICTS` / `UNCERTAIN`。

### Human Review（人工复核）
敏感、身份、低置信度、可疑内容进入收件箱，人工 `approve` / `reject` / `edit`。

### Secret Gate（密钥门禁）
密钥在**任何模型调用之前**被确定性识别并阻断，不写库、不进日志、不外发。

### Conflict Handling（冲突治理）
冲突写入冲突日志并保留双方证据，**不静默覆盖**。

### Transaction / Rollback（事务与回滚）
记忆写入是一个事务：有日志、可恢复、可回滚，冲突检测不覆盖后续改动。

### Context Projection（上下文投影）
只向 Agent 注入**当前任务相关**的已批准 Claim，并有全局字符预算。

---

## 它不是什么

Stellamem **不是**：

- ❌ ChatGPT Memory 的破解工具
- ❌ 一个聊天记录备份器
- ❌ 云端用户画像平台
- ❌ 全量上传用户私人数据的服务
- ❌ 一个必须依赖 OpenClaw 才存在的项目

**OpenClaw 是「第一个完整验证的宿主」，不是「唯一宿主」。**
核心编译器是框架无关的。

---

## 状态矩阵

| Capability | Status |
|---|---|
| OpenClaw Adapter | ✅ Alpha · 隔离真实宿主 E2E 通过 |
| Claim compiler | ✅ |
| Relation Engine | ✅ |
| Source provenance | ✅ |
| Human Review Inbox（CLI） | ✅ |
| Secret Gate | ✅ |
| Context Projection | ✅ |
| Transaction / Rollback | ✅ |
| Crash recovery | ✅ |
| Local models（llama.cpp 等） | ✅ |
| Remote cloud models | 🚧 暂不支持（仅环回地址） |
| Other agent adapters | 🚧 |
| GUI | 🚧 |
| Sync | 🚧 |

> 状态**严格来自真实实现**。标 🚧 的尚未实现。

---

## 快速开始

```bash
# 1. 环境自检（只读，不改任何文件）
python setup.py check --offline

# 2. 干跑，先看清会创建/修改什么
python setup.py install --mem-root <记忆根目录> --dry-run

# 3. 真安装（幂等，重复跑安全；不覆盖已有自定义文件）
python setup.py install --mem-root <记忆根目录> --config <config.json 路径>

# 4. 跑离线演示（虚构数据，无需模型）
python demo/run_demo.py --offline

# 5. 自检
python compiler/memory.py doctor
```

要求：**Python 3.10+**（核心只用标准库）。Node 仅在启用 OpenClaw 集成时可选。

> 👉 完整步骤见 [`docs/QUICKSTART.md`](docs/QUICKSTART.md)。
> ⚠️ 需要本地模型服务（llama.cpp / Ollama / LM Studio 等），且**只接受环回地址**。

### 用 AI 来安装（不会编程也可以）

Stellamem 内建一条「AI 安装路线」。把仓库交给一个能操作电脑的 AI，让它读
[`AI_BOOTSTRAP.md`](AI_BOOTSTRAP.md) 即可。可以直接把下面这段丢给它：

```text
Clone this repository and read AI_BOOTSTRAP.md.
Install Stellamem in an isolated way.
Do not modify existing memory or agent configuration without asking me first.
Run the built-in checks and report whether installation succeeded.
```

---

## 安全与隐私

简版如下，完整版见 [`SECURITY.md`](SECURITY.md)。

- **Local-first** —— 记忆默认保存在你自己的机器上。
- **不复制原始对话** —— Stellamem 不保存第二份完整 raw conversation；只存来源指针与 hash，原文留在宿主。
- **Secret before model** —— 密钥在发给任何模型之前被检测阻断。
- **Sensitive review** —— 敏感与身份信息需要人工确认，且**永远不自动写**。
- **Provenance or nothing** —— 来源不可验证时，**不伪造**。

---

## 量化效果（Benchmark）

> **Benchmark results are experimental and limited to predefined test sets.**
> 以下是内部版本比较用的实验数据，**不构成准确率承诺**。

- 样本：30 篇日记（单一用户）
- 指标：recall ≈ 0.705 / precision ≈ 0.505
- `False Write = 0` **仅**表示在已定义的对抗样本中未观察到该类失败，**不代表真实环境零错误**

测试条件与局限已完整记录，详见 [`benchmark/README.md`](benchmark/README.md)。

---

## 当前限制（Alpha 的诚实边界）

- **OpenClaw 是唯一完整 E2E 验证的宿主**（核心与框架无关，但其它宿主适配尚未完成）
- **远程云模型未正式支持** —— 模型客户端仅允许环回地址
- **Adapter 召回仍偏关键词** —— 可能漏召回不含同词的自然提问
- **本地 4B 模型存在分类漂移**（kind 判定不够严谨）
- **英文临时情绪检测不完整**
- **蒸馏 / 保留释放流程尚未实现**
- **尚无 GUI**
- **尚未经过大规模外部用户长期验证**

- 第 2 步 hold 规则已在分支阶段完成（干跑验证通过），标签写入真实数据仍未做。

### 本版新增的已知问题（2026-11-02，见上文“本版是什么”一节）

- D4、D5 已合并到 main，gate 当前仍无生产调用方，接线前仍需单独确认；
- D1、D3 已合并到 main；D7–D13 因审计内容缺失仍未修；
- 主体标签只到干跑，未写入真实数据，hold 规则已实现并经干跑验证；
- 人格事件同步实际写库未实现，桥接图形界面已加固，72 小时 TTL 为低置信猜测；
- 语义检索健康检查 / 缓存 / 重排已实现，生产接线未做；
- 生产侧 3969 条 pending 未激活，核心身份候选未获批准不得写入。

这些透明项是设计的一部分，不是缺陷隐瞒。

---

## Roadmap

### Alpha（当前）
- 可靠性打磨
- 召回质量
- 外部测试

### Beta
- 更多宿主 Adapter
- 语义检索
- 迁移稳定性
- 复核体验改进

### Future
- 桌面 GUI
- Companion 集成
- MCP
- 加密同步
- 团队记忆
- 企业治理

详见 [`ROADMAP.md`](ROADMAP.md)。

---

## 参与贡献

欢迎参与：见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。
> ⚠️ 任何与记忆行为相关的修改，**必须附回归测试**。

安全问题请勿公开提交 Issue，按 [`SECURITY.md`](SECURITY.md) 的渠道联系。

---

## Vision

未来的 AI 会存在数年甚至更久。如果一个 AI 真正长期陪伴、工作或服务一个人，它就需要回答：

- 我为什么知道这件事？
- 这是用户什么时候说的？
- 现在还成立吗？
- 后来有没有改变？
- 两条记忆冲突怎么办？
- 用户能不能撤销？
- 敏感内容是否应该调用？

Stellamem 希望成为 **AI 长期记忆的治理基础层**。

**OpenClaw 是第一站，不是终点。**

---

## License

MIT © 宇枫 · 见 [`LICENSE`](LICENSE)
