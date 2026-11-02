# STAGING 测试步骤（仅 staging，绝不直接对生产跑）

> 红线：本文件描述的任何一步都不以生产记忆、生产索引或生产代码
> 为目标。生产部署需要用户另行单独批准；本轮不提供生产部署脚本。

## 0. 准备：先备份，再复制

1. 在本地先对 staging 做一次完整备份（记忆目录 + 索引 + 代码），
   并记录备份路径与 SHA，备份没做完不许往下走。
2. 把本 `work/` 树完整复制到 staging 目录（例如一个与生产隔离的
   staging 文件夹）。不要把 `work/` 直接指到生产目录，不要用
   生产的索引路径、记忆路径、端口去跑测试。
   PATCH1 起包内 `config.json` 是安全默认（`./stellamem-data`、
   `127.0.0.1:8081`），可直接用于 staging；生产机真实配置在
   `config.production-local.json`，不会被自动加载，**不要**把它设成
   `STELLAMEM_CONFIG` 去跑测试。
3. 确认 staging 里没有真实生产记忆的写入口：如果 staging 记忆是
   生产的复制品，测试用的写操作也只应落在 staging 复制品上，
   测完即弃，不要回写生产。

## 1. 跑测试（staging 上，合成数据为主）

```bash
cd <staging-copy>
python3 -m unittest discover -s tests
```

期望：Ran 266 tests，OK（187 原有 + 39 改进轮 + 16 PATCH1 + 24 PATCH2）。
如果原有 187 中任何一项失败：停下来，先查是不是 staging 环境
（依赖目录、路径、Python 版本）问题；不要通过放宽断言让它通过。
如果新增测试失败：把失败项、日志、staging 与 work 的 diff 一起
发回，不要自行改生产代码去“凑通过”。

## 2. 逐项手工核对（staging 上，用合成占位数据）

- as_of：造 150 条以上合成 active 记录，默认 `as_of()` 应全量返回；
  `as_of_paged(limit=100)` 应报 `truncated=True, total=150`。
- gate：送一条 overlap=0、semantic=0.2、weak=False 的条目，应被拦；
  `allow_weak=True` 时应注入并打低置信度标记。
- gate（PATCH1）：只带 `overlap=0` 或只带 `semantic=0.2` 的“部分字段”
  条目也应被拦；`overlap="0.9"` 这类数字字符串应按数字解读；只有
  text、没有任何信号字段的条目仍不判定。
- gate 日志：把日志写到超过 1 MB 或 10,000 行，确认出现 `.1` 轮转
  文件、当前日志只剩最新一条，且不超过 `.3`。
- snapshot：正常快照应成功；重名检测可用单元测试覆盖（正常文件
  系统下难以自然造出重名，不要为测它而改生产文件）。
- subjects：user 查不到 persona、persona 查不到 user、viewer 独立；
  未打标签的旧记录应返回空（这是 fail-closed 保护，不是故障）。
- bridge：批准前拿不到副本；批准时必须给 approver 和全文确认；
  改写后绕过关键词的请求仍是 pending；整批中有一条已撤回则整批不动；
  72h 过期后不可批准。
- bridge（PATCH1）：批量里出现重复 req_id、或某一条在验证时已过期，
  必须整批抛错且没有任何一条被批准；成功批次的所有条目 approved_at
  相同（批量只读一次时钟）。
- persona_adapter：事件先 pending、检索包里看不到；显式
  confirm_spoken 后才出现；预算 120 字符时文本不超预算、卡片在最前。
- persona（PATCH2）：SQLite sidecar 持久化——待确认不可见、确认后可见、
  人工复核后才进 `export_pack`、重复 event_id 拒绝、重启后事件仍在、
  subject=user 被拒绝（不写用户库）。
- 主体标签（PATCH2）：`legacy_tags.plan_legacy_tags()` 是纯干跑——不改输入、
  不写盘、返回计划哈希；`tagged_records()` 只在内存加 `namespace`；
  旧 recall 结果补 `namespace=user` 后一条不少；persona 未桥接为 0；
  桥接副本只对目标主体可见；无效 `namespace` 存在时 fail-closed。
- bridge（PATCH2）：第二阶段注入失败（`_copy`/`_audit` 抛错）后整批回滚为
  pending，不留下半批。
- promises：带证据完成、过期后完成被拒绝且审计里有拒绝记录、
  list_open 里不再出现已过期项。
- pending_report：只读计数，跑完后 staging 记录的状态、条数、
  SHA 应与跑之前一致；任何一条状态变化都算失败。

## 3. 需要单独确认的三件事（staging 报告里必须写清）

1. 旧记录主体标签方案：staging 上用哪种规则给旧记录打
   user/persona/viewer 标签？没定这个，`recall_for_subject()` 在
   真实旧数据上会返回空。
2. persona_adapter 的持久化方案：本轮是内存态，staging 若要接
   真实直播，必须先定落盘位置与格式，否则重启即丢。
3. openclaw 端到端检索：上一轮上线后未实测，本轮未改 openclaw
   适配器；staging 上应做一次真实对话式检索抽查，确认记忆仍能
   被检索到。

## 4. 回滚（staging 出问题时）

- 代码回滚：删掉 staging 复制品，恢复步骤 0 的 staging 备份。
- 数据回滚：staging 记忆如有改动，从步骤 0 的备份恢复，不要
  从生产复制覆盖（那会把生产状态反向带进 staging 的判断）。
- 回滚后重跑 `python3 -m unittest discover -s tests`，确认回到
  187 项基线状态，再决定是否重新测试本轮改动。

## 5. 生产批准（不在本轮内）

全部 staging 检查通过后，仍需用户单独批准才能谈生产。生产步骤
至少包括：生产全量备份 + SHA 基线、staging 与生产最终代码的
diff 核对、迁移/部署的逐项检查点、以及一次真实替代验证。
没有这份批准，任何人都不应把本轮代码写进生产。
