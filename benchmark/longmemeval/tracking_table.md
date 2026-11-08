# LongMemEval 逐题追踪表（路线 B，2026-11-06 第三轮 / 2026-11-08 第四轮）

> 给复核方（凯瑞's Agent、silver）看的表。按「从 source turn → claim →
> 审批/版本 → 检索候选 → 投影入上下文 → 最终回答/拒答」逐题记「源里有没
> 有、claim 有没有、候选有没有、投影有没有、回答是否引用并被证据蕴含」。
>
> **第四轮（20261108 版，最新）**：`tracking_table_20261108.csv`——main
> `9034cde`（PR #79 窄 guard 在位），判分 177/500 = 0.354、recall@5 均值
> 0.857、正确拒答 24/30 = 0.800；断点分布 project/cite 211、pass 175、
> answer/use 57、extract 37、retrieve 14、answer 6。轮间变化：判分对上一轮
> 提升 28 题、退步 26 题、446 题不变（净 +2）。拒答失败集：修好了 a96c20ee
> 与 f685340e（#78 回归题），新翻了 0ddfec37 与 gpt4_93159ced，净变化 0——
> PR #79 在全量上没有净效果，见工作区报告 `longmemeval-rerun3-20261108.md`。

## 文件

- **表本体（CSV，500 行）**：`tracking_table_20261108.csv`（最新，第四轮）；`tracking_table_20261106.csv`（第三轮，保留对照）
- **生成脚本（可复跑）**：`tracking_table.py`（见用法，下方）
- **本轮报告**：工作区报告 `longmemeval-rerun2-20261106.md`（数字来源；报告与
  原始逐题结果 `results.json` 随每轮评测保存在工作区，不进仓库）
- **题库**：公开数据集 `xiaowu0162/longmemeval-cleaned @98d7416c`，SHA256
  `821a2034…d38620c`；题库是合成数据，样本不含用户私人记忆

## 复跑用法

```sh
python tracking_table.py \
  --results <本轮 results.json> \
  --prev-results <上一轮 results.json> \
  --oracle <longmemeval_oracle.json> \
  --out tracking_table_<日期>.csv
```

脚本跑完会打印核验行：行数、判分通过、recall@5 均值、正确拒答、断点分布、
轮间变化；这些数必须与当轮报告对上，否则表不可信。本表（20261106 版）
由同一脚本从第三轮证据文件生成，与工作区原表逐字节一致。

## 协议与版本（给复核方）

- 评测协议：`v2-full-route-B`，500 题全量；仓库 stellamem main `c46a6fa`
  （PR #78 召回修复已合入）；回答与判分模型均为 `deepseek-flash`
  （thinking disabled）；32 次调用上限不变。
- 上一轮对照：同一协议、同一 500 题，main `365e83a`。
- 判分口径：`judge_main` 是判分模型给的 `judge_label`
  （`judge_response` yes/no）；`judge_hypothesis` 列是判分时看到的答案原文。
- **本表的能力边界（先说清楚）**：`results.json` 里没有逐条 claim 文本，
  只有会话级哈希（`extracted_turns` / `retrieved_sessions` /
  `cited_sessions`）和数值指标。所以本表是**阶段级追踪**（每题走到哪一环、
  断在哪一环），不是 claim 文本级追踪。claim 级（哪条 claim 进了上下文、
  哪条被投影丢了）需要给 adapter 加埋点才能出，是下一步，不是本表能给的。
  表里不假装有 claim 文本。

## 链路定义：loss_stage 是怎么判的

只对判错的题定断点；判对的题记 `pass`（即使 recall@5 不满 1）。

| loss_stage | 定义 |
|---|---|
| `pass` | 判分通过（拒答题：正确拒答） |
| `extract` | `error` 非空，或 `extracted == 0`（源会话没提出任何 claim） |
| `retrieve` | 提取成功，但 `recall@5 == 0`（金证据一次没被召回） |
| `project/cite` | 召回到了（recall@5 > 0），但 `cited_sessions` 为空或 `citation_valid` 为假（证据没进引用/投影） |
| `answer/use` | 召回到了、引用也有效，但判分仍错（证据在手里，答案没用上/用错） |
| `answer`（仅拒答题） | 该拒答却作答 |

部分召回（0 < recall@5 < 1）只记在 `loss_detail` 里当促成因素，不当第一断点。
拒答题没有金检索目标（recall 字段为空），链路单独处理。

## 分母是分开算的

| 指标 | 分母 | 本轮 |
|---|---|---|
| 判分通过（main） | 500 题 | 175 / 500 = 0.350 |
| recall@5 均值 | 470 道非拒答题（拒答题无此字段） | 0.870 |
| 正确拒答（exact 口径） | 30 道拒答题 | 24 / 30 = 0.800 |
| 正确拒答（judge 口径） | 30 道拒答题 | 26 / 30 = 0.867 |
| 引用有效 | 只在 loss 定性里用，不单独成 headline 指标 | 见下 |

## 核验：表里的总数能对上报告

- 判分 175/500 = 0.350 ✓；recall@5 均值 0.870 ✓；拒答 exact 24/30 = 0.800 ✓。
- 轮间变化：判分通过从 118 题 → 175 题（+57）；逐题比对：变好 82 题、
  变差 25 题、不变 393 题（82 − 25 = 57 ✓）。

## 丢失归因：325 道判错的题断在哪

| loss_stage | 题数 | 占判错题 |
|---|---|---|
| project/cite（召回到了，引用/投影没接住） | 211 | 65% |
| answer/use（引用有效，答案还是错） | 61 | 19% |
| extract（没提出 claim） | 35 | 11% |
| retrieve（金证据一次没召回） | 14 | 4% |
| answer（拒答题该拒未拒） | 6 | 2% |
| pass | 173* | — |

\* pass 173 与判分通过 175 差 2，是拒答题上两个口径打架的两题（见下），
它们在 loss_stage 里按 `correct_abstention` 记为 answer。

**结论 1：瓶颈已经不在召回，在投影/引用。** retrieve 只断了 14 题，
PR #78 把召回修好之后，65% 的错题是「找得到、引不出来」。
按类别：multi-session 68 题、temporal-reasoning 64 题、
single-session-assistant 33 题断在 project/cite。
single-session-assistant 全类别只过 4/56，报告里它的证据覆盖只有 0.089，
是投影环节最具体的病灶。

**结论 2：extract 的 35 题要拆两半看。** 27 题有记录的 ChunkFailure；
另外 8 题没有任何 error 记录但 `extracted == 0`，**全在
single-session-assistant**：`ceb54acb / 41275add / b759caee / 8752c811 /
0e5e2d1a / 89527b6b / 3e321797 / 1903aded`。是静默提取失败还是计数口径
问题，表里标出来了、没定论，需要单查。

注：29 题 ChunkFailure 是 `results.json` 里 main 臂 `error` 非空的总数
（multi-session 15 + temporal-reasoning 13 + abstention 1）。
本表 extract 只含其中 27 题：另 2 题因判分口径进了别的桶——
`gpt4_372c3eed_abs`（拒答题，按拒答分支记为 answer）、
`gpt4_a1b77f9c`（见结论 3 的第二条，判分给了空答案一个 yes，
故在 loss_stage 里记为 pass）。

**结论 3：拒答的 fail-open 有实证，但要分两件事说。**
- 6 道答错的拒答题里，5 道在作答前确实召回了会话（`retrieved_n ≥ 1`），
  与报告「召回变强 → 不该答的题也敢答」的假说一致，但这只是相关性，
  表里只记了「作答前有召回」这个事实，没断言因果。
- 判分与拒答指标在 2 题上直接打架：
  - `6456829e_abs`：模型作答「5 tomato plants…」，判分说对
    （答案与金答案文字相符），但这题本应拒答——judge 记对、拒答记错。
    这就是「拒答错误是否被注入门 fail-open 放大」的实例：
    只看判分会漏掉它。
  - `gpt4_372c3eed_abs`：提取失败（ChunkFailure）、答案为空，
    判分却给了 yes——空答案被判对，是判分模型的 artifact，不是真答对。
  两题的 `judge_hypothesis` 和 `answer_text` 都在 CSV 里，可逐题复核。
- 还有第三个判分 artifact，不在拒答题里：`gpt4_a1b77f9c`
  （temporal-reasoning）同样提取失败、答案为空，判分也给了 yes。
  也就是说 headline 判分 175/500 = 0.350 里含了 2 道空答案被判对的题；
  若按「空答案不算对」计，应为 173/500 = 0.346。本表不改 headline 数字，
  只把这两题标出来，供复核方决定怎么算。

**结论 4：user/assistant 来源差异——有差异，但别直接归因到来源。**
金证据在 user 回合的题：144/416 通过（34.6%）；
金证据在 assistant 回合的题：3/51 通过（5.9%）；mixed：2/3。
但 assistant 来源的题几乎全在 single-session-assistant 类别里
（51 题里该类别占大头），而这个类别本身只过 4/56。
来源差异与类别差异完全混杂，表里同时给了 `gold_evidence_role` 和
`category` 两列，复核时请按类别分层再看，不要单看来源。

## 脱敏样本（6 题，题干与金答案来自公开题库）

| 题号 | 类别 | 断点 | 题干（略） | 发生了什么 |
|---|---|---|---|---|
| `gpt4_7fce9456` | multi-session | extract | 看了几套房才给 Brookside 的联排别墅下 offer？ | ChunkFailure，32 次上限顶穿，extracted=0 |
| `70b3e69b` | single-session-assistant | retrieve | 之前聊政治气候影响时提到的那个人是谁？ | 提取成功，recall@5=0，金答案 Manolo García 一次没召回 |
| `720133ac` | multi-session | project/cite | Lola 看兽医加驱虫药一共多少钱？（金答案 $75） | recall@5=1.0，但 cited_sessions 为空、citation_valid=false |
| `54026fce` | single-session-preference | answer/use | 想和同事保持联系，有什么建议？ | recall@5=1.0、引用有效，判分仍错 |
| `031748ae_abs` | abstention | answer | 刚当上软件工程经理时带几个工程师？ | 应拒答却作答，作答前召回了 1 个会话并引用了它 |
| `6456829e_abs` | abstention | answer（口径打架） | 最初种了几株番茄和辣椒？ | 作答内容被判分判对，但本题应拒答；judge 与拒答指标不一致 |

完整 500 题见 CSV，按 `loss_stage` / `category` 筛选即是各环节的全部清单。

## 交付给复核方时附什么

1. 本文件 + CSV（仓库内即公开链接；CSV 不含私人记忆，只有公开
   LongMemEval 合成题的题号、类别与阶段指标）。
2. 对应 commit：`c46a6fa`（本轮）；对照轮 `365e83a`。
3. 评测协议：`v2-full-route-B`（工作区报告 `longmemeval-rerun2-20261106.md`
   §6 有完整跑法、命令与数据集 SHA）。
4. 样本：上表 6 题 + CSV 里 `loss_stage` 各档可任意抽查。
5. 未公开的私人记忆不在表里，也不需要发。

## 下一步（本表回答不了、需要另做的事）

1. claim 级埋点：adapter 在投影时记下每条 claim 是否进上下文，
   才能把 project/cite 的 211 题再拆成「投影丢了哪条 claim」。
2. 单查 8 题静默 extracted=0（全在 single-session-assistant）。
3. 拒答 fail-open：给 6 道错题做「去掉召回证据是否还会作答」的对照，
   再定是不是召回导致的。
