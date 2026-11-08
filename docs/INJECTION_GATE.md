# 注入门 P1：调用方实体/关键词门

> 用户 2026-11-06 拍板 Wave 1 第一项。做法是评审建议的 option (a)：
> 调用方先判问题与记忆有没有关联，没关联就不注入（budget=0）。

## 门在哪

- 规则实现：`compiler/injection_gate.py`（`check` / `gated_budget` / `gate_enabled`）。
- 生效入口：MCP `stellamem_recall`（`adapters/mcp/stellamem_mcp_server.py`）。
  门拦住时返回 `status: "gated"`、`items: []`、`budget: 0`，附 `gate` 详情。
- HTTP API（`/v1/recall`）默认行为不变，没有接门；以后 API 侧想用，
  调 `gated_budget` 再把预算传给 `recall` 即可。

## 规则从哪借的

OpenClaw 适配器 `adapters/openclaw/index.js` 的 lexical 门：

- 内容词：拉丁词 >= 3 字符、中文连续串 >= 2 字，去掉意图停用词
  （偏好/喜欢/项目/决定……——只说明问题类型，不说明话题）。
  命中 = 任一记忆 claim 的内容词出现在问题里。
- 意图：问题命中某类意图词时，该类记忆可进。
- 技术 carve-out：问端口/版本/文件时，单凭“偏好”二字不能把偏好
  记忆拖进来。

Python 侧没有另写一套判断，而是把同一条规则落到 `compiler/search.py`
的词面信号上：lexical 探针直接复用 search() 的候选 1/2/3 构造
（二元组倒排 MATCH、trigram 短语、精确子串 LIKE），LIMIT 1。
JS 的整词命中一定蕴含二元组命中，所以整词规则被二元组探针包含；
意图/泛问/时间问句三条豁免与 JS 的意图通道和 search() 自有的
泛问/时间通道对应。

判定顺序：

1. 空问题 → 拦（`empty_prompt`）。
2. 词面探针命中 → 放行（`lexical`）。
3. 技术问题 + 只有意图/泛问信号、没有任何词面命中 → 拦
   （`technical_no_lexical`，JS 的 carve-out）。
4. 泛问（“我有哪些偏好”）→ 放行（`broad`）。
5. 时间问句（最近/这几天）→ 放行（`recent`）。
6. 意图命中 → 放行（`intent`）。
7. 语义通道在线（调用方传 `semantic_available=True`）→ 放行
   （`semantic_may_match`）。
8. 其他 → 拦（`no_signal`）。

## 精确性（语义离线时）

门拦住 ⟹ `recall()` 一定返回空。理由：探针覆盖了 search() 的全部
三条词面候选（trigram 命中蕴含二元组命中，因为 trigram 词 >= 2 字，
其二元组必在问题与记录两边）；偏好卡/规律项通道要求 overlap
>= min_score，也需要共享二元组；泛问与时间问句已豁免。
所以在生产现状（语义离线）下，门只省掉注定空的召回调用，不改变
任何一次召回的结果；它省的是 token，不是记忆。

## 什么时候会误拦

- **语义在线时的意译问句**：零词面命中但语义能命中的问题，门默认
  会拦（默认 `semantic_available=False`，按生产现状语义离线）。
  语义恢复在线后，调用方应在调门时传 `semantic_available=True`。
- **技术问题里只有意图/泛问信号**：例如“偏好 8081 端口”，门拦住。
  这是故意的（JS 原本的 carve-out），不是 bug。
- **意图词本身不产生注入**：放行只等于“让召回跑一趟”，召回内部
  的打分仍会滤掉零重叠记录；意图放行省不下那次调用的 token，
  省的是明确无关问题的调用。

## 开关

`STELLAMEM_INJECTION_GATE=0`（或 false/no/off）关闭门；默认启用。
启用的门抛出异常或返回无效判定时，本次召回返回 `fail_closed`、空结果和
零预算；日志只记录异常类型，不输出异常原文。下一次健康请求可以照常召回。
代价是故障期间可能拒绝本来相关的记忆，不能把它解释为“没有记忆”。
显式关闭的是相关性门，主体授权、索引保鲜和记录状态检查仍然执行。
词面探针内部的 `OperationalError` 降级仍按原规则处理；本修复只改变
传播到 MCP 入口的异常。故障对照见 `verification/MCP-GATE-FAULT-20261109.md`。

## 验证

见 `tests/test_injection_gate.py`：无关问题 → 拦、budget 0、零注入；
记忆问题 → 放行、正常召回；技术 carve-out；主体隔离（只命中别的主体
记录的问题，在本主体下拦住）；MCP 入口正反例子。
