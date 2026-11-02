# PATCH2 更新日志（脱敏版）

## 1. 版本、日期、基线、目标

- 版本：PATCH2
- 日期：2026-11-02
- 基线：PATCH1 树，**242 项单测通过**（不基于更早的 226 版改进包重做）
- 目标（只有这五项）：
  1. 主体标签：新增 `namespace` 字段、不改现有 `subject`；先盘点生产索引已分类记录，
     再对既有记录干跑标 `namespace=user`；
  2. persona 持久化：SQLite sidecar 只追加事件日志，重启后事件仍在、不自动写用户库；
  3. D6 补证：证明批量批准第二阶段中途失败时不留半批；
  4. D4/D5 先查清再定修不修（`blocked_weak` 口径、缺 `text` 时 gate 的行为）；
  5. 清单：核对从 247 到 251 新增的文件、确认无多余；包内 `config.json` 保持安全默认，
     生产配置不得自动加载。
- 范围：仅 staging。生产记忆、生产索引、生产代码、端口均未触碰。

## 2. 更新了什么（按文件）

| 文件 | 改了什么 | 为什么改 |
|---|---|---|
| `compiler/subjects.py` | `namespace` 作为权威字段：存在且有效即采用；存在但无效按未分类处理（不回退 `subject`）；没有 `namespace` 的旧记录仍按 `subject`/旧拼写识别 | 旧代码把自由文本 `subject` 当命名空间会误合并主体；无效 namespace 回退 `subject` 会让可见性悄悄改变 |
| `compiler/legacy_tags.py`（新增） | 纯函数干跑补标：`classify_record()`、`plan_legacy_tags()`（计数/计划哈希/人工处理清单）、`tagged_records()`（仅内存加 `namespace`） | 需要先看清“会改哪些行”，而不是直接写库；本模块不打开记忆目录/索引，不做任何写入 |
| `compiler/persona_store.py`（新增） | SQLite sidecar：`persona_events`（event_id/subject/text/created_at/status/spoken_at/source + 复核字段）+ `persona_event_log`（只追加）；状态机 `pending_confirmation → confirmed → reviewed`；`export_pack()` 只返回 reviewed 数据 | 上一版 persona 事件只存在内存里，重启即丢；且同步必须有人工复核闸，不能自动写用户库 |
| `compiler/persona_adapter.py` | 新增可选 `store=` 注入：接 store 时 record/confirm/pack 走持久化，并新增 `review_spoken()`、`export_reviewed()`；不接 store 时行为与 PATCH1 完全一致 | 让适配层可持久化，同时保持向后兼容（原有内存态调用不受影响） |
| `compiler/bridge.py` | `approve_batch()` 第二阶段改为事务式：先保存每条请求的 `status/approver/approved_at`，中途任何异常回滚全部字段再抛出 | D6：原实现第二阶段中途失败会部分落地；需用失败路径证明“不留半批” |
| `tests/test_patch2.py`（新增） | 24 项测试：namespace 语义/干跑/检索一致性 9、persona store 7、adapter 持久化 3、bridge 事务 3、其他 2 | 为上面五项提供可重复验证；不改动任何原有测试 |
| `SHA256SUMS.txt` | 重新生成：text-mode、LF、相对路径、无 `*./` 前缀；排除清单见 `MANIFEST-NOTES.md` | 旧清单是改进轮之前生成的（存在不符/幽灵条目/未列文件），不能作为交接凭据 |
| `MANIFEST-NOTES.md` | 更新生成方式、排除项与校验说明 | 让清单可复现、可校验 |
| `README-交接.md`、`STAGING-TEST.md` | 同步测试数（266）、PATCH2 核对项、配置安全说明 | 文档与实际不一致会误导接手人；无代码行为变化 |
| `PATCH2-TEST-REPORT.md`、`PATCH2-CHANGELOG-脱敏版.md` | 交付文档 | 记录验证结果与回滚方法 |

说明：`config.json` 未改（保持 PATCH1 的安全默认：相对路径 + 本地端口）；生产机配置仍在
`config.production-local.json`，不会被自动加载。`config.example.json` 未改。

## 3. 与之前有什么不同（只写行为/接口差异）

| 阶段 | 单测数 | 行为/接口差异 |
|---|---|---|
| 生产三项版（上线轮） | 187 | 事实双时间戳、索引加两列、`as_of()` 时间回放；`as_of()` 默认 `limit=100`（静默截断）；注入门只拦 `weak=True`；`gate_log.jsonl` 只增不减；`snapshot()` 无重名检测；无主体命名空间/桥接/承诺/待审报告 |
| 改进版（226） | 226 | P0：`as_of()` 默认不截断 + 新增 `as_of_paged()`；`gate.is_low_signal()`（要求 overlap 与 semantic 同时存在）+ 日志轮转 + `snapshot()` 重名检测。P1（新增边界，未接线）：`subjects.py`、`bridge.py`、`persona_adapter.py`（内存态）、`promises.py`。P2：`pending_report.py`（只读计数） |
| PATCH1（242） | 242 | `gate.is_low_signal()` 覆盖“只带一个信号字段/数字字符串”的条目（D2）；`bridge.approve_batch()` 去重 + 第二阶段一次性落地（D6 初版）；`config.json` 改为安全默认、生产配置移到 `config.production-local.json`；清单重新生成并排除隐私文件 |
| PATCH2（266） | 266 | 新增 `namespace` 字段（权威、无效 fail-closed）；新增补标干跑接口（不写盘）；persona 事件 SQLite 持久化 + 可选 store 注入 + 人工复核/导出接口；`bridge.approve_batch()` 第二阶段失败回滚（有失败路径测试）；清单改为 text-mode；D4/D5 仅出调查结论（未改行为） |

接口差异要点：
- `subjects.subject_of()`：`namespace` 存在时优先；无效 namespace 不再回退 `subject`。
- 新增 `legacy_tags` 模块（`plan_legacy_tags()` 返回计数/计划哈希/人工处理清单；`tagged_records()` 只返回内存副本）。
- `persona_adapter.PersonaAdapter(store=None)`：新增可选参数；新增 `review_spoken()`、`export_reviewed()`；默认行为不变。
- 新增 `persona_store.PersonaStore`（record/confirm_spoken/review/pack/export_pack/log/list_*）。
- `bridge.approve_batch()`：返回值不变，失败时状态回滚（语义收紧）。
- 测试入口不变：`python -m unittest discover -s tests`。

## 4. 没动什么

- 生产记忆、生产索引、生产代码、生产端口：未读正文、未复制、未写入、未重启。
- 未激活任何 pending 记录；未写入任何未批准的核心身份候选。
- 未改手写核心画像、未改主配置（`config.json` 保持 PATCH1 安全默认）、未改原始语料。
- 未自动发布、未自动合并身份、未自动删除记忆。
- 原有 242 项测试**一个都没改**（只新增 `tests/test_patch2.py`）。
- 未实现 persona 事件自动同步到用户库（仅导出 reviewed 数据，供另行批准的任务消费）。
- 未修 D1（`as_of_paged` 的 offset 缺失组合）、D3（轮转阈值措辞）、D4（`blocked_weak` 口径）、D5（缺 `text` 抛 KeyError）、D7–D13。

## 5. 验证结果

- 单测：**266 / 266 通过**（242 + 新增 24），失败 0；原有 242 项未改一行。
- 手工核对：**37 / 37 通过**，失败 0（覆盖：补标干跑与检索一致性、persona 持久化与重启、
  D6 失败注入、D4/D5 现状证据）。
- 解压副本自校验：清单 **256 / 256** 条校验通过；在解压副本上重跑 266 项单测通过。
- 配置安全默认实测：不设置环境变量时，配置解析结果为相对路径（`./stellamem-data`），
  生产机配置 `config.production-local.json` 未被自动加载。
- 生产未动证据（只写数量与状态，不贴内容/完整路径）：
  - 生产记忆文件数：529，前后一致；
  - 生产索引行数：4,393，前后一致；
  - 状态分布：active 412 / pending 3,948 / quarantined 33，前后一致；
  - 生产索引文件 SHA256：前后一致（值略）；
  - 生产代码目录改动计数：前后一致；
  - 常驻端口进程 PID：前后一致。
- 生产索引无 `namespace` 列，证明补标只在干跑层，未改表结构。

## 6. 已知遗留

1. **主体标签只到干跑**：`namespace` 尚未写入任何真实数据。写入前需要：全量备份、按行
   dry-run 清单核对、回滚方案、批准人。
2. **53 行角色/系统类自由文本**（`assistant`、`system`）按当前规则会被标成
   `namespace=user`；另有 30 行含“人格/观众”字样但不等值。建议写标签前加 hold 列表或人工抽检。
3. **`manual_hold` 分支无真实样本**：persona/viewer 两类在生产数据里都是 0 行，首次出现
   即首跑路径，建议先在索引副本演练。
4. **persona 同步未实现**：`export_pack()` 只返回 reviewed 数据，真正的“同步到用户库”
   需要单独的、人工批准的任务。
5. **D4/D5 未修**：已给出影响与建议（当前 gate 无生产调用方，零运行时影响），等接线前再定。
6. **D1/D3/D7–D13** 未修（详见上一轮审计报告），均不影响本轮五项。
7. **生产部署未做**：PATCH2 不是生产批准，生产仍需单独批准与 checkpoint。
8. 下一步：先决定“标签写入方案（含角色类 hold 规则）”与“gate 接线前的 D4/D5 修法”，
   再谈 PATCH3。

## 7. 回滚方法（staging）

1. 删除当前 staging 副本目录，重新解压 PATCH1 补丁包（或从 PATCH1 备份恢复），
   得到 PATCH1 树。
2. 在 PATCH1 树上运行 `python -m unittest discover -s tests`，确认回到 **242 通过**；
   同时确认 PATCH2 新增的 `compiler/legacy_tags.py`、`compiler/persona_store.py`、
   `tests/test_patch2.py` 不在树中。
3. 若测试期间生成过 staging SQLite 文件（如 persona 事件库），直接删除即可（测试产物，
   不含生产数据）。
4. 不要用生产数据覆盖 staging；也**不要**把 PATCH2 文件反向写回生产。
5. 回滚后如需重试 PATCH2，重新从 PATCH1 树应用补丁包，不要在生产树上操作。
