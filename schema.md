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
- **0.75 ~ 0.95**：写入 memory-inbox.md 待确认
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

实现状态：2026-09-22 本地代码已加入四字段去重、四段式 Claim ID 与 legacy 读取，并通过 9 项安全测试；定时任务恢复后的真实编译仍未验证。

---

## 八、2026-09-22 三层记忆保留（追加）

解决「信息不丢」与「隐私可控」的矛盾，按时间分三层：

### 热层（0–14 天）· 全量保留

| 位置 | 内容 | 索引/注入 |
|---|---|---|
| `daily/_raw/YYYY-MM-DD.jsonl` | **全量对话原文**（不截断、不抽样、去系统元数据） | ❌ **永不进索引、永不注入** |
| `daily/YYYY-MM-DD.md` | 摘要（人读用，仍是派生件） | ✅ 可注入 |

**关键：`_raw/` 是「信息无损底座」** —— 旧实现每条只留 150 字符、超 60 条抽样，信息在第一步就丢。现改为：原文全存 `_raw/`，摘要另写 `.md`。

### 温层（14–30 天）· 原样保留

- `_raw/` 原地不动，标记「待蒸馏」，不参与日常注入。

### 冷层（30 天后）· 蒸馏

| 分类 | 去向 |
|---|---|
| 重要（偏好/决策/教训/项目/身份/情绪模式/重要事件） | → 编译器 → `core/` `long-term/` `semantic/` |
| ★收藏（金句/灵感/有趣对话） | → `semantic/highlights.md`（**永久，永不蒸馏**） |
| 中性记录（寒暄/重复确认/一次性操作） | → 只留 hash + 时间戳（**不留摘要**，避免二次损耗） |
| 准释放 | → `semantic/trash-pending.md`（**7 天缓冲，可捞回**）→ 7 天后无异议 → 只留 hash |

### 永不释放（硬规则）

以下内容**无论何时都不进入释放流程**：

- 任何带强烈情绪的原始记录（哪怕是深夜写的一句话）
- 自我怀疑 / 痛苦 / 孤独 / 被遗忘恐惧相关内容
- 创作素材、世界观设定、金句
- 模型判定为「无来源」「不完整」的碎片 —— **无来源 ≠ 无价值**

### 触发

- **定时**：每周日一次
- **手动**：用户说「整理一下」
- **报告**：升 N / 归档 N / 待释放 N / ★收藏 N

### 隐私

- `_raw/` 不进 FTS 索引、不进 Adapter 注入、不被 `memory_search` 搜到
- Adapter 硬排除正则：`(_raw|_原始|archive|benchmark|adversarial)` 与 `(*.bak|*.pre-*|*.reset.*)`
- 敏感类（身份/健康/隐私）升级长期记忆**必须人工确认**

实现状态：2026-09-22 `_raw` 全量落盘已实测（11960 条，最长 3816 字符）、Adapter 硬排除已加、两个模板已就位；`distill.py` 尚未实现。
