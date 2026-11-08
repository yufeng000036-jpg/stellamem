# 负样本评测（第二版）

> Hermes 在 EigenFlux 对话里的建议（4.3）：LongMemEval 量的是检索质量，
> 但「错误记忆被正确检索出来」比「没检索出来」更危险——过期 / 被推翻的
> 旧事实还在注入就是 FAIL。本评测就是第一版。第二版（负控做实）不再只
> 测门本身，还防评测自己变假绿：哨兵、池轮换、七字段交换格式（见下）。

## 规则

- 每个用例是一道**陷阱题**：问题与旧事实逐字匹配，纯检索一定能找到它。
- 旧事实必须**不注入**（`must_not_contain` 里的串一个都不能出现在投影里）；
  有新事实的用例，新事实必须注入（`must_contain`）。
- 有一条漏网就算 FAIL；跑完打印每条 PASS/FAIL 与总数，任一 FAIL 则
  退出码为 1。

## 哨兵（第二版新增）

- 被禁的串一旦出现在投影里，说明那条记录**被检索出来且注入了**，
  哨兵就响：该用例记 `sentinel_tripped: true`，整轮
  `sentinel_tripped: true`，CLI 打印 `SENTINEL TRIPPED: <用例>`，
  退出码为 1。
- **哨兵一响，本轮结论不许对外。** FAIL 只是“这条没过”，哨兵响是
  “门可能已经被拆了”——结论先作废，查门再说。
- 证明：`tests/negative-samples.test.mjs` 里有一条 active 事实被当作
  禁串的元测试（门正常拦不住它时应有的样子——等价于拿掉
  `eligible()` / `project()` 的 guard），哨兵必响；门正常时整轮
  9 条哨兵全静。另有 CLI 级测试：拿一个必定漏出的临时池子跑
  runner，断言退出码 1 且 stdout 里有 `SENTINEL TRIPPED`。

## 用法

```sh
node benchmark/longmemeval/negative_samples.mjs
```

CI 里由 `tests/negative-samples.test.mjs` 跑同一套用例（见下）。
每次跑输出一行 `manifest: {...}`（四要素，见“七字段与 manifest”），
供 CI 存档、事后对拍。

## 池子 schema（第二版，版本化）

`negative_samples.json` 不再是裸数组，而是
`{version, changelog, cases}`：

| 字段 | 含义 |
|---|---|
| `version` | 池子版本，整数；每次改用例就 +1 |
| `changelog` | 变更记录，每条 `{version, date, change}`；最新一条的 version 必须等于池子 version |
| `cases` | 用例数组（见下） |

裸数组是 v1 遗留格式，读入时认作 version 0（`loadPoolData`），
旧池子永远冒充不了当前池子。

每条用例（`cases[]`）：

| 字段 | 含义 |
|---|---|
| `id` | 用例编号 |
| `question` | 陷阱问题（与旧事实逐字匹配） |
| `must_not_contain` | 绝不能出现在投影里的串（旧事实的值） |
| `must_contain` | 必须出现在投影里的串（新事实的值；无则为空） |
| `records` | 写入隔离记忆根的 claim（只写与本用例有关的字段，其余走默认记录） |
| `reason` | 这条用例在防什么 |
| `fault` | 注入的故障类型：`expired` / `outdated` / `superseded` / `status` / `future` / `ambiguous` / `none`（`none` = 阳性对照，无故障） |

## 轮换（第二版新增）

负控池会被检索器过拟合而退化：同一批陷阱题跑久了，门是为这几道
题调的、还是真拦得住，渐渐分不清。所以池子定期轮换：

- **周期：每季度一轮**，与 LongMemEval 回归同频；每轮由当轮负责人
  执行并记 changelog。
- **每轮新增 ≥ 2 条新陷阱 case**（虚构数据，绝不写真实记忆），
  覆盖当轮最担心的故障类型；新 case 先跑一遍“门拆掉版”确认它
  真能漏出（否则它测的不是门），再进池。
- **淘汰已退化的 case**：连续两轮“零漏出且检索器已针对它调优”的
  旧 case 移出 `cases`，记入 changelog 的 `retired`（只移出、不
  删除文件外的记录——淘汰的 case 与淘汰原因保留在 changelog 里，
  可追溯、不可悄悄消失）。
- **每次改动 version +1**，changelog 写清日期、改了什么、为什么；
  `manifest.pool_version` 与每条结果的 `evidence_source` 都跟着变，
  旧轮次的证据拿新池子的号对不上，一眼可辨。

## 七字段与 manifest（第二版新增）

负控交换格式是七字段（XH-workbuddy schema）：每条用例结果都带

| 字段 | 含义 |
|---|---|
| `arm_executed` | 跑的是哪个 arm：固定 `"negative-control"` |
| `criteria_digest` | 该用例判定标准的摘要（sha256：问题 + must / must-not 清单 + fault + 池版本） |
| `injected_fault` | 注入的故障类型（用例的 `fault` 字段） |
| `expected_verdict` | 期望判级：故障用例是 `检出故障`，阳性对照是 `正常注入` |
| `observed_verdict` | 实测判级：`检出故障` / `未检出故障` / `正常注入` / `注入缺失` |
| `run_id` | 本轮运行 id（`neg-<UTC 时间>-<随机>`），整轮结果共用一个 |
| `evidence_source` | 证据来源：`negative_samples.json v<池版本>#<用例 id>` |

`expected_verdict` 是独立判级、**不是 PASS/FAIL**：什么都不做的
判据输出 PASS 也能算通过，负控就失去意义。`pass` 只在内部由
`observed_verdict === expected_verdict` 导出。

每轮还出一份 manifest，四要素：

| 要素 | 含义 |
|---|---|
| `schema_version` | 七字段 schema 版本（当前 1） |
| `criteria_digest` | 整池判定标准的摘要（各用例 `criteria_digest` 的汇总） |
| `run_id` | 本轮运行 id，与每条结果的 `run_id` 相同 |
| `pool_version` | 池子版本 |

**必失败样绑 `run_id` 才算当前证据**：一条结果只有 `run_id` 与
本轮相同、且带 `criteria_digest`，才算本轮证据（见
`isCurrentEvidence`）。缺 `run_id` 的必失败样可能是旧轮次、旧池子
或旧标准的陈年拷贝，不许当作本轮结果引用。

回执绑定：guardrail 回执（`compiler/guardrail_receipt.py`，PR #86）
也可带 `criteria_digest` 与 `run_id`，带上后两者一并折进
`rule_digest`——把回执贴到别的标准或别的轮次上，验签即失败。
`InjectionGate.decide()` 透传这两个参数（可选，不传时回执与原来
完全一样）。

## 用例（9 条，全是虚构数据）

| 用例 | 在防什么 |
|---|---|
| `neg-expired-01` | `valid_to` 已过期的旧券 |
| `neg-outdated-01` | 标了 `outdated` 的旧事实（无替代） |
| `neg-superseded-01` | 被新值替代的旧值（旧的不进、新的必须进） |
| `neg-status-01` | 状态不是 `active` 的记录 |
| `neg-future-01` | `valid_from` 还没到的记录 |
| `neg-ambiguous-01` | 同一 claim_id 两条 active 修订（一个都不许猜） |
| `neg-control-01` | 阳性对照：当前 active 事实必须注入（防止评测靠全拦来刷绿） |
| `neg-expired-02` | 过期陷阱第二条：过期健身房年卡（换一个槽位，不只靠一张券） |
| `neg-superseded-02` | 替代陷阱第二条：旧手机号被新号替代（旧的不进、新的必须进） |

## 负对照

把 `adapters/openclaw/index.js` 里 `eligible()` 的过期 / outdated /
状态检查与 `project()` 的歧义修订 guard 拿掉，6 条负用例全部 FAIL
（每条恰好漏出被禁的串），阳性对照仍 PASS，退出码为 1——评测确实
能抓到门被拆掉的情况，不是永远刷绿。拿掉 guard 时哨兵也必响
（见“哨兵”一节的元测试）。

## 边界（第二版不做什么）

- 只测**投影层**（旧事实进没进上下文）。reader 拿到空投影后会不会
  拒答，是 reader 的事，不在本评测里。
- 用例是手工写的虚构固定样本，不是随机生成的对抗集。
- 七字段交换格式已落地（本版）；小美征集的负对照 JSONL 九字段
  schema 与太极的故障注入验收片段还没拿到，拿到后对拍字段映射，
  不阻塞本版。

## 加用例

往 `negative_samples.json` 的 `cases` 里加一条：陷阱问题要与旧事实逐字匹配
（否则测的不是门、是检索）；`must_not_contain` 写旧事实值里最独特
的那段；有新值的用例同时写 `must_contain`；`fault` 写故障类型；
同时 `version` +1、`changelog` 记一笔（见“轮换”）。
