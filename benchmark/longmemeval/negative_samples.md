# 负样本评测（第一版）

> Hermes 在 EigenFlux 对话里的建议（4.3）：LongMemEval 量的是检索质量，
> 但「错误记忆被正确检索出来」比「没检索出来」更危险——过期 / 被推翻的
> 旧事实还在注入就是 FAIL。本评测就是第一版。

## 规则

- 每个用例是一道**陷阱题**：问题与旧事实逐字匹配，纯检索一定能找到它。
- 旧事实必须**不注入**（`must_not_contain` 里的串一个都不能出现在投影里）；
  有新事实的用例，新事实必须注入（`must_contain`）。
- 有一条漏网就算 FAIL；跑完打印每条 PASS/FAIL 与总数，任一 FAIL 则
  退出码为 1。

## 用法

```sh
node benchmark/longmemeval/negative_samples.mjs
```

CI 里由 `tests/negative-samples.test.mjs` 跑同一套用例（见下）。

## 用例 schema（我方第一版，6 个字段）

| 字段 | 含义 |
|---|---|
| `id` | 用例编号 |
| `question` | 陷阱问题（与旧事实逐字匹配） |
| `must_not_contain` | 绝不能出现在投影里的串（旧事实的值） |
| `must_contain` | 必须出现在投影里的串（新事实的值；无则为空） |
| `records` | 写入隔离记忆根的 claim（只写与本用例有关的字段，其余走默认记录） |
| `reason` | 这条用例在防什么 |

## 用例（7 条，全是虚构数据）

| 用例 | 在防什么 |
|---|---|
| `neg-expired-01` | `valid_to` 已过期的旧券 |
| `neg-outdated-01` | 标了 `outdated` 的旧事实（无替代） |
| `neg-superseded-01` | 被新值替代的旧值（旧的不进、新的必须进） |
| `neg-status-01` | 状态不是 `active` 的记录 |
| `neg-future-01` | `valid_from` 还没到的记录 |
| `neg-ambiguous-01` | 同一 claim_id 两条 active 修订（一个都不许猜） |
| `neg-control-01` | 阳性对照：当前 active 事实必须注入（防止评测靠全拦来刷绿） |

## 负对照

把 `adapters/openclaw/index.js` 里 `eligible()` 的过期 / outdated /
状态检查与 `project()` 的歧义修订 guard 拿掉，6 条负用例全部 FAIL
（每条恰好漏出被禁的串），阳性对照仍 PASS，退出码为 1——评测确实
能抓到门被拆掉的情况，不是永远刷绿。

## 边界（第一版不做什么）

- 只测**投影层**（旧事实进没进上下文）。reader 拿到空投影后会不会
  拒答，是 reader 的事，不在本评测里。
- 用例是手工写的虚构固定样本，不是随机生成的对抗集。
- 小美征集的负对照 JSONL 九字段 schema 与太极的故障注入验收片段
  还没拿到；拿到后把字段映射到本 schema（或扩展本 schema）再对拍，
  不阻塞第一版。

## 加用例

往 `negative_samples.json` 里加一条：陷阱问题要与旧事实逐字匹配
（否则测的不是门、是检索）；`must_not_contain` 写旧事实值里最独特
的那段；有新值的用例同时写 `must_contain`。
