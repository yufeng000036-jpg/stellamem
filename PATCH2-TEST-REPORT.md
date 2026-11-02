# PATCH2 测试报告

- 日期：2026-11-02
- 基线：PATCH1 树（242 项单测通过）
- 范围：仅 staging；生产未触碰
- **单测：266 / 266 通过（失败 0）**
- **手工核对：37 / 37 通过（失败 0）**

## 1. 五项内容与证据

### 1.1 主体标签（`namespace`，不改 `subject`）

- 生产索引只读盘点（不读正文）：4,393 行；已分类 **203 行 = `user` 39 + `用户` 164**；
  **persona / 人格 / viewer / 观众 各 0 行** → 没有需要手工处理的记录；
  未分类 4,190 行（3,082 个不同 `subject` 值）。
- 干跑：`legacy_tags.plan_legacy_tags()` 纯函数，结果为 4,393 行 → `namespace=user`，
  `manual_hold=0`；`changed_anything=False`、`wrote_anything=False`；输入记录前后未变。
- 检索验证（合成数据 + 真实 SQLite 索引）：
  - 旧 `recall()` 结果补 `namespace=user` 后**一条不少**（id 顺序一致，6/6）；
  - 检索候选 30/30 不丢；
  - persona 未桥接时 = 0；
  - 桥接副本只对目标主体可见，原件对另一主体不可见；
  - `namespace` 存在但无效时按未分类处理（fail-closed，不回退 `subject`）。
- 生产索引无 `namespace` 列，证明干跑未改表结构。
- 证据（staging 证据目录，未打包）：`prod-classified-records.md`（含 203 行清单与只读前后
  SHA 对比）。

### 1.2 persona 持久化（SQLite sidecar）

- 事件字段：`event_id`、`subject`、`text`、`created_at`、`status`、`spoken_at`、`source`
  （另有复核人/复核时间）。
- 实测：待确认事件在检索包中不可见；显式确认后才可见；`review()` 人工复核后才进
  `export_pack()`；导出只返回数据，**不写用户库**；重复 `event_id` 拒绝且不覆盖；
  `subject=user` 拒绝；重启后事件仍在；适配器接 store 后同样跨重启。
- 审计日志只追加（recorded / confirmed_spoken / reviewed / export）。

### 1.3 D6 补证（第二阶段中途失败不留半批）

- 实现：`approve_batch()` 第二阶段先快照 `status/approver/approved_at`，任何异常回滚后再抛出。
- 注入测试：第 2 条 `_copy` 抛错、第 2 条 `_audit` 抛错 → 两条请求都回滚为 `pending`、
  `approver=None`、无副本；正常批量仍全部批准。
- 限定：覆盖“可捕获异常”；进程崩溃/断电不在覆盖内（内存态 store 无持久事务），因此不宣称
  “任何情况都不存在半批”。

### 1.4 D4 / D5 调查（只查不修）

- D4：`blocked_weak` 实际统计“全部被拦”，不是 weak=True 的数量；`note` 对非 weak 条目也写
  “都是弱信号”。影响：日志/审计会被误读。当前 gate **无生产调用方**（全树检索仅测试引用），
  零运行时影响。
- D5：会被注入的条目缺 `text` 时 `decide()` 抛 `KeyError`；被拦条目缺 `text` 不崩。
  影响：接线后一次缺字段会让整次注入抛异常。建议“跳过 + 计数 + 日志”，但需接线前决定。
- 证据（staging 证据目录，未打包）：`d4-d5-analysis.md` + 探针 JSON。

### 1.5 清单与配置

- 清单（PATCH1 包）：252 文件 / 251 条；相对原改进包**新增 5、删除 0**：
  `tests/test_patch1.py`、`config.production-local.json`、`CHANGELOG-PATCH1.md`、
  `MANIFEST-NOTES.md`、`PATCH1-TEST-REPORT.md`。
  “247→251 新增 4”是口径差（251 + SHA 自身 = 252，而原包 247 含它自己的 SHA），
  **实际新增 5 个文件**。
- 清单（PATCH2 包）：257 文件 / 256 条；相对 PATCH1 **新增 5、删除 0**：
  `compiler/legacy_tags.py`、`compiler/persona_store.py`、`tests/test_patch2.py`、
  `PATCH2-CHANGELOG-脱敏版.md`、`PATCH2-TEST-REPORT.md`。
- 逐文件核对：无未列文件、无缺失、无哈希不符、无 `__pycache__`/测试现场/隐私文件。
- `config.json` 保持安全默认（相对路径 + 本地端口）；`config.production-local.json`
  不会被自动加载（实测默认解析为相对路径）。

## 2. 测试与核对

| 项目 | 结果 |
|---|---|
| 全量单测 | 266 / 266 通过，失败 0 |
| 其中原有 PATCH1 242 项 | 未改一行，全部通过 |
| PATCH2 新增 | 24 项，全部通过 |
| 手工核对 | 37 / 37 通过，失败 0 |
| 解压副本清单校验 | 256 / 256 通过 |
| 解压副本重跑单测 | 266 / 266 通过 |

## 3. 生产未动证据

- 生产记忆文件数：529，前后一致；
- 生产索引行数：4,393，前后一致；
- 状态分布：active 412 / pending 3,948 / quarantined 33，前后一致；
- 生产索引文件 SHA256：前后一致（值略）；
- 生产代码目录改动计数：前后一致；
- 常驻端口进程 PID：前后一致。

## 4. 交付物

- `PATCH2-CHANGELOG-脱敏版.md`（更新日志，已通过脱敏检查）
- 本报告
- PATCH2 补丁包（zip，SHA256 见交付消息）
