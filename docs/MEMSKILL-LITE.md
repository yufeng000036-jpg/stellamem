# MemSkill 轻量适配（Skill Bank + Designer 复盘）

> 来源：MemSkill 论文（arXiv:2602.02474，ICML）——"Learning and Evolving
> Memory Skills for Self-Evolving Agents"。
> 适配原则：**偷思想和表示法，不偷训练闭环**。

## 偷了什么

### 1. Skill Bank 表示法（`compiler/skills.py`）

把"怎么记"从散落在各处的硬编码，收敛成命名、版本化、带自然语言描述的
skill 注册表。每个 skill 包含：

| 字段 | 说明 |
|---|---|
| name / version | 唯一名 + 语义版本 |
| description | 一句话说明 |
| when_to_use | 何时选用 |
| instructions | 怎么执行 |
| constraints | 硬约束 |
| triggers | 规则选择的关键词 |
| source | 提炼来源（文件名或 `memskill-primitive`） |
| status | active / deprecated / pending |
| digest | 内容 SHA256（防篡改） |

初始 skill 集 = MemSkill 的 INSERT / UPDATE / DELETE / SKIP 四原语 +
从现有手写流程提炼的 8 个领域 skills：

- `extract_atomic_claims` ← `pipeline.py` SYSTEM（≤6 条、evidence ≤240 字、质量 bar、语言规则）
- `evidence_verify` ← `pipeline.py` 证据核实 + 说话人归因
- `noise_filter` ← `claim_normalizer.py` `is_noise_claim`
- `secret_gate` ← `security.py`
- `injection_screen` ← `security.py` `detect_injection`
- `quarantine_not_delete` ← `quarantine.py`
- `chunk_budget_1200` ← `indexer.py` / `search.py`

选择逻辑是**规则匹配**（task/tags/文本关键词 + 提取任务默认安全网），
**不是 RL**。每次选用记审计日志（`_audit`）。

### 2. Designer 复盘 → 提案（`compiler/designer_review.py`）

论文里的 Designer 会自动进化技能库。本适配**刻意砍掉自动进化**，
Designer 只做两件事：

1. **只读收集** hard cases（`recall_failure` / `wrong_memory` / `incomplete_memory`），输入原样不动；
2. **生成版本化提案**，状态一律 `pending`，写入提案文件。

提案生效的唯一路径：具名人类 `approve`（需确认短语 `APPLY`）→
同一人 `apply`。提案本身版本化、可拒绝、可审计。

## 没偷什么（以及为什么）

**完整的 RL 训练闭环**（Controller 策略训练、LLM 裁判、下游任务标签、
自动进化技能库）明确 out of scope：

- 需要训练基础设施和标注数据，当前阶段投入产出比不划算；
- 自动进化与本项目"AI 起草、人拍板"的治理哲学冲突；
- 论文场景是单 Agent 问答基准，不解决多主体隔离问题。

如果将来要做，先补训练评估集和人工复核流程，再谈。

## 红线（写死在代码里）

1. `SkillBank.register()` / `deprecate()` 没有具名人类批准直接拒绝。
2. `designer_review.apply()` 仅当提案 `approved` 且执行人 = 批准人，否则拒绝。
3. 本模块**不存在** `auto_evolve` / `auto_apply` —— 不是缺失，是设计。
4. 运行时新增的 skill 进 JSON overlay（版本化），内建 skill 的修改走同一条批准路，旧版本进 history。

## 用法

```python
from skills import SkillBank
bank = SkillBank(overlay_path="system/skills.json", audit_path="system/skill-audit.jsonl")
picked = bank.select_skills({"task": "extract", "tags": ["daily"], "text": "..."})
```

```bash
# 复盘 hard cases，生成 pending 提案（只读输入，不碰技能库）
python compiler/designer_review.py review --cases hard_cases.json --proposals system/skill-proposals.json
# 人工批准（需确认短语）
python compiler/designer_review.py approve sp-20261103-001 --proposals system/skill-proposals.json --by 宇枫 --confirm APPLY
# 同一人应用
python compiler/designer_review.py apply sp-20261103-001 --proposals system/skill-proposals.json --by 宇枫
```

## 后续可做（不阻塞本次）

- 把 `pipeline.py` 提取路径接入 `select_skills`（运行时选技能，目前 skills 只是注册表+文档）；
- hard-case 的自动收集（从 selector-audit / 召回失败日志里沉淀）；
- 跨 skill 的组合策略（目前一次选多个，顺序无关）。
