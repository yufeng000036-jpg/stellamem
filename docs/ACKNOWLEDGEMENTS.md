# 外部致谢

本文件只记录文档里点到的外部项目：来源、借的或打算借的想法、为什么、打算怎么适配、许可、以及有没有进代码。

许可来自 2026-11-04 外部方案清单所记的 GitHub 许可证。这次改文档没有再请求 GitHub API。清单把一部分运行结论标成未验证；那些句子下面单独标明。未接入就是未接入。

## 仅作对照（没有把代码接进来）

### Mem0

- **来源项目**：Mem0 公开 README；评测仓库 mem0ai/memory-benchmarks。
- **借用的想法**：没有借用实现。对照的是「本地 / 自托管 / 云」这几种运行方式和公开跑分是否存在。
- **为什么写进文档**：避免把「能在本机跑」或「能检索」写成只有 Stellamem 有的差异。
- **适配**：不接入 memory-benchmarks。清单引用该 README：「Currently supports the Mem0 Cloud and OSS versions」。这是 README 的范围声明，不是「Mem0 没有其他接口」的证明。
- **许可**：memory-benchmarks 在清单里记为 Apache-2.0。Mem0 主仓库的许可不在该清单的单独一行里，这里不写成已核实。
- **状态**：未接入。

### MemOS

- **来源项目**：MemTensor/MemOS。
- **借用的想法**：没有借用实现。对照的是同一宿主上的本地插件描述。
- **为什么写进文档**：清单记录其 README（2026-03-08 一条）描述了面向 OpenClaw/Hermes 的本地插件，存储为 SQLite + FTS5 + 向量，并带 Memory Viewer；同一 README 自报 LoCoMo 88.83、LongMemEval 89.20。因此 Stellamem 不把本地运行或检索写成差异点。这些分数是对方自报，本仓库未复现。
- **适配**：不接入该插件，不引用其分数作为 Stellamem 的成绩。
- **许可**：清单记 Apache-2.0。
- **状态**：未接入。README 未提及的能力不能写成「对方没有」。

### Graphiti

- **来源项目**：getzep/graphiti。
- **借用的想法**：没有把代码接进来。对照的是「事实指回来源」和双时间。清单记录其 README 写每条事实回指来源 episode，并保留时间历史。README 未提及回滚、人工复核收件箱、密钥门禁。未提及不等于没有。
- **为什么写进文档**：本仓库已经有 `valid_to` / `expired_at`（`compiler/store.py`、`compiler/dual_time.py`）。可对照的缺口是「从事实点回原始 episode」的展示，当前代码没有这条可点击回链。
- **适配**：若以后做回链，只补展示与指针，不替换现有的 Claim 版本链。尚未做。
- **许可**：清单记 Apache-2.0。
- **状态**：未接入。

### Letta

- **来源项目**：letta-ai/letta；配套仓库 letta-ai/sleep-time-compute。
- **借用的想法**：计划借鉴「空闲时整理记忆，主路径不堵住」。没有借用其代码。清单记录 Letta README 描述可本地或自托管 App Server。README 未提及来源哈希、回滚、人工复核、密钥门禁。未提及不等于没有。
- **为什么写进文档**：和夜间整理的方向同题。本仓库的 `compiler/nightly.py` 与 `compiler/sleep_consolidation.py` 是自己的确定性文本流程，不是 Letta 的 sleep agent。
- **适配**：候选 A 若做，整理结果仍进人工复核，不自动注入。尚未做。
- **许可**：清单记 letta 为 Apache-2.0，sleep-time-compute 为 MIT。
- **状态**：未接入。

### LoCoMo

- **来源项目**：snap-research/locomo。
- **借用的想法**：没有。文档里出现它，只因为 MemOS README 自报了一个 LoCoMo 分数。
- **为什么写进文档**：把该分数标成对方自报，避免被读成 Stellamem 的成绩。
- **适配**：不接入。
- **许可**：清单记为未声明（NOASSERTION）。使用前要再确认。
- **状态**：未接入。

## 计划借鉴，尚未实现

这些想法服务候选 A 或 P3。候选 A 的规则以 README 为准：每条归纳必须引用原始记忆，至少 3 条证据且跨至少 2 个会话，一律人工复核，不自动注入。现有 `mine_patterns` 不是这个规则。

### Generative Agents

- **来源项目**：joonspk-research/generative_agents，论文 2304.03442。
- **计划借鉴的想法**：reflection：重要性累计过阈值才触发；高层洞见必须引用支撑记忆的编号。
- **为什么**：候选 A 要求归纳结论带回证据，而不是只给一句概括。
- **打算怎么适配**：新的归纳结果要引用本仓库的原始记忆 id，并满足 ≥3 条证据、跨 ≥2 个会话，然后进入 `compiler/inbox.py` 的人工复核。不自动注入。阈值与触发方式尚未定，也未写代码。
- **许可**：清单记 Apache-2.0。
- **状态**：未实现。

### Nemori

- **来源项目**：nemori-ai/nemori。
- **计划借鉴的想法**：先按话题切开 episode，再用已有知识预测新 episode，只把预测错的部分收成新知识，避免把同一事实再堆一遍。
- **为什么**：现有 `mine_patterns` 是把相同 subject/predicate/value 重复计数，不是「只保留预测失败的部分」。
- **打算怎么适配**：若与 Generative Agents 的引用规则一起用，输出仍是待复核的归纳，不自动注入。未写代码。
- **许可**：清单记 MIT。
- **状态**：未实现。

### fastembed 与 sqlite-vec

- **来源项目**：qdrant/fastembed；asg017/sqlite-vec。
- **计划借鉴的想法**：进程内向量，少一个单独的 embedding 端口；向量与现有 SQLite 放在一起。
- **为什么**：当前语义通道走 llama.cpp 的 HTTP `/v1/embeddings`（`compiler/semantic.py` 默认 `127.0.0.1:8082`）。这是 P3 备选，不是已经选定的方向，也不是差异点。
- **打算怎么适配**：先确认模型列表里有合适的中文模型，再考虑替换 HTTP 嵌入。清单记 fastembed 0.9.1 的 `TextEmbedding` 列表里有 BAAI/bge-small-zh-v1.5（MIT，512 维）。同一清单写 jina-embeddings-v3 为 CC-BY-NC-4.0，非商用，排除。清单写 Windows 轮子可下载，但**未在 Windows 实机跑过**。本仓库两种都未接入。语义服务不可用时，现行代码是静默退回字面检索（`compiler/search.py`），不是这里的方案。
- **许可**：两个仓库在清单里都记为 Apache-2.0。模型自身的许可另计，见上。
- **状态**：未实现，未选定。

### litestream

- **来源项目**：benbjohnson/litestream。
- **计划借鉴的想法**：在写前快照之外，对 SQLite WAL 做持续复制。
- **为什么**：快照是按次的文件备份。持续复制是另一层，尚未决定要不要做。
- **打算怎么适配**：不替换 `compiler/snapshots.py` 的确认短语和 SHA256 校验。未接入。
- **许可**：清单记 Apache-2.0。
- **状态**：未实现，未选定。

## 评测来源（计划中的使用，不是已接入）

### MemoryAgentBench

- **来源项目**：HUST-AI-HYZ/MemoryAgentBench。
- **计划使用的部分**：FactConsolidation 的子串打分，用来测冲突处理。见 `docs/evaluation-plan.md`。
- **为什么**：不需要 judge，且和 `SUPERSEDES` / `CONTRADICTS` 同题。
- **打算怎么适配**：在对方仓库的 agent 分支里加 Stellamem 后端。不把对方代码拷进本仓库当作已经集成。
- **许可**：清单记 MIT。
- **状态**：计划 / 未验证。未跑分。

### LongMemEval

- **来源项目**：xiaowu0162/LongMemEval。
- **计划使用的部分**：从 `longmemeval_s_cleaned` 自抽 100 题，用上游 `src/evaluation/evaluate_qa.py` 打分。
- **为什么**：官方评测脚本可以打分，但没有官方的 100 题 dev 集，所以必须自己抽样并写明。
- **打算怎么适配**：适配器只负责灌会话、检索、作答和写出 hypothesis。打分仍用官方脚本。judge 要 OpenAI，数据集是英文。
- **和仓库里已有脚手架的区别**：`benchmark/longmemeval/` 是本仓库的有界本地实验（140 道冻结题，本地模型）。它没有官方 `evaluate_qa.py`，也没有公开分数。
- **许可**：清单记 MIT。
- **状态**：计划 / 未验证。脚手架在仓库里，分数不在。
