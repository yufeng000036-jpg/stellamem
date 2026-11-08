# 拒答改法验证（第一波）

> silver 12:13 的两个改法（1a 生成前 CoT 预检、1b 语义级 guardrail），先在
> 小样本上验证再决定进不进生产路径。验证无效就停下，不硬上。

## 结论（2026-11-08 验证跑完，60 次新鲜调用、36,052 token、$0.0059；全程 $0.0063）

基线臂完整复现第四轮：拒答集 10/15 拒答、5 道翻车题原样翻车；阳性对
照 10/10 保住。验证本身有效，两个改法都不进生产路径：

- **1a：拒答 5/5 翻过来了，但阳性对照只保住 5/10。** 充分性指令确实
  抓住了近似证据（5 道翻车题全翻成拒答，10 道答对的拒答题也全保住），
  但它把门槛抬到了所有题上：5 道证据充足的答题也被它拒了（Suica 卡、
  约两小时、星期三等，基线都答对了）。结论：**1a 作为通用改法验证无
  效，不进生产路径。** 它是一个有效的拒答机制，但需要加作用域（只在
  证据薄弱/近似时启用，或把措辞放软）——那是下一轮的设计，不是本次
  的交付。
- **1b：拒答 2/5 翻过来，阳性对照保住 9/10。** 净 +2/−1。judge 的判
  断不可靠：0ddfec37 判对了但理由是错的（无中生有说题目问的是橄榄
  球）；3 道翻车题 judge 被表面匹配骗过（4 engineers、5 tomato
  plants、9年）；阳性对照里还误杀 1 道（d7c942c3，gold 是 Yes，judge
  说证据不支持——理由本身有道理，但跟 gold 冲突）。结论：**1b 单独
  不够把 0.800 推到 0.90，也验证无效（作生产路径）。** 全量 30 题外
  推约 0.87，且每次答题多一次云调用（成本/延迟/隐私待算）。
- **负样本读者案：三个臂都是 4/7，构成不同。** 1a 多抓了歧义修订（看
  得见的冲突），但把 superseded 的正确答案也拒了；过期/状态失效两案
  三个臂全漏——读者看不见 valid_to/outdated/status，投影行里没这些元
  数据。结论：**有效性必须在投影层拦，读者侧修不掉**，与 PR #84 的
  设计一致。另：neg-future-01 的“干净”是检查口径的 artifact——答案
  “明年”不含“火星”所以 must-not 没抓到，但它确实是用了未生效的事
  实；读者层复用 must-not 检查时要注明这个口径缺口。

总 verdict：两个改法都不进生产路径。本 PR 只落验证工具（脚本+本文档
+测试）与这次测量的阴性结论，不改任何生产路径。全量 500 题重测
（约 $1.29）与下一轮（加作用域的 1a、更强的 judge）须另行批准。

## 两个改法是什么

- **1a 生成前 CoT 预检**：读者 system prompt 加一段充分性判断——只有当
  记忆行“完全且直接”支撑唯一答案（同一物品、同一人、同一时间窗口、同
  一数量）时才答；行只关于相似物品、不同时间窗口，或对同一问题给出两个
  不同数字，就拒答；不许挑最像的数字。单次调用，JSON 输出格式不变。
  注意：本评测路径发 `"thinking": {"type": "disabled"}`，所以这段检查是
  模型内部推理，不是外显的 CoT 文本；双调用外显 CoT 是后续选项，不在
  本次验证里。
- **1b 语义级 guardrail**：基线答完后，轻量 LLM-as-a-judge 拿同一上下文
  对比“问题”与“生成答案”；答案含上下文没提的核心事实就拦截、改成拒
  答。只在答案臂真的答了时才调 judge；已拒答的不需要 judge。

## 验证集（全部是现成数据，不新造金标）

| 集 | n | 来源 | 通过标准 |
| --- | --- | --- | --- |
| 拒答集 | 15 | 第四轮原投影臂的 5 道翻车题（031748ae、0ddfec37、2133c1b5、6456829e、gpt4_93159ced）+ 24 道答对拒答题里固定种子（20261108）抽的 10 道 | 翻车题翻成拒答；答对的保持拒答 |
| 阳性对照 | 10 | 第四轮判对的非拒答题（有 sidecar），固定种子（20261108）抽样 | 仍答、答案仍对（金答案探针命中，或与第四轮答案一致）；掉下去就是召回侧回归 |
| 负样本读者案 | 7 | PR #84 的 7 条负样本重建成读者案：陷阱记录就在上下文里（假装投影漏了它） | 逐案看 must / must-not，不折成单一 pass/fail |

> 注（PR #93 之后）：负样本池已版本化为 v2（9 条，新增 neg-expired-02、neg-superseded-02），`build_negative_cases` 按当前池子读；上表与本文件的 4/7 结论是 2026-11-08 在 v1 七条池上实测的数，不改写。

### 0ddfec37 的数（已核实，别再抄错）

简报初稿写“抽了 21 条”——sidecar 里没这个数，**不要引用**。核实值：
sidecar `6817efa4a19118c0.json`：projected_n=7、candidates_n=18、2 个会
话、投出的 7 条里引用 1 条（c-006cea81f433c39f1976765f，“三个月内已收
集15个”）。模型答了 "15"——题目问的是“收藏的头三个月”新增数，证据
给的是“三个月内 15 个”和“近几个月新增 20 个”，时间窗口对不上，是典
型的近似证据翻车。

### 负样本读者案的边界（先说清楚，免得误读）

投影行里没有 valid_to / outdated / status 这类有效性元数据，所以读者
**看不见**过期、被推翻、状态失效——这几案在读者层失败，不证明改法
坏，只说明有效性必须在投影层拦，读者只能抓看得见的冲突（歧义修订两
个值都在行里）和日期错位。验证报告里按案列数字，不折成一个总分。

## 怎么跑

```sh
# 起代理（动态端口，见 port.json；usage 日志用绝对路径，目录改名会断）
python3 ~/workspace/longmemeval-pilot-20261106/proxy.py \
  --port-file work/abstain-validation/port.json \
  --log work/abstain-validation/usage.jsonl > work/abstain-validation/proxy.log 2>&1 &
PORT=$(python3 -c "import json; print(json.load(open('work/abstain-validation/port.json'))['port'])")

python3 benchmark/longmemeval/abstain_validation.py \
  --results work/full/results.json \
  --oracle work/longmemeval_oracle.json \
  --diagnostics work/claim-replay-full/diagnostics \
  --cache-dirs work/full/shard0/cache work/full/shard1/cache \
               work/full/shard2/cache work/full/shard3/cache \
  --work work/abstain-validation --endpoint http://127.0.0.1:$PORT \
  --usage-log work/abstain-validation/usage.jsonl \
  --negative benchmark/longmemeval/negative_samples.json
```

- 基线臂复用第四轮缓存（cache key 完全一致，$0）；新鲜调用只有 1a 臂
  （32 次）+ 1b 的 judge（21 次）+ 负样本读者案的基线/答案臂
  （14 次），实测合计 60 次、36,052 token、$0.0059。**不要跑完整 500 题重跑**（约 $1.29，
  须用户另行批准）。
- 费用口径：proxy usage 日志里的 token 数 × flash 非高峰官价
  （miss $0.15/1M、hit $0.003/1M、out $0.6/1M），与第四轮 merge.py
  同口径；脚本结束时打印精确 token 数与费用。
- 结果写进 `work/abstain-validation/validation_results.json`（不进仓库；
  仓库里只留脚本、本文档与测试）。脚本可续跑：已有的臂跳过。

## 验证有效才进生产路径

有效 = 拒答集里翻车题基本翻成拒答、答对的没翻成答题、阳性对照答案基
本保住（判对率不掉下门）。有效也不等于直接全量上线：生产落点（读
者 prompt 放在适配器投影头、MCP 召回指令，还是只写进客户端指引；
1b 的 judge 调用走不走云、成本与隐私怎么算）要另写方案；全量 500 题
重测（约 $1.29）须用户另行批准。
