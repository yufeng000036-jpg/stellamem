# 借鉴「记忆管家 V4.0.6」的 4 项治理功能 · 适配设计

来源：https://blog.yeyupiaoling.cn/article/1793315459001（2026-11-03，经用户批准借鉴）
日期：2026-11-03 ｜ 状态：设计→实现→测试→PR

> 适配原则：4 个功能都按 Stellamem 架构独立实现，不是简单复制。
> 铁律：衰减只降可检索性、绝不静默删除；快照/回滚是显式运维操作；
> core 画像与 pinned 记录不参与衰减；所有新功能只读优先、写操作必审计。

## 外部致谢：记忆管家 V4.0.6

- **来源项目**：记忆管家 V4.0.6（`@indiv-aakook/memory-manager-v2`，经
  SkillHub 分发的社区 Skill；介绍：https://blog.yeyupiaoling.cn/article/1793315459001）
- **采用的功能**：写前自动快照 + 一键回滚、Token 看板与预算治理、
  90 天时效衰减、selector-audit（"写了但没被想起来"诊断）四项功能的设计思路
- **为什么采用**：这四项是记忆系统从"能跑"到"可运维"的关键拼图——快照回滚
  解决"改坏了怎么办"，Token 看板解决"记忆吃掉多少成本"，时效衰减解决
  "旧记忆淹没新记忆"，selector-audit 解决"写了为什么想不起来"。
  该项目把它们做成开箱即用的组合，设计非常出色
- **我们的适配与差异**：四项功能均按 Stellamem 架构独立实现。刻意差异有三处：
  ①看板保持只读，超预算只给人工复核建议，不做自动压实/throttle
  （删除/压实必须走人工复核）；②衰减只降检索排序权重，绝不删除、
  不改 status，core 画像与 pinned 记录不参与衰减；③快照恢复是显式运维操作
  （需确认短语 + SHA256 校验），不绕过 append-only。另：实现中发现并修复了
  衰减在真实数据上静默失效的一个 bug（`rank._timestamp_ms` 未识别
  `occurred_at`/`observed_at` 字段）。

---

## F1 落盘前自动快照 + 一键回滚（`compiler/snapshots.py`）

**记忆管家做法**：写入前自动建快照，一键回滚。

**Stellamem 适配**（注意 append-only 语义）：
- Stellamem 已有 `reliability.snapshot()`（整树 zip）和事务级 `undo()`。
  本功能补的是**轻量写前快照**：只对 `index.db` + 关键配置文件
  （`config.json`、`openclaw.json` 除外——只读红线不碰）做文件级快照，
  每次写操作（索引重建、标签写入、pending 落库、approve/reject）前自动触发。
- 快照存 `system/snapshots/<ts>-<label>/`，每个文件记 SHA256，
  快照清单本身也记 SHA256；快照/回滚/清理全部写审计日志。
- `restore_snapshot()` 是显式运维操作：必须传确认短语，否则抛错；
  恢复前先校验 SHA256，不一致拒绝恢复。
- `.db` 只用 `sqlite3.Connection.backup()`。备份失败则失败关闭：抛错、
  不留下快照、写 `snapshot_auto_failed` 审计，**不会**回退到 `shutil.copy2`
  （WAL 下只拷主文件会丢 `-wal` 里的表，打开即 `no such table`）。
  非 SQLite 的配置文件仍用 `copy2`。
- manifest 对 SQLite 快照额外记录 `tables`、`row_count`（`records` 行数）
  和 `latest_record_timestamp`（`observed_at` / `occurred_at` 的较新值）。
- 恢复 SQLite：先确认 live 库和 `-wal`/`-shm` 没有活动连接，否则拒绝且不改文件；
  然后用 `backup()` 把快照写回 live 库（不是 `copy2`），切到 DELETE 日志模式并
  删除 sidecar，使旧 WAL 不会被重放。接着做 `integrity_check`，并核对表、行数、
  最新记录时间。任一失败都把库滚回恢复前的内容。
- **不绕过 append-only**：快照恢复针对的是"运维事故"（如索引重建写坏），
  不是常规记忆修改路径；记忆内容的修改仍走事务 + `valid_to` 过期，
  快照恢复的每一次调用都有审计记录、可追溯。
- 默认保留最近 10 份，`prune_snapshots()` 清理旧份（删快照本身也记审计）。

---

## F2 Token 看板 / 预算治理（`compiler/token_dashboard.py`）

**记忆管家做法**：dashboard 看板、tiktoken 真实计量、三色预算告警、
超预算自动压实、throttle 一键压预算。

**Stellamem 适配**（只读、不改数据）：
- `dashboard(db)` 返回：总条数、按 namespace / status / kind 分组、
  token 估算总量与 Top-N 大户。
- namespace 的来源不是 `records.namespace`（索引表没有这一列，读它会把每条
  都算成未分类）。看板用 `subjects.subject_of()`：若 `meta.root` 指向可读的
  记忆根，则读规范记录上的 `namespace` 字段；否则用该行里实际存在的
  `namespace` / `subject`。对不上已知主体时记为 `(unclassified)`。
- 计量：优先 `tiktoken`（装了就用）；没有则用 CJK 启发式
  （CJK 字符 ≈ 1.5 token，ASCII 按 4 字符 1 token），**输出必须标注"估算"**。
- 预算：`token_budget` 可配置（默认 30000 token）；三色告警
  green <70%、yellow 70–100%、red >100%。
- **不做"自动压实/throttle"**：Stellamem 的记忆删除/压实必须走人工复核，
  工具只给"如果超预算，建议复核这些大户"的只读建议清单。这是与记忆管家
  刻意不同的地方（治理哲学差异），在文档中明确写出。
- CLI：`python -m token_dashboard --db … [--json] [--budget N] [--top N]`。

---

## F3 90 天时效衰减（`compiler/rank.py` 接入 `search.recall()`）

**记忆管家做法**：CJK bigram 相关度 + 90 天时效衰减，旧记忆权重自动下降。

**Stellamem 适配**：
- `rank.py`（遗忘曲线 v1）已存在：`R = 0.5^(days/S)`，按 kind 分稳定度，
  identity/user-core/core 永不衰减。本次工作是**把它接入 recall 评分层**。
- `search.recall(..., decay=True)`：对 search 命中先按 `rank.rank()` 重排
  （base_score × retention），再走去重/top-k/budget。decay 只影响排序，
  **不删除任何条目、不改 status**。
- 可配置半衰期：`rank.configure_decay(halflife_days=90)` 设置全局半衰期覆盖；
  默认 None = 沿用按 kind 的稳定度（preference/relationship 本来就是 90 天）。
- 豁免：`pinned=true` 的记录不参与衰减（常客 pin）；core 画像类已豁免。
- 调试：`recall(..., explain_decay=True)` 时每个 item 带
  `decay={retention, age_days, stability_days}`；`/v1/recall` 支持
  `explain_decay` 参数透传。
- 回退：`rank.configure_decay(strength=0)` 或删调用点即关闭，无残留。

---

## F4 selector-audit（`compiler/selector_audit.py`）

**记忆管家做法**：selector-audit 查候选池就绪度，describe 补可见性。

**Stellamem 适配**：诊断"这条记忆为什么没被召回"，逐阶段输出：
1. 索引中存在？（id/claim_id/revision_id 查找）
2. namespace 隔离是否放行？（`subject_of()` 为 None → `namespace=unknown` 且
   `blocked_by_namespace`；传入的请求方主体不匹配 → 同样拒绝）
3. status 是否为 active？（pending/rejected 被筛掉，注明）
4. `search()` 是否召回？（score / overlap / semantic 分项）
5. `min_score` 过滤？
6. 去重折叠？（同 claim_id / 同值）
7. top-k 截断位置？
8. budget 字符截断？
9. decay 系数？（retention 值）

只读；CLI：`python -m selector_audit --db … --id … --prompt … [--namespace …]`。
`--query` 是 `--prompt` 的别名，`--subject` 是 `--namespace` 的别名
（请求方主体，`user` / `persona` / `viewer`）。未分类记录不会被标成通过。
输出 JSON，便于程序化处理。

---

## 测试策略

- 每个功能独立测试文件：`tests/test_governance_f1_snapshot.py` 等，
  全部用合成数据（placeholder），不碰真实记忆。
- 全量回归：`python -m unittest discover -s tests`，338 基线不得跌。
- 脱敏：`python tools/secret_scan.py` 门禁模式必须干净。
