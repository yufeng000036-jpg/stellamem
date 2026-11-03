# Batch2：偷来 + 超越（2026-11-03）

第二批"偷来"的功能：把之前各 PR 里"刻意没做、列为后续"的项全部实现，
外加用户拍板转入实现队列的 SenseNova 三件套与睡眠巩固（文本版）。

分支：`feat/batch2-beyond-20261103`（基于 main `0ad18f5`，含 PR #17/#18/#20）。

## 做了什么（5 项）

### 1. Skill 运行时接线（`compiler/pipeline.py` 接入 `skills.select_skills`）
- PR #18 当时刻意没做（"pipeline.py 提取路径实际接入 select_skills"列为后续）。
- 现在：`extract_chunks` / `compile_messages` 在每个 chunk 提取前按上下文选 skill，
  结果里带 `skills_used`；提取任务默认带上安全网
  （`evidence_verify` + `secret_gate` + `injection_screen`），每次选用记审计。
- 保持规则选择，不引入 RL 训练；`SkillBank` 不可用时降级为"无 skill 记录"，
  提取流程本身不受影响。

### 2. `preference_summary` 接入 recall / 注入（`compiler/search.py`）
- PR #19/#20 当时刻意没做。现在 `recall()` 新增可选参数：
  `preference_cards`（配额 `pref_quota=2`）、`patterns`（配额 `pattern_quota=1`）、
  `usage_tracker`。
- 注入项走**独立匹配通道**（bigram overlap），只占 `max_items` 剩余额度；
  sqlite 候选域（398）与 namespace 规则原样不动；注入项明确标注
  `[偏好]` / `[规律]` 并展示证据链（偏好卡证据条数 / 规律项 provenance 期数）。

### 3. 使用频率回流为质量信号（`compiler/usage_signals.py`，新增）
- PR #20 当时列为"后续可做"的轻量版压力测试。
- `UsageTracker`：记录每条偏好卡 / 规律项被 recall 命中的次数与首末时间，
  可选 JSON 持久化；`report()` 只读看板按命中倒序。
- 铁律：只增信号，不碰内容 —— 模块没有任何删除 / 降权 / 自动清理路径；
  "被想起的频率"是参考信号，不是质量判决，解读权在人。

### 4. SenseNova 三件套（`compiler/proactive.py`，新增）
- 路线图已拍板：①主动提醒 ②跨会话任务接续 ③冲突消解。
- ① **主动提醒**：新记忆与旧记忆矛盾（同 subject+predicate 不同值）、
  或记忆 `valid_to` 已过但仍为 active → 生成 pending 提醒草稿；
  需具名人类 `confirm` / `dismiss`。**没有任何推送 / 发送 / 自动执行路径**，
  不自动打扰。
- ② **跨会话任务接续**：`session_briefing(open_items, last_summary)` 纯函数，
  拼出"上次进展"简报；不读库、不写库、无副作用。
- ③ **冲突消解**：`detect_conflicts` 生成双方证据并列的冲突报告（pending）；
  `adjudicate` 只记录人的裁决（`keep_new` / `keep_old`）并给出
  `suggested_action`，**不自己改记忆** —— 调用方在自己的批准流程里执行。

### 5. 睡眠巩固（文本版）（`compiler/sleep_consolidation.py`，新增）
- 港中大 / 浙大 Memo 论文的"值得做的"：保留检索式情景记忆，加一条异步巩固通道。
- 论文原版走模型权重（LoRA / MEMIT / TTT）；我们走文本（原因见下）。
- `mine_patterns(episodes, min_episodes=2)`：同一 (subject, predicate, value)
  在 ≥N 个不同 episode 反复出现 → pattern 候选，永远带 provenance
  （来源 episode 列表），可追溯、可删除；`PatternStore.approve` /
  `withdraw` 必须具名人类。
- recall 权重约定：`pattern_candidates` 得分乘 `PATTERN_WEIGHT=0.8`（<1），
  规律项参与 recall 但权重低于原始记录；只收 `status=active` 的。

## 为什么睡眠巩固是文本版而不是权重版（用户已拍板）

1. 人格跑的是 DeepSeek API，模型权重摸不着；
2. 本地微调要 GPU 时间 + 数据管线 + expertise，成本超预算；
3. 权重写入不可逆 —— "撤回某一条记忆"在权重里是未解难题，
   与用户"能查能改能撤回"的要求直接冲突。
等以后本地小模型 + LoRA 成本下来再谈权重那条路。

## "比原版好"体现在哪

| 对比 | 原版 | 我们 |
|---|---|---|
| vs MemSkill | Designer 自动进化 skill；RL 选技能 | Designer **只许提议、人工批准**（红线写死）；规则选择 + 全程审计 |
| vs SenseNova 6.8 | 云端黑盒 API（私人记忆送第三方 + 花钱） | 本地运行；每项可单独关闭；全程审计；**不自动打扰** |
| vs 记忆管家 | 自动压实 / 自动清理 | 坚持人工复核；使用频率只做信号，不做判决 |
| vs Ant 可读记忆 | 只说"可读" | 加了"**每条陈述强制带证据**"（缺证据拒绝写入 + 记审计） |
| vs Memo 论文 | 权重巩固（LoRA/MEMIT） | 文本版睡眠巩固：可追溯、可删除、可撤回，符合治理要求 |

## 测试

- 新增 46 项（`tests/test_batch2_*.py` ×5），全量 518 passed + 37 subtests，
  基线（472）零下跌。
- `ruff check` / `ruff format` 通过（本分支新增与修改的文件）；
  `tools/secret_scan.py` 干净。
- 未验证：GitHub Actions CI（合并前看一眼）；运行时真实数据接入（按铁律未碰生产）。
