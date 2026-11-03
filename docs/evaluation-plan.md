# 评估计划

状态：**计划中，未验证。** 本文没有在 Stellamem 上跑出任何分数。Stellamem **没有公开基准分数**。下面的数据集、题量和费用都来自 2026-11-04 的外部清单；清单自己把一部分运行结论标成未验证。本仓库没有把这些步骤接进代码，也没有复跑。

合入 `main` 不是评测通过。Stage E 仍是 NOT MET，Stage F 仍关闭。见 `docs/verification/STAGES_E_F_STATUS.md`。

## 不采用 mem0ai/memory-benchmarks

外部清单记录该仓库 README 写明目前只支持 Mem0 Cloud 与 Mem0 OSS 后端，不能直接接自定义后端。Stellamem 不走这条。许可见 `docs/ACKNOWLEDGEMENTS.md`。这不等于 Mem0 没有别的评测入口；这里只采用清单里引用的那句 README。

## 第一步：MemoryAgentBench 的 FactConsolidation

先做这一步，因为它用子串打分，不需要评判模型。

- 来源：HUST-AI-HYZ/MemoryAgentBench（清单记 MIT，ICLR 2026）。未接入。
- 目标子集：Conflict Resolution 里的 FactConsolidation（先 `sh`，需要时再看 `mh`）。清单写打分函数是 `substring_exact_match`，**不需要 judge**。Accurate Retrieval 也走子串；LongMemEval 与 `infbench_sum` 才要 gpt-4o 判分。第一步不跑那两类。
- 为什么先做：它测的是增量写入下的冲突处理，贴近本仓库已有的 `SUPERSEDES` / `CONTRADICTS`。这是计划中的理由，不是已经测得的结论。
- 打算怎么接：清单写对方没有插件接口，后端写死在 `agent.py` 的 `AgentWrapper` 里。计划是仿 mem0 分支增加初始化、处理函数和 `send_message` 分发。写入一个 chunk 与检索后作答都还没写。
- 回答模型：清单写对方的客户端是 `OpenAI()`，读 `OPENAI_API_KEY` / `OPENAI_BASE_URL`，因此可以把基址指到本地 llama.cpp 的兼容接口。**未验证。**
- 题量：清单写 CR 有 6k / 32k / 64k / 262k 四档，`--max_test_queries_ablation N` 可以限题。`factconsolidation_sh_6k` 加 N=20～50 被写成最小可行档。每档确切题数清单写明没查到（HF datasets-server 超时），下载前不要写成已知数字。
- 费用：这一步按子串打分，不调用 OpenAI judge。若回答模型走本地服务，调用费按清单的推算是 0。该推算未实测。

完成这一步之前，不能写「冲突治理已有独立基线分」。

## 第二步：LongMemEval 自抽 100 题

第一步有结果之后再做。这一步要花钱，而且只覆盖英文。

- 来源：xiaowu0162/LongMemEval（清单记 MIT）。官方打分脚本是上游的 `src/evaluation/evaluate_qa.py`。本仓库没有该脚本，也没有接上。
- 没有官方 dev 100 题。清单记录的官方文件是 `longmemeval_oracle`、`longmemeval_s_cleaned`、`longmemeval_m_cleaned`（HF：`xiaowu0162/longmemeval-cleaned`）。
- 计划：从 `longmemeval_s_cleaned` 按 6 类分层抽 100 题，固定随机种子，把题号清单放进仓库，报告写明这是**自抽子集**。抽取尚未做，种子尚未定。
- 计划中的流水线：自写适配器，把会话灌入、检索、生成回答，写出 hypothesis 文件，再用官方 `evaluate_qa.py` 打分。judge 需要 OpenAI，会花钱。
- 数据集是英文。Stellamem 的提取提示要求 value 与证据跟随源文语言。这个分数如果将来产生，只代表英文能力。
- 仓库里已有另一套东西：`benchmark/longmemeval/` 冻结了 140 道官方题，用本地模型做有界实验，**不用**官方 `evaluate_qa.py`。`docs/verification/AUTOLOG_BOUNDARY.md` 与 `docs/verification/LONGMEMEVAL_RUNTIME_V2.md` 写明没有最终 v2 质量分，中断的那次不是质量通过。不要把这套脚手架写成第二步已经完成，也不要把它没有分数说成 0 分。

## 明确不写成 Stellamem 的成绩

- MemOS README 自报的 LoCoMo 88.83 与 LongMemEval 89.20 是对方 README 上的数字（2026-11-04 清单）。不是 Stellamem 的分数。
- 旧日记实验的 recall ≈ 0.705 / precision ≈ 0.505：`benchmark/README.md` 写明旧的 LLM 匹配器没有随仓发布，现行匹配器复现不了这两个数。它们不是公开基准分。
- Stage E 的 reader Recall@1 = 1.00 只覆盖 80 条合成投影契约用例。`docs/verification/STAGES_E_F_STATUS.md` 禁止拿它宣称成熟度。

## 顺序

1. MemoryAgentBench FactConsolidation，子串打分，无 judge。计划 / 未验证。
2. LongMemEval 自抽 100 题，官方 `evaluate_qa.py`，OpenAI judge，花钱，仅英文。计划 / 未验证。

外部项目的来源、许可和适配边界见 `docs/ACKNOWLEDGEMENTS.md`。
