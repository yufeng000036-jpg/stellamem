# Changelog

本文件记录「星忆 / Stellamem」的所有主要更新，按时间倒序。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 语义化版本规范。

## Unreleased — 待审自动批准

用户 2026-11-06 亲口要求：不再逐条人工批，什么都不做就该自动批。以前的默认是“显式人工批准才进正式记忆”，人工复核仍随时可用，但不再是唯一入口；上线前已在收件箱里的积压仍需人工处理，不参与自动批准。

### 新增

- **待审自动批准（默认开，宽限期 72 小时）**：干净的 user 主体主张在收件箱里满 `pending_auto_approve_hours`（默认 72）无人操作后，由显式命令 `inbox auto-approve [--dry-run]` 批量批准。宽限期内人工 `approve` / `reject` / `edit` 照常可用。`inbox list` 显示每条的 `pending_since` 与自动批准状态（`eligible` / `in <n>h` / `never: <原因>`，`--json` 另给 `eligible_at`）。
- **可撤回**：`inbox withdraw <id>` 把一条自动批准的记录送回待审；撤回即否决——撤回后该记录不再有 `pending_since`，永远不再自动批准，只有人工能再批它。只对自动批准的记录生效，人工批准的记录不接受撤回。
- **配置**：`config.json` / `config.example.json` 新增 `pending_auto_approve_enabled`（默认 `true`，改 `false` 即关闭）与 `pending_auto_approve_hours`（默认 `72`，可配 1–720 小时），非法值在 `load_config()` 即拒绝。
- **时间戳**：`store.ingest()` 给每条新进收件箱的主张打 `pending_since`（进入收件箱的时刻，不是来源消息时间）。没有这个时间戳的记录（即上线前的全部积压）永远不自动批准；人工 `edit` 不补打也不重置它。

### 不变（仍需人工）

- 桥接跨主体仍默认拒绝、每条全文人工批准（`bridge.py` 未动）；5 个核心身份候选仍不得写入（硬保护文件拒绝未动）。
- 敏感、身份、矛盾（CONTRADICTS / UNCERTAIN / 非 NEW 关系）、未核验证据、含密钥、目标为硬保护文件、槽位已被占用、非 user 主体（persona / viewer / 未分类）的候选永远不自动批准；无法分类为安全的，一律留在收件箱。
- 夜间结算仍只读，不触发自动批准；摄入时自动写（ingest-time auto-write）的窄口径未动。
- 每次自动批准都写进版本链与复核日志（`review_action: "auto-approve"`，含规则与 `pending_since`），并在记录上标 `approved_via: "auto"`；`inbox approve` 的既有行为与输出未变（LongMemEval 的 `worker.py` 调用点未动）。

### 测试

- 新增 `tests/test_pending_auto_approve.py`（27 项）：72 小时边界前后、全部排除项、积压排除、非 user 主体、关闭配置、dry-run、二次扫幂等、审计标记、`withdraw` 往返、配置默认值与非法值拒绝。全量 **798 Python + 18 Node 通过**，`ruff check` 与 `ruff format --check` 全过，`secret_scan.py` 干净，`demo/run_demo.py --offline` 通过。

## 2026-11-06 — v0.4.0 通用客户端版本

方向收束：不为某一个宿主服务，做任何 AI 客户端都能接的记忆治理层。今天的生产宿主是 DSH 桌面助手；接线方式是 MCP（stdio）与 HTTP API，启动时用 `--allowed-subjects` / `STELLAMEM_ALLOWED_SUBJECTS` 绑定主体。README 按此愿景重写（OpenClaw 不再作为主角出现）。

### 新增

- **MCP 主体绑定**（PR #64，合并提交 `4243a37`）：启动时绑定允许集；切到集外主体直接拒绝，集外召回一律空结果、写一律拒绝；非法值启动即报错；不配时保持单人可信行为 + 启动警告。多客户端部署必须配绑定。
- **注入门**（PR #65，合并提交 `1db10e6`）：调用方实体/关键词门，无关问题 budget=0、零注入（MCP 入口 `status: "gated"`）；记忆问题正常放行；HTTP 默认未动；门故障时失败放行。
- **召回兜底**（PR #66，合并提交 `ecb5971`）：JS 加 CJK 二元组信号 + fact 名字意图；Python 空池时意图/时间兜底（标 `fallback` + 弱分）；32 次调用上限未动。
- **Hit 投票**（PR #67，合并提交 `deb0624`）：召回真注入自动记命中；手动票只做补充（赞成票、半权重、无反对票、不下沉）；按主体分开计票；只重排已获准召回内的记录；结算走夜间结算。
- **4K 桌面页**（PR #68，合并提交 `0988108`）：每主体一张、各 ≤4096 字节（超了拒绝、不静默截断）；改写留 diff，删人和约行要批；tmp + rename + 锁 + 14 份备份。
- **日档五段 + 长期**（PR #70，合并提交 `d697716`）：每主体一张日档（触点 / 我的想 / 变 / 我的回 / 回声），长期只记“我变了什么”；合成一律本地规则版，不调模型、不出本机；当天没日记那步直接跳过。
- **夜间结算报数**（PR #63，合并提交 `81c1198`）：每晚报四个数 + 两行状态；只读，失败关闭。
- **后台线程重建**（PR #55，合并提交 `9cba86b`）：运行中索引变旧时后台重建、读请求先返回旧数据；真机 `ensure_index()` 从约 3180 ms 降至 2.7–75.8 ms。
- **并发读锁**（PR #57，合并提交 `c3d9674`）：`Index` 加 RLock，API 读路径持锁；真机压测健康检查 25/25 + 50/50 零断开。
- **文档与验收修复**（PR #52，合并提交 `9ffdcfb`；PR #53，合并提交 `50c552c`；PR #54，合并提交 `368636e`）：加固文档、验收 P1/P3 修复（重启重建、历史修订字段、写入 schema 版本校验）、Windows 文件锁竞态修复。

### 修复

- **PR #69**（合并提交 `89503c7`）：桌面页 CLI 没调 `ensure_utf8_stdout()`，Windows 控制台默认编码下印中文直接炸；修完 CI 7/7 全绿。
- **CI 加固**（PR #56 / #58 / #59 / #60 / #61 / #62）：Ubuntu 3.12 后台重建失败已修（冷却 60→5 秒 + 重试 + stdout 兜底）；Windows 上一个 MCP stdio 测试经验证是 runner 侧原因，已按用户批准在 win32 上跳过该单测，Linux/macOS CI 继续跑它。

### 测量

- **LongMemEval 全量 500 题**（2026-11-06，只用 deepseek-flash，总计 $1.8282）：带记忆判分 0.222 vs 不带记忆 0.060（delta +0.162）；召回@5 0.637（直接塞原文 0.909）；最弱多会话 0.083、时间推理 0.102、单会话助手 0.054；拒答题带记忆臂退步（0.800 vs 1.000）；32 次上限顶穿 25 题（5%）；验收门仍是 CLOSED；不是 full-distractor 官方分数。重跑待 DeepSeek 充值。

### 测试

- 全量 **747 Python + 13 Node 通过**（v0.3.0 时 587 → 本轮 +160），零下跌；`ruff format --check` 全过。注：main 在 `windows-latest, 3.10` 有一次失败正在定位（同跑 Ubuntu 3.10 与 Windows 3.12 都绿、本地全量多次全绿，疑似 runner 侧时序抖动，未定性）。

### 已知限制

- 多客户端隔离的彻底完成正在等评审按最新 main 复核，复核通过之前不对外宣称已完成。
- 召回仍落后于直接塞原文（0.222 vs 0.602）；语义检索生产离线（8082 端口被占用）；生产侧约 3948 条 pending 未激活；DSH 生产部署的应用版本落后于 main，更新任务已排队。

## 2026-11-05 — 安全加固（PR #42–#51 已合入 main，提交 `29f97ce`）

六轮外部审查 + 修复，只修安全边界与兼容性，不加新功能。每个 PR 先经 trial-merge 与全量测试零下跌验证才合入。第三方审查评分 8.8/10（早期 7.2 → 8.3 → 8.4 → 8.6 → 8.7 → 8.8）。Release 仍为 v0.3.0（标签在 `e925378`），补丁版本待发布。

### 新增

- **Scoped Token 权限绑定**（PR #45）：Master Token 不受限（DSH 零改动）；Scoped Token 按 SHA256 存于 `system/api-token-scopes.json`，只读绑定主体，越权请求直接 403。
- **主体别名兼容**（PR #51）：`subjects.py` 新增 `expand_subjects` / `canonical_subject`；查询 `user` 时展开别名命中 `subject="用户"` 的旧记录，索引里保留原始拼写，不批量改写生产记忆。
- **备份恢复回归测试**（PR #51）：`test_backup_restore_roundtrip`——隔离副本备份 → 删除 → 恢复 → 记录一致，服务重启后记忆恢复。

### 修复

- **PR #42**：主体过滤从 `LIKE '%user%'` 改为精确匹配 `subject = ?`，不再误匹配 `superuser`。
- **PR #43**：全部读接口（除 `/v1/health`）要求 Bearer Token 鉴权。
- **PR #44 / #46**：Ruff 格式修复（`compiler/search.py` / `compiler/api.py`）。
- **PR #47**：Scoped Token 三漏洞——`/v1/recall` 过滤 `items` 后未同步重建 `text`（越权主体正文泄露）；写接口只验 Token 不验权限（受限 Token 也能审批、备份、回滚）；`search / timeline` 先取候选再过滤会漏召回（改走 DB 层过滤）。
- **PR #48**：多主体 Token 改用 SQL `IN (授权主体集合)` 过滤，不再"取 3 倍再过滤"（31 条未授权 + 1 条授权时旧策略返回空）。
- **PR #49**：空权限列表 `[]` fail-closed（`persons is None` 才是不限制，空集合返回零结果）；`history()` 补 `subject` 字段，否则受限 Token 的历史记录始终为空。
- **PR #50**：非法 Scoped Token 配置（null / 字符串 / 数字）直接拒绝认证（401），不再返回"不受限"（此前等于拿到 Master 权限）；显式指定主体与授权范围取交集；`budget=0` / `max_chars=0` 真正关闭召回（此前 `or 1200` / `or 2000` 会把 0 恢复成默认）；测试隔离——`setUpClass` 建好索引，`test_history_and_related` 等单独跑不再全空。
- **PR #51**：`stellamem_switch_subject` 明确标注只适用于单人可信部署，不得宣称为不可信多客户端的安全边界；MCP 召回后置过滤用标准命名空间比较。
- **测试假阳性**：`test_dual_subject_no_leak` 与 `test_explicit_subject_intersects_scope` 之前写的新文件不在索引的 MANAGED 清单里，记录根本没进库，测试空跑通过；已改为追加到 `long-term/projects.md` 并加非空断言。

### 测试

- 全量 **599 Python + 8 Node 通过**（v0.3.0 时 587 → 本轮 +12），零下跌；`ruff format --check` 全过；CI 7/7 全绿（Linux/Windows/macOS × Python 3.10/3.12 + 代码质量）。
- 新增测试：`test_token_subject_scope`、`test_recall_text_filtered`、`test_scoped_token_readonly`、`test_dual_subject_no_leak`、`test_empty_scope_returns_nothing`、`test_history_returns_subject`、`test_malformed_scope_config_rejected`、`test_explicit_subject_intersects_scope`、`test_recall_budget_boundaries`、`test_subject_alias_recall`、`test_backup_restore_roundtrip`。

### 已知限制

- 适合单人可信 DSH 环境正式使用；暂不建议开放给互不信任的多客户端，也不建议作为唯一记忆备份。
- OpenClaw SQLite 会话自动采集尚未接通；LongMemEval 尚未跑出结果；Token 成本对照尚未拿到完整账单数据。

## 2026-11-04 — v0.3.0 正式落地

**项目正式落地版本。DSH 桌面助手已完成三阶段部署验收并投入日常运行。**

### 新增

- **MCP 真实召回**（PR #36）：`StellamemMCPServer` 新增可选参数 `index_db_path`，传入索引库路径即接通真实检索；不传则保持 `wired:false` 向后兼容；主体隔离，无效主体 fail_closed。
- **check JSON 校验**（PR #35）：`setup.py check` 对 `.json` 文件额外做 `json.loads` 可解析性校验，损坏文件不再误报 OK。
- **DSH 正式部署**：隔离安装 → MCP 召回验证 → 真实数据接入 → 日常运行（每日健康检查、每周快照备份）。

### 修复

- **PR #37**：MCP 自建 SQLite 连接缺 `row_factory`，导致 `dict(row)` 报错；由 DSH 实测发现，一行修复（`con.row_factory = sqlite3.Row`）验证通过。

### 部署验证

- 阶段一：21 文件隔离安装、check 全绿、虚构测试卡写读一致
- 阶段二：3/3 查询 `wired:true / status:ok / 命中1条 / text 逐字一致`
- 阶段三：备份 1476 文件/768MB（只读）、正式库全新空库、真实偏好写入 3/3 查回一致
- 日常运行：4 条真实记录入库、召回 5/5 正确、每日 23:30 检查、每周 23:45 快照

### 测试

- 全量 **587 passed**，CI 全绿（Linux/Windows/macOS × Python 3.10/3.12）。

## 2026-11-04 — Round 4 加固（PR #31 / #32 / #33 已合入 main）

第四轮硬化模块 + 两轮外部审查修复。主分支评分 7.0 → 7.3。

### 新增

- **文件锁与原子写入**（`compiler/file_lock.py`）：跨进程并发写保护，`atomic_append_jsonl` 修复 torn tail，`atomic_write_text` 保证写入原子性。
- **Markdown 增量缓存**（`compiler/derived_cache.py`）：解析结果缓存，命中时 SHA256 校验防止旧读，mtime/size 不变但内容变化时能正确重解析。
- **记忆去重与冲突识别**（`compiler/memory_purge.py`）：扫描重复记忆、检测语义矛盾（"喜欢→讨厌"），只标记不自动删除。签名用 percent-encode 防分隔符碰撞（cf. Mem0 CVE-2026-116575）；兼容核心 `(subject, predicate, value)` 字段。
- **MCP 接口**（`adapters/mcp/stellamem_mcp_server.py`）：标准 JSON-RPC 2.0 接口，偏好提案真实写入 `PreferenceCardStore` pending（需具名审批激活），支持多主题。
- **版本链**（`compiler/memory_purge.py`）：变更历史追踪，空数据不覆盖非空历史，损坏文件永不覆盖，合并写入不丢历史。

### 修复

- **PR #32**：版本链损坏文件保护——无法解析的 JSON 文件永不覆盖，保留原始字节供恢复。
- **PR #33**：偏好卡多主题——`(subject, topic)` 双键，同一用户可存多张独立偏好卡；`topic` 为 keyword-only 参数向后兼容；`approve`/`withdraw`/`rollback` 保留主题不丢失。

### 测试

- 新增 `tests/test_round4_hardening.py`（10 项）：并发写入、缓存校验、分隔符注入、版本链保护、多主题、MCP 持久化、重启恢复。
- 全量 **587 passed**，CI 7/7（Linux/Windows/macOS × Python 3.10/3.12 + 代码质量）。


## Unreleased - review-round2（外部审查第二轮，快照 / 主体隔离 / 看板 / selector-audit）

来源：对 main `803df0d` 的外部审查。四项均先用失败测试复现，再在根上修复，测试保留为回归。

### 修复

- **WAL 快照失败关闭**（`compiler/snapshots.py`）：`Connection.backup()` 失败时不再
  `shutil.copy2` 活动 WAL 库（那会写出缺表的快照，打开即 `no such table`）。
  失败则抛错、不留下快照，并写 `snapshot_auto_failed` 审计。
- **恢复**（同一模块）：先确认 live 库及其 `-wal`/`-shm` 没有活动连接，再用
  `Connection.backup()` 把快照写回 live 库（不用 `copy2`），然后切到 DELETE
  日志模式并删除 sidecar，避免旧 WAL 被重放。manifest 记录 `row_count`、
  `latest_record_timestamp` 和表名；恢复后做 `integrity_check` 并核对这三项，
  任一失败都把库滚回恢复前。
- **召回主体隔离**（`compiler/search.py`）：偏好卡和规律项使用同一个可信主体
  （`request_subject()` 上下文优先于 `pref_subject`）。主体未知时两条通道都
  返回空，不再混入其他主体。主体不从 prompt 或卡片文本推断。
- **Token 看板 namespace**（`compiler/token_dashboard.py`）：`records` 表没有
  `namespace` 列。统计改为 `subjects.subject_of()`：索引 meta 里能找到记忆根时
  读规范记录上的 `namespace`，否则用行内已有字段。
- **selector-audit**（`compiler/selector_audit.py`）：`subject_of()` 返回 None
  时 namespace 阶段失败，`namespace` 为 `unknown`，verdict 为
  `blocked_by_namespace`。CLI 补上文档里的 `--namespace` / `--subject` 和
  `--query`（`--prompt` 的别名）。

## Unreleased - review-fixes（外部代码审查 4 项逻辑修复 + ruff）

来源：第三方代码审查报告（2026-11-03），4 项问题已在 `b0de0c9` 源码中逐条核实属实，
逐项修复并配回归测试（`tests/test_review_fixes_20261103.py`，10 项）。

### 修复

- **WAL 快照不一致**（`compiler/snapshots.py`）：`auto_snapshot()` 对 `.db` 文件改用
  `sqlite3.Connection.backup()` 在线备份——生产 `index.db` 是 WAL 模式
 （`indexer.py: PRAGMA journal_mode=WAL`），此前 `shutil.copy2` 只拷主文件会丢
  `-wal` 里最近提交的数据。非 db 文件保持 `copy2`；manifest 新增 `method` 字段
  注明备份方式。备份失败回退 `copy2` 的行为已在 review-round2 去掉：
  活动 WAL 库备份失败现在失败关闭，不再文件拷贝。
- **偏好卡跨主体混入**（`compiler/search.py`）：`recall()` 新增可选参数 `pref_subject`，
  `_match_pref_cards()` 在指定时只匹配该 subject 的卡——此前新偏好卡通道纯按文本
  bigram 匹配、无请求者过滤。review-round2 起，不传主体不再放行：没有可信主体时
  偏好卡和规律项都不注入。
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
  明确标注 `[偏好]` / `[规律]` 并展示证据链；398 候选域语义不变。
  主体边界见 review-round2：没有可信主体时偏好卡和规律项都不注入。
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
