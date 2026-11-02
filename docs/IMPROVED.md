# Stellamem 改进轮 — 说明

工作范围：只改 `work/` 里的副本。`original/` 未动，生产路径未碰，
真实记忆文件未读、未复制。本轮测试数据全部是合成占位数据
（测试用户A / 测试人格B / 测试观众A），没有真实个人信息。

基线与结果（本机工作副本，Python 标准库 only）：
- 改动前：`python3 -m unittest discover -s tests` → Ran 187 tests，OK
- 改动后：Ran 226 tests，OK（187 原有 + 39 新增，0 失败）
- 没有削弱任何原有测试。`as_of` 的签名改动不需要更新原有测试：
  经 grep 确认，原有测试里没有任何 `as_of` 调用，只有 `search.py`
  自身的定义，因此不存在“为通过而改测试”的情况。

## 文件改动一览

### P0 — 已知缺陷修复

- `compiler/search.py`（改动）
  - `as_of()` 默认值由 `limit=100` 改为 `limit=None`（无 LIMIT 子句）。
    原因：2026-11-02 上线第 10 步证实，默认 100 会按 occurred_at DESC
    静默截断（412 条 active 只返回前 100 条）。显式传 limit 仍生效。
  - 新增 `as_of_paged()`：与 `as_of()` 共用 `_as_of_base()` 过滤逻辑
    （status 为 active/superseded、valid_to 时间窗为准），返回
    `{items, total, returned, limit, offset, truncated}`，截断可被看见。
  - 附带：本文件行尾由 CRLF 统一为 LF，纯格式，不改语义。
- `compiler/gate.py`（改动）
  - 新增 `is_low_signal()`：原来只拦 `weak=True`。现在即使
    `weak=False`，只要字面重叠 0 且语义 < MIN_SIM（0.45，来自
    `semantic.py`），或综合分 < LOW_SCORE（0.10），同样按弱信号拦下；
    `allow_weak=True` 时仍可注入，但打低置信度标记。
  - 明确限定：完全没有 overlap/semantic/score 字段的条目不由此
    判定（无法凭空 invent 信号），这是已知边界，不是疏漏。
  - 新增日志轮转：`gate_log.jsonl` 大小达到 1 MiB（1,048,576 字节）或行数达到 10,000 行时，在写入
    当前条目前轮转，最多保留 3 个旧文件（.1/.2/.3），当前写入永不丢失。
- `compiler/reliability.py`（改动）
  - `snapshot()` 改为“先收集、后写盘”：先收集全部相对路径并用
    `_check_duplicate_names()` 检查重名，重名时抛 ValueError 且不
    留下任何部分 zip；正常快照行为不变。
  - 附带：本文件行尾由 CRLF 统一为 LF，纯格式。
  - 诚实说明：正常文件系统下 `rglob` 本来产不出重复相对路径，
    因此重复路径的集成测试是用 patched `rglob` 返回同一文件两次
    来触发的；检测逻辑本身另有直接单元测试。

### P1 — 两项目融合（一个治理核心，三个独立主体）

- `compiler/subjects.py`（新增）
  - 命名空间：`user`（个人记忆）/ `persona`（直播人格记忆）/
    `viewer`（观众上下文，最受限）。
  - `recall_for_subject()` 默认只返回同主体记录；桥接副本之所以
    能被目标主体看到，是因为副本自身的 subject 就是目标主体，
    原件仍对另一主体不可见。内容永不合并。
  - 重要设计（fail-closed）：原有记录里 subject 是自由文本
    人名/主题的，在本模块里属于“未分类”，对任何主体都不返回。
    这是故意的：把人名悄悄当成命名空间，正是本模块要防止的合并。
    上 staging 前，这类记录必须先显式打标签，否则会得到空结果——
    空结果是保护，不是 bug。
  - 本模块不改写原有 search/recall 路径，只作为新的融合边界。
- `compiler/bridge.py`（新增）
  - 默认拒绝：建请求时状态即 pending，批准前目标主体拿不到副本。
  - 仅三个结构化模板：relationship（A 是 B 的 C）、method
    （做 X 用 Y 方式）、result（X 的结果是 Y）；字段缺失、为空、
    多余字段、未知模板、同主体桥接一律拒绝。没有自由文本字段。
  - 批准必须同时满足：approver 非空、confirmed_full_text=True
    （表示已看过完整渲染文本并为此负责）；黑名单命中时还需
    acknowledge_flag=True。批准时重新校验模板字段。
  - 批准后副本记录 source、origin_id、bridge_id、approver、
    approved_at；每一次建/批/拒/撤/过期都写审计。
  - 批量批准是两阶段全有或全无：先全部重检，任一失败则整批
    不动；单条撤回仅 pending 可撤。
  - TTL 72 小时：明确标注为低置信度猜测，待真实使用一个月后
    再定；过期后不可批准、不可出副本（fail-closed）。
  - 关键词黑名单只是噪声过滤器，永远不是安全边界：改写后绕过
    关键词的请求仍是 pending，仍需人工批准全文——测试覆盖了这一点。
- `compiler/persona_adapter.py`（新增，内存态接口）
  - `record_event()` 只产生 persona 主体事件，初始状态
    pending_confirmation；传 subject=user 会被拒绝，不是悄悄改写。
  - `retrieve_pack()` 只返回 persona、只含已确认事件、字符预算
    上限、发言人卡片在最前；未说出口的 pending 事件不可见。
  - `confirm_spoken(confirmed=True)` 才把 pending 升级为 confirmed；
    confirmed=False 不改变任何状态；没有自动确认、没有超时确认。
  - 限定：本轮 adapter 是内存态接口，不持久化到 markdown/SQLite。
    落盘与否是下一轮的存储决策，本轮不要把它当成已持久化的记忆。
- `compiler/promises.py`（新增）
  - 独立 SQLite sidecar 表 `promises` + `promise_audit`，不是
    markdown claim，因此承诺的状态机不会改写记忆记录。
  - create / fulfil（必须带证据）/ expire / expire_due / list_open；
    过期后 fulfil 被拒绝，但拒绝本身记审计、承诺标为 expired 且
    可查，不删除、不标为已完成。

### P2 — 只读辅助

- `compiler/pending_report.py`（新增）
  - `screening_report(records)` 只计数：pending 总数、按 kind 分组、
    按 status 分组、pending 中重复 claim_id 组及多余行数、最老/最新
    时间；返回 `changed_anything=False, activated_anything=False`。
  - 本模块不打开任何记忆目录、索引或生产路径；调用方负责从
    staging 副本读记录喂给它。它本身不可能激活任何记录。
- `tests/test_improved_p0.py`（新增，13 个测试）
  - as_of 默认不截断、显式 limit、分页截断标志；gate 低分拦截、
    allow_weak 标记、强信号仍注入；日志按大小/行数轮转、最多 3 个；
    快照重名检测与正常快照。
- `tests/test_improved_p1.py`（新增，24 个测试）
  - 主体隔离（user/persona/viewer 互不可见、未分类全拒、桥接副本
    仅目标可见）；桥接批准前后、全文确认、黑名单绕过仍需批准、
    黑名单命中需确认、模板/同主体拒绝、批量整批失败与成功、撤回、
    72h 过期；人格适配器 pending→已说流程、预算与卡片在前、拒绝
    user 写入；承诺完成/证据/过期后拒绝可审计/到期清理。
- `tests/test_improved_p2.py`（新增，2 个测试）
  - 待审报告计数、重复组、时间范围、输入记录不被修改、空输入。

## 没有做的事，以及为什么

- 没有桥接 GUI：本轮只有终端式批准（approver + confirmed_full_text
  参数），GUI 是另一轮的工作量，不是把参数改个名就能冒充的。
- 没有真实 Douyin/平台播出信号：没有开放平台应用、OAuth、回调，
  无法凭空造一个“真实信号”；persona_adapter 的确认仍需显式调用。
- 没有修 8082 端口冲突：那是本地配置（QQ 占用），不是代码问题，
  改代码修不了端口占用。
- 没有激活任何 pending 记录：P2 报告是只读的；3969 条 pending 的
  处理需要单独的审批流程，不在本轮。
- 没有写 5 条未批准的核心身份候选：用户未批准，任何轮次都不能顺带写。
- 没有多设备同步、没有图数据库：基础未稳之前加这两项只会放大漂移。
- 没有生产部署脚本：按任务要求，本轮不产生生产部署脚本，也不宣称
  生产已更新。生产部署仍需单独批准（见 STAGING-TEST.md）。
- 没有改动原有代码里的 `./stellamem-data/index.db` 等默认路径：
  它们是原有代码自带的默认值，本轮既没有运行它们，也没有假装修掉它们。

## 剩余风险（按优先级）

1. 上 staging 后必须先给旧记录打主体标签，否则
   `recall_for_subject()` 会因 fail-closed 返回空——这是保护机制，
   但如果没人做标签，会被误读为“检索坏了”。
2. `persona_adapter` 本轮不持久化；如果 staging 直接把它当长期
   记忆用，进程重启后事件会丢。下一轮必须先定持久化方案再接直播。
3. gate 对无信号字段的条目不判定；调用方如果只传 text、不传
   overlap/semantic/score，低分拦截就覆盖不到它。调用方应传信号字段。
4. 桥接 TTL 72h 是猜测值；真实使用一个月后必须用实际队列形态重定。
5. 关键词黑名单仍可被改写绕过；真正边界始终是人工看全文批准，
   任何“黑名单已拦住所以安全”的说法都是错的。
6. 快照重名检测在正常文件系统上难以自然触发；它的价值在于
   zip 写入前多一道防线，不是已在生产上复现过的故障。
7. openclaw 端到端检索在上一轮上线后仍未实测；本轮没有改动
   openclaw 适配器，因此这一项仍然悬着，staging 时应一并测。
