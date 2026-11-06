# 记忆 Schema（Claim 规范）

> 核心原则：**存 Claim，不存句子。**
> 记忆的权威形态是结构化的原子声明（Claim），Markdown 是它的可读呈现。
> 一条人类自然语言 → 先编译成 Claim → 再由 Python 决定写进哪个 Markdown 文件。

## 一、Memory Types（记忆类型）

| 类型 | 含义 | 例 |
|---|---|---|
| preference | 用户喜欢/不喜欢的长期倾向 | 「回复简洁」 |
| decision | 某个选择 + 理由 + 状态 | 「某工具 用 vX.Y.Z」 |
| lesson | 踩过的坑 + 原因 + 方案 | 「yt-dlp 被 412 拦截」 |
| project | 进行中的项目状态 | 「视频剪辑项目」 |
| fact | 客观信息 | 「用户是自由职业者」 |
| relationship | 人与人的关系 | 「好兄弟是游戏搭子」 |
| procedure | 可复用的操作步骤 | 「音乐下载方法」 |

## 二、Claim 字段（以 preference 为例）

```yaml
kind: preference        # 记忆类型
subject: user           # 主语（谁）
predicate: response_length   # 谓语（哪个维度）
value: concise          # 值
scope: global           # 作用域
confidence: 0.94        # 置信度 0~1
evidence: "最近发现我越来越喜欢回答短一点"   # 原始证据（溯源）
source: daily/2026-09-20.md                # 来源文件
```

## 三、稳定 ID 规范

格式：`{kind缩写}.{subject}.{scope}.{predicate}`

- `pref.user.response_length` → 「用户回复长度偏好」
- `dec.tool.version` → 「工具版本决策」
- `lesson.platform.download-limit` → 「某平台下载限制教训」

**作用**：同一个作用域内、同一个语义的不同表述会落到同一稳定 ID；不同 scope 默认兼容并存。

## 四、Relation Types（7 + 1 种关系）

| 关系 | 含义 | 例 |
|---|---|---|
| NEW | 全新，无关联 | 第一次记录某偏好 |
| DUPLICATE | 完全重复 | 同一句话又说一遍 |
| REINFORCES | 强化已有（证据 +1） | 再次确认同一偏好 |
| REFINES | 细化（后者是前者的子集） | 深圳 → 深圳南山 |
| COMPATIBLE | 兼容并存 | 出差杭州 ≠ 搬家 |
| SUPERSEDES | 取代（新覆盖旧） | 搬到杭州，覆盖深圳 |
| CONTRADICTS | 矛盾 | vX.1.0 vs vX.2.0 |
| UNCERTAIN | 不确定，挂起 | 无法判断关系 |

**关键**：「住深圳」vs「家在深圳南山」不是 conflict，是 **REFINES**；「搬到杭州」才是 **SUPERSEDES**。冲突日志（conflict-log.md）只承接 CONTRADICTS 这一种。

## 五、自动写权限（分级）

| 目录 | 自动写权限 | 说明 |
|---|---|---|
| episodic/ | 宽松 | 日记索引、月度摘要可自动写 |
| long-term/projects.md | 中等 | 项目状态可自动更新 |
| long-term/lessons.md | 中等 | 新教训可自动追加 |
| semantic/ | 中等 | 画像可自动整合 |
| core/preferences.md | 严格 | 仅高置信度（≥0.95）自动写 |
| core/user-core.md | **禁止自动写** | 每次拟写入文本均须用户逐字确认 |
| core/identity.md | **禁止** | 防止自我强化环 |

## 六、置信度阈值（可校准；不适用于两个核心只读文件）

- **≥ 0.95**：自动写入对应文件
- **0.75 ~ 0.95**：写入 memory-inbox.md 待确认。其中干净的 user 主体主张（证据已核验、PRIVATE、关系 NEW、无敏感无冲突）在收件箱里满宽限期（默认 72 小时）无人操作后，由 `inbox auto-approve` 自动批准；上线前已在收件箱里的积压没有 `pending_since` 时间戳，永远不自动批准。
- **< 0.75**：只保留在 daily/episodic，不进长期记忆

---

## 记忆宪法（Constitution）

1. **Canonical owner 唯一**：每个事实只有一个权威文件，不允许多处并存。
2. **Raw source 不改**：原始日记/对话只读，永不修改、永不删除。
3. **派生可重建**：所有派生记忆必须可从 raw source 重建，删掉可再生成。

---

## 七、2026-09-22 Schema 更正（追加）

本节优先于上文旧稳定 ID、去重键、自动写权限和统一置信度阈值：

- Claim 必须包含 `scope`；稳定 ID 为 `{kind缩写}.{subject}.{scope}.{predicate}`。
- 确定性去重键为 `(kind, subject, scope, predicate)`。不同 scope 默认兼容并存，不得互相覆盖。
- 历史三段式 ID 作为 legacy 保留，不自动重写现有记忆。
- `core/identity.md` 与 `core/user-core.md` 均为默认只读。任何自动提取、路由、合并和恢复都不得直接修改；每次拟写入文本必须由用户逐字确认。
- 模型自报 confidence 只是一个输入信号，不能解释为经过校准的真实正确率。自动写入还必须通过来源、说话人、引用/反话/注入、时效性和目标敏感度检查。

实现状态：2026-09-22 本地代码已加入四字段去重、四段式 Claim ID 与 legacy 读取，并通过 78 项 Python 安全/集成测试（合成数据）；真实模型已做工程性验证（非准确率声明）；真实 OpenClaw 宿主编译仍未验证。

---

## 八、来源保留（更正：指针，不是原文副本）

> ⚠️ **2026-09-22 更正**：本文件早期版本描述了一个 `daily/_raw/YYYY-MM-DD.jsonl`
> 「全量对话原文」归档层。**该归档层在当前实现中并不存在，也不是当前设计。**
> 保留原文副本既放大了隐私面，也从未可靠落盘。现行方案如下。

现行方案 **不复制原始对话**：

| 位置 | 内容 | 索引/注入 |
|---|---|---|
| `daily/YYYY-MM-DD.sources.json` | **来源指针清单**（session 文件、entry 序号、message id、时间戳、说话人、content hash） | ❌ 永不注入 |
| `daily/YYYY-MM-DD.md` | 简短活动摘要（人读用，**不是证据**） | ❌ 不注入 |
| 宿主 canonical session（OpenClaw） | **原文唯一来源，原地保留** | 由宿主管理 |

**关键点**：真相在宿主会话文件里，不在 Stellamem 里。Stellamem 只保存足以**重新定位并校验**原文的指针与 hash。

编译时（`compiler/source_reader.py`）按指针回到会话文件读取正文，并逐条校验
`content_hash`、`timestamp`、`speaker`、`message_id`。校验不通过就**抛 `SourceError`，不消费、不伪造来源**。

指针失效时的降级行为（明确且安全）：

- 找不到原文件 → 报错，不崩溃，不编造内容
- 内容被改（hash 不符）→ 报错，保留已有的 evidence 摘要与 hash
- 会话被 `/new` 轮转或改名 → 在同一会话范围内做**有界**搜索（`session.jsonl*`）

### 保留与释放

保留分层是**未来档位**，当前代码只实现了核心编译与人工审批闭环；下列归档/释放流程
（`distill.py`、`semantic/trash-pending.md` 自动流转）**尚未实现**，属于路线图项：

| 分类 | 计划去向 |
|---|---|
| 重要（偏好/决策/教训/项目/身份/情绪模式/重要事件） | → 编译器 → `core/` `long-term/` `semantic/` |
| ★收藏（金句/灵感/有趣对话） | → `semantic/highlights.md`（永久） |
| 准释放 | → `semantic/trash-pending.md`（缓冲后可捞回） |

### 永不自动删除（硬规则）

以下内容**无论何时都不进入自动释放流程**：

- 任何带强烈情绪的原始记录
- 自我怀疑 / 痛苦 / 孤独 / 被遗忘恐惧相关内容
- 创作素材、世界观设定、金句
- 模型判定为「无来源」「不完整」的碎片 —— **无来源 ≠ 无价值**

释放动作一律需要人工确认；`inbox reject` 只是把候选标记为 rejected，**不删除任何原始文件**。

### 隐私

- Adapter（`adapters/openclaw/index.js`）只注入 `PUBLIC`/`PRIVATE`、`source=user`、`evidence_verified=true` 的单条 Claim，且按相关性最小注入；legacy 自由文本、身份与敏感内容一律不注入
- 敏感类（身份/健康/隐私）升级长期记忆**必须人工确认**

### 触发（计划）

- **定时**：每周日一次（尚未实现）
- **手动**：用户说「整理一下」
- **报告**：升 N / 归档 N / 待释放 N / ★收藏 N
