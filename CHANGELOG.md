# Changelog

本文件记录「星忆 / Stellamem」的所有主要更新，按时间倒序。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 语义化版本规范。

## Unreleased - review-fixes（外部代码审查 4 项逻辑修复 + ruff）

来源：第三方代码审查报告（2026-11-03），4 项问题已在 `b0de0c9` 源码中逐条核实属实，
逐项修复并配回归测试（`tests/test_review_fixes_20261103.py`，10 项）。

### 修复

- **WAL 快照不一致**（`compiler/snapshots.py`）：`auto_snapshot()` 对 `.db` 文件改用
  `sqlite3.Connection.backup()` 在线备份——生产 `index.db` 是 WAL 模式
 （`indexer.py: PRAGMA journal_mode=WAL`），此前 `shutil.copy2` 只拷主文件会丢
  `-wal` 里最近提交的数据。非 db 文件保持 `copy2`；manifest 新增 `method` 字段
  注明备份方式；备份失败回退 `copy2`（旧行为）。
- **偏好卡跨主体混入**（`compiler/search.py`）：`recall()` 新增可选参数 `pref_subject`，
  `_match_pref_cards()` 在指定时只匹配该 subject 的卡——此前新偏好卡通道纯按文本
  bigram 匹配、无请求者过滤，老 recall 通道的 namespace 规则在新通道没有等价边界。
  多主体调用方必须传 `pref_subject`；不传 = 不过滤（保持旧行为）。
- **Skill 选择移到提取之前**（`compiler/pipeline.py`）：`extract_chunks` 每个 chunk
  提取**前**调 `select_skills`，选出的 skill `instructions` 拼入 `_extract_group` 的
  system prompt（含递归重试路径），真正指导本次提取；此前是提取后才选、只做事后
  标记。`SkillBank` 不可用时降级为纯 SYSTEM 提取。`docs/MEMSKILL-LITE.md` 补"诚实注记"。
- **使用计数虚高**（`compiler/search.py`）：`usage_tracker.record_hit` 移到 budget
  裁剪循环之后，只对实际进入注入文本的 extras 计数——此前在裁剪前计数，
  被 budget 截掉没注入的卡也被记了命中（`budget_chars=0` 时旧代码会计 2 次）。

### 杂项

- Ruff：`compiler/token_dashboard.py:62` F841（`cols` 未使用，删除）、
  `tests/test_governance_f2_dashboard.py` F401（未用的 `json` import，删除）——
  `ruff check .` 现已全过，CI quality 门恢复绿色。
- 全量测试：528 passed + 37 subtests（基线 518 零下跌）。

## Unreleased - batch2（外部借鉴+超越：Skill 接线 / 偏好卡注入 / 三件套 / 睡眠巩固）

设计文档：`docs/BATCH2-BEYOND.md`；ADR：`docs/adr/0005-sleep-consolidation-text.md`。
把 PR #18/#19/#20 里"刻意没做、列为后续"的项全部实现，外加用户拍板转入
实现队列的 SenseNova 三件套与睡眠巩固（文本版）。

### 新增

- **Skill 运行时接线**（`compiler/pipeline.py`）：提取路径真正接入
  `skills.select_skills`；每个 chunk 结果带 `skills_used`；安全网
  （`evidence_verify` + `secret_gate` + `injection_screen`）默认带上，
  每次选用记审计；保持规则选择，不引入 RL 训练。
- **偏好卡 / 规律项注入 recall**（`compiler/search.py`）：`recall()` 新增可选参数
  `preference_cards`（配额 `pref_quota=2`）、`patterns`（配额 `pattern_quota=1`）、
  `usage_tracker`；注入项走独立匹配通道，只占 `max_items` 剩余额度，
  明确标注 `[偏好]` / `[规律]` 并展示证据链；398 候选域语义与 namespace 规则不变。
- **使用频率回流**（`compiler/usage_signals.py`，新增）：`UsageTracker` 记录偏好卡 /
  规律项被 recall 命中的次数与首末时间，只读看板；只增信号，不碰内容，
  无任何删除 / 降权 / 自动清理路径。
- **SenseNova 三件套**（`compiler/proactive.py`，新增；本地、可审计、可关闭）：
  ①主动提醒（矛盾 / 过期 → pending 草稿，具名确认，无推送路径，不自动打扰）；
  ②跨会话任务接续（纯函数简报，无副作用）；
  ③冲突消解（双方证据并列的冲突报告，具名裁决，只记录不执行）。
  三个开关可独立关闭。
- **睡眠巩固（文本版）**（`compiler/sleep_consolidation.py`，新增）：
  `mine_patterns` 从近 N 期 episode 小结挖反复出现的规律（带 provenance，
  可追溯、可删除）；`PatternStore` 批准 / 撤回须具名人类；
  `pattern_candidates` 得分乘 0.8 权重（低于原始记录）。
  权重版（LoRA/MEMIT）明确不做：API 模型摸不着权重、成本超预算、
  不可逆 vs "能撤回"要求冲突（见 ADR 0005）。

### 测试

- 新增 46 项（`tests/test_batch2_*.py` ×5）；全量 518 passed + 37 subtests，
  基线零下跌；`ruff` 通过；`tools/secret_scan.py` 干净。

---

## Unreleased - attribution（外部致谢表述规范化）

- 将文档与注释中的非正式口语表述全部改写为正式外部致谢：
  `docs/GOVERNANCE-4-FEATURES.md`、`docs/MEMSKILL-LITE.md`、
  `docs/READABLE-MEMORY.md` 新增"外部致谢"小节（来源项目/采用功能/
  采用原因/适配差异四要素），`compiler/skills.py`、`benchmark/pii_audit.py`、
  `docs/LAUNCH_COPY.md`、`CHANGELOG.md`、`PUBLIC-CHANGELOG.md` 同步改写。
  功能代码零改动。

---

## 2026-11-03 - PR #17 / #18 / #20 合入 main（head `0ad18f58`）

更新总览见 `docs/UPDATE-2026-11-03.md`。三项均为外部借鉴、按 Stellamem 治理哲学重做。

### 新增（已合入）

- **4 个治理功能**（PR #17，`c8d20307`，借鉴记忆管家 V4.0.6；详见 `docs/GOVERNANCE-4-FEATURES.md`）：
  写前自动快照 + 一键回滚（`compiler/snapshots.py`）、Token 看板只读版（`compiler/token_dashboard.py`，
  刻意不做自动压实）、90 天时效衰减接入 recall（`compiler/rank.py`，只降排序不删数据）、
  selector-audit 九阶段召回诊断（`compiler/selector_audit.py`）。
- **MemSkill 轻量适配**（PR #18，`19e7ec53`；详见 `docs/MEMSKILL-LITE.md`）：
  Skill Bank（`compiler/skills.py`，11 个命名/版本化/可审计 skill，规则选择）+
  失败案例复盘（`compiler/designer_review.py`，只提议、人工批准才生效，永不自动改）。
- **可读记忆两小件**（PR #20，`0ad18f58`，替代 #19；详见 `docs/READABLE-MEMORY.md`）：
  桥"只传摘要、不搬原文"明文规则（ADR 0004）+ 一等记忆类型 `preference_summary`
 （`compiler/preference_summary.py`，每条陈述强制带证据）。

### 修复

- `rank._timestamp_ms` 不认索引表实际 `occurred_at`/`observed_at` 字段，
  导致真实数据上衰减静默失效（PR #17 顺带修复）。

### 测试

- 全量回归 **472 passed + 37 subtests**，基线全程零下跌；`tools/secret_scan.py` 干净。

### 已合入

- PR #22（`feat/attribution-20261103`，`608acd11`）：外部致谢文档（"偷"表述改为正式致谢）。

### 待合入（draft）

- （无）

### 未验证 / 已知

- 三个已合入 PR 的 GitHub Actions CI 均无状态上报（workflow 权限问题）。
- decay 生产启用、#21 功能的运行时真实数据验证，均需单独确认（未碰生产）。

---

## Unreleased - governance-4-features（借鉴记忆管家 V4.0.6）

设计文档：`docs/GOVERNANCE-4-FEATURES.md`。4 个功能按 Stellamem 架构独立实现，
而非简单复制；铁律：衰减只降可检索性不删除、快照/回滚为显式运维操作、
core 画像与 pinned 记录不参与衰减。

### 新增

- **F1 写前自动快照 + 显式回滚**（`compiler/snapshots.py`）：索引重建、
  approve/reject 等写操作前自动对 `index.db` + 关键配置做文件级快照
  （SHA256 清单）；`restore_snapshot()` 必须传确认短语 `RESTORE` 否则拒绝，
  恢复前校验 SHA256；快照/回滚/清理全部写审计日志；默认保留最近 10 份。
  不绕过 append-only：记忆内容修改仍走事务 + `valid_to`，快照恢复只用于
  运维事故且每次可审计。
- **F2 Token 看板**（`compiler/token_dashboard.py`，只读）：总条数、
  按 namespace/status/kind 分组、token 估算（优先 tiktoken，否则 CJK 启发式
  并标注"估算"）、可配置预算（默认 30000）、三色告警（<70% 绿 / 70–100% 黄 /
  >100% 红）、超预算时给出"建议人工复核的大户清单"。刻意不做自动压实/
  throttle：删除与压实必须走人工复核。
- **F3 90 天时效衰减接入 recall**：`compiler/rank.py` 新增
  `configure_decay(halflife_days, strength)`（默认沿用按 kind 稳定度，
  可设全局半衰期如 90 天；identity/user-core/core 与 `pinned` 记录永不衰减）、
  `search.recall(..., decay=True, explain_decay=False)` 在评分层做
  base_score × retention 重排（只影响排序，不删除条目、不改 status）、
  `/v1/recall` 支持 `explain_decay` 透传调试信息。另修复真 bug：
  `rank._timestamp_ms` 原先不认索引表实际的 `occurred_at`/`observed_at`
  字段，导致真实数据上衰减静默失效。
- **F4 selector-audit**（`compiler/selector_audit.py`，只读）：诊断单条记忆
  在召回链路各阶段的命运——存在性 → namespace 隔离 → status → search 召回
  （score/overlap/semantic）→ min_score → 去重折叠 → top-k → budget → decay，
  输出 verdict（injected / not_in_index / blocked_by_namespace / blocked_by_status /
  not_recalled_by_search / filtered_by_min_score / folded_by_dedup /
  cut_by_top_k / cut_by_budget）与分阶段 JSON。

### 测试

- 新增 33 项：`test_governance_f1_snapshot.py`（8）、
  `test_governance_f2_dashboard.py`（6）、`test_governance_f3_decay.py`（11）、
  `test_governance_f4_audit.py`（8），全合成数据。
- 全量回归：371 / 371 通过（338 基线零下跌）。

---

---

## Unreleased - pref-card-review（偏好卡进入人工复核 + 全量版本历史）

- **偏好卡**（`compiler/preference_summary.py`）：`create` / `update` 不再直接
  `status=active`。新卡和改写后的卡是 `pending`，`list_active()` 看不到，
  直到具名人类 `approve(subject, by)`。`withdraw` 仍追加撤回版本。
- 版本历史按主体只追加：每个版本保留当时的 text、evidence、status。
  `rollback(subject, version, by)` 再追加一条 pending，正文来自该旧版本，
  不改已经写下的记录。审计条目带 `content`（正文与证据），不只是操作和版本号。
- `validate_card` 未写 status 时默认为 `pending`，允许 `pending` / `active` /
  `withdrawn`。

## Unreleased - large diary reliability

- 新增 MemSkill 轻量适配（`compiler/skills.py` + `compiler/designer_review.py`，见 `docs/MEMSKILL-LITE.md`）：Skill Bank 把记忆提取/整理/修剪收敛为命名、版本化、可审计的技能（INSERT/UPDATE/DELETE/SKIP 四原语 + 从现有流程提炼的 8 个领域技能）；Designer 只收集 hard cases 并生成 pending 提案，**永不自动生效**——新增/修改/删除 skill 必须具名人类批准。刻意未做 RL 训练闭环（原因见文档）。
- 可读记忆（Ant International EMNLP 2026 印证 + 补两小件，见 docs/READABLE-MEMORY.md）：
  - 新增 ADR 0004：桥接只传摘要、不搬原文（明文规则 + 人工批准检查清单）；
    `bridge.raw_source_hits()` 噪音过滤器标记疑似原文混入（只提示，不自动拒绝）。
  - 新增一等记忆类型 `preference_summary`（compiler/preference_summary.py）：
    subject/text/evidence/updated_at/version/status；每条陈述强制有证据，
    缺证据拒绝写入 + 记审计；支持更新（version+1）与撤回。
    pref-card-review 起，新建和更新为 pending，批准后才 active，并保留逐版本正文。
  - 未采用 AlignXplore+ 两阶段训练（模型不开源、训练管线太重，文档留了原因）。
- 默认编译分块调整为 2500 字符，llama.cpp 上下文窗口调整为 16384。
- 将 `finish_reason=length` 与连接、超时错误分开；输出截断时递归重切。
- 完整响应若 JSON 无效，原块只重试一次；连续无效再按无损边界重切，到下限仍无效则硬失败，不修补残缺 JSON。
- 删除固定 `text[:6000]` 输入截断和残缺 JSON 补齐路径。
- 同一来源全部 chunk 成功后才事务提交并推进内容游标；失败留下状态供重试。
- 所有重试仍执行 Secret Gate；逐字证据保留消息级 provenance，未核实证据保留 chunk 级 offset 并进入待审核。

---

## [0.2.0-alpha] - 2026-11-02（公开分支，尚未部署到生产）

### 新增

- 时间回放不截断：`as_of()` 默认不再静默截断，新增 `as_of_paged()`（总数与是否截断）。
- 注入门低分拦截、gate 日志轮转（1 MB / 10000 行，最多 3 份）、snapshot 重名检测、append-only 内容保护。
- 主体隔离：`user / persona / viewer`，默认仅同主体检索；桥默认拒绝，仅三种模板、全文人工批准、批量失败整批不通过、单条可撤回、72 小时过期（低置信猜测）。
- `namespace` 权威主体字段（无效 fail-closed）与只内存干跑的补标接口；人格事件 SQLite sidecar（append-only，`export_pack()` 只返回 reviewed 数据）；承诺表 SQLite sidecar；只读 pending 筛查报告。
- 公开清理：删除生产本地配置、全部 `.bak` 文件、内部交接与审计文档与历史目录，真实路径替换为占位路径；公开分支第二提交又修复了 benchmark 中 5 个文件、14 处旧项目路径，并统一版本号标注为 v0.2.0-alpha。

### 未做 / 已知问题

- D4、D5 未修，gate 无生产调用方，接线前必须先修；D1、D3、D7–D13 未修。
- 主体标签只到干跑，未写入真实数据；人格同步到用户库未实现；桥无图形界面；语义检索修复不在本版范围；生产侧 pending 未激活。
- 详见 `PUBLIC-CHANGELOG.md`（含已更新 / 未更新 / 下面还想提升什么）与 README“本版是什么”一节。


## [0.1.0-alpha] - 2026-09-20

### 新增

- 自动日记 hook（daily-autolog）：`/new` 写今天 + 网关启动补昨天 + 幂等进度标记
- 记忆注入 Adapter（OpenClaw）：`before_prompt_build` 注入 core 记忆 + 关键词下钻
- benchmark 评测：30 篇真实日记，召回 0.705 / 精确 0.505 / False Write 0
- memory CLI：只读体检（索引同步 / 冲突检测 / 健康检查 / 版本链校验）
- **AI 自举安装**：`AI_BOOTSTRAP.md` + `INSTALL.md` + `setup.py` 三件套，AI 读完可自行安装
- **可分发安装器**：`setup.py` 支持 `--mem-root` / `--config` / `--oc-root` / `--model-url` / `--dry-run` / `--upgrade` 参数；**不会**修改 `openclaw.json`，只生成配置片段。
- **发布前安全文档**：新增 `SECURITY.md`（安全策略）、`THREAT_MODEL.md`（12 项威胁模型）、`ROADMAP.md`（规划中能力）
- **README 重写为发布版**：Alpha 声明、安全模型（Secret is not Memory / Single Writer / Adapter 边界）、Your Memory Is Yours、Git for AI Memory、退出机制、数据边界

### 改进

- 星忆支持 identity / user-core 类型
- 安全防护：注入 / 反话 / 临时情绪 / 过期检测
- 配置化：路径 / 模型 / 端口抽到 config.json
- 权威数据源收口：旧 MEMORY.md / USER.md 等归档，事实以 core / long-term / semantic 为准
- 安装器不污染源文件：只改拷贝到目标位置的副本，源文件保持模板状态可重复分发
- `openclaw.json` 自动合并：深度合并（只追加 keys、列表去重），自动备份

### 修复

- 修复 session 归档文件读取（`.jsonl.reset.*` 被漏）
- 修复幂等（改用隐藏进度标记，不再依赖模型输出时间戳）
- 修复旧文件污染（MEMORY.md / USER.md 归档为 .bak）
- 修复 sessions 目录探测（优先默认 agent main）
- 修复 memorySearch 位置（对齐真实 openclaw.json 的 agents.defaults 结构）
- 修复安装器更新 hook/adapter 时悄悄删除旧目录的问题（改为先备份旧目录再替换）

---

## 说明

本项目的版本节奏以「阶段 / 任务」推进，而非严格的语义化版本号。
上表按日期归档，后续每个开发日追加一节即可。

---

## 2026-09-22 审计更正

- 评测口径：`False Write 0` 只描述既定对抗样本，不能解释为所有写入正确或真实环境零错误；同次评测还包含 272 条 extra，并受单用户样本与单次 LLM 匹配器限制。
- Schema：稳定 ID 与去重键增加 scope；本地编译器已加入四字段实现和 legacy 读取，78 项 Python 测试与 3 组 JS 测试在合成数据上通过，真实模型仅完成工程性验证（非准确率声明）；安装后的 OpenClaw hook/adapter 尚未在真实宿主端到端验证。
- 模板：补齐编译器和适配器已引用但此前未随安装模板创建的四个 semantic 文件。
