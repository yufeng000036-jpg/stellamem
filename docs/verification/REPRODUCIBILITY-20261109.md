# LongMemEval 第四轮（2026-11-08）可复现对照说明

> 轮次：route B 全量 500 题，main `9034cdec0bf709ee5d66c281b9af48bf17c515f7`，
> 模型 `deepseek-flash`（回答 + 判分），协议 `v2-full-route-B`，
> 数据集 SHA-256 `821a2034d219ab45846873dd14c14f12cfe7776e73527a483f9dac095d38620c`
> （与冻结 `freeze.py` 的 `DATA_SHA` 一致）。
> 费用实测 $1.2862（10,871 次调用），报告见 `~/workspace/longmemeval-rerun3-20261108.md`。
>
> 本文件只记录“如何不花钱复现第四轮数字”，不改写任何历史分数。
> 修正后的分数（174/500）来自 PR #100 的评分完整性审计器，
> 原始分数（177/500）保持原样；两者口径见 §3。

## 1. 数字（复现目标）

| 指标 | 值 | 口径 |
| --- | --- | --- |
| 判分 main / candidate | 177/500 = 0.354 | `results.json` 各 case `arms.*.judge.judge_label` 计数 |
| 判分 none / direct | 30/500 = 0.060；294/500 = 0.588 | 同上 |
| 修正后判分 main（PR #100） | 174/500 = 0.348 | 审计器在 tracking CSV 上检出 3 个历史误判并剔除 |
| 召回@5 main | 0.857（470 道非拒答题均值） | `retrieval_recall_at_5` 均值 |
| 正确拒答 exact | 24/30 = 0.800 | 失败集 {031748ae, 0ddfec37, 2133c1b5, 6456829e, gpt4_372c3eed, gpt4_93159ced}（`_abs` 后缀） |
| 字面匹配 main | 74/500 = 0.148 | `exact_match` 计数 |
| 提取失败 main | 32（31×ChunkFailure + 1×ModelError） | `error` 非空 |
| 32 次调用上限顶穿 | 25 题 | merge.py 的 budget-breach 签名 |
| 费用 | $1.2862 / 10,871 次 | usage.jsonl 按 flash off-peak 价重算 |

## 2. 材料在哪（2026-11-09 清点，磁盘 `~/workspace/longmemeval-rerun3-20261108/`）

总计 124 MB 磁盘占用、20,398 个文件。关键项：

| 材料 | 位置 | 大小 | 状态 |
| --- | --- | --- | --- |
| 合并结果 | `work/full/results.json` | 8.8 MB | ✅ 在，有全部 500 题 + summary + cost |
| 分片结果 | `work/full/shard{0..3}/results.json` | 各 2.2–2.3 MB | ✅ 在，各 125 题，repo_sha 均为 9034cde |
| 调用日志 | `work/full/shard{0..3}/usage.jsonl` | 各 ~0.8 MB，共 10,871 行 | ✅ 在 |
| 模型调用缓存 | `work/full/shard{0..3}/cache/` | 8,859 文件；内容 6.7 MB，磁盘占用 ~35 MB（多为小文件块开销） | ✅ 在 |
| 数据集 | `work/longmemeval_oracle.json` | 15 MB，500 题 | ✅ 在，SHA 与 DATA_SHA 一致 |
| claim 追踪 CSV | `work/claim_tracking_20261108.csv` | 1.0 MB，7,851 行 | ✅ 在，且已进仓库（PR #92），与仓库版哈希一致 |
| 追踪表 CSV | 仓库 `benchmark/longmemeval/tracking_table_20261108.csv` | 97 KB | ✅ 在，SHA `fe799435…`，即审计器输入 |
| 投影 sidecar | `work/claim-replay-full/diagnostics/` | 469 文件，内容 2.9 MB | ✅ 在（= 500 − 31 道 ChunkFailure；其余每题 1 个） |
| 重放结果 | `work/claim-replay-full/replay_results.json` | 100 KB，495 题 | ✅ 在（另 5 题因 sidecar 已存在被跳过，见 §5） |
| 重放缓存（副本） | `work/claim-replay-full/cache/` | 8,859 文件，与分片缓存逐文件哈希全等 | ✅ 在，是分片缓存的完整复制 |
| 重放 manifest | `work/claim-replay-full/manifest.json` | 104 KB，500 条 | ✅ 在 |
| 原始 worker 输出 | `work/full/shard{0..3}/raw/` | 2,000 文件（每题每臂 1 个） | ✅ 在 |
| 拒答验证/实验 | `work/abstain-validation/validation_results.json`、`work/abstain-experiment/abstain_results.json` | 各 16 KB | ✅ 在 |
| 运行脚本 | `full.py`、`merge.py`、`run_full.sh`、`run_smoke.sh`、`monitor.py` | 14 / 6.5 / 2.0 / 1.2 / 1.8 KB | ✅ 在 |
| smoke 结果 | `work/smoke/results.json` | 32 KB | ✅ 在 |
| claim-replay-smoke | `work/claim-replay-smoke/` | 仅 `usage.jsonl`（19 KB） | ⚠️ 无 sidecar、无 replay 结果（见 §5） |

仓库（main `e4f5cd9`，2026-11-09 只读克隆核查）已有：
`benchmark/longmemeval/claim_tracking_20261108.csv`、
`tracking_table_20261108.csv`、冻结四文件 + `freeze.py` + `evaluation-lock.json`。
仓库里**没有** `results.json`、缓存、sidecar、oracle。
`.gitignore` 不拦 `benchmark/longmemeval/*.json`（只拦 `benchmark/results/`、
`benchmark/samples/`、`benchmark/golden/`、`*.log` 等），所以本包的精简产物可以进
`benchmark/longmemeval/`，但大体积原始产物仍不进 git（见 §4）。

## 3. 零成本复现：已验证的结果（2026-11-09，零新模型调用）

全部用已保存的材料验证；验证脚本见 `benchmark/longmemeval/verify_replay.py`。

1. **合并复现——字节级一致。** 用 9034cde 的冻结 `evaluate.py` + `freeze.py`
   重建 `merge.py` 依赖的 `/tmp/stellamem-lme-rerun3` 路径后，
   从 4 个分片 `results.json` + `usage.jsonl` 重新跑 `merge.py`，
   输出与存档 `work/full/results.json` **逐字节相同**（9,130,349 字节）。
   唯一的前提是重建那个 `/tmp` 路径——原始路径在虚拟机重启后已不存在（见 §5）。
2. **headline 重算——精确复现。** 直接对 `results.json` 重数：
   main/candidate 判分 177/500、none 30/500、direct 294/500；
   recall@5 0.857270、recall@1 0.581915；字面 74/500；拒答 24/30；
   失败集与报告 §3 逐字一致；费用按 usage.jsonl 重算为 10,871 次、$1.2862，
   分项（提取 $1.1371 / 回答 $0.1004 / 判分 $0.0486 / 预检 $0.0001）全部对上。
3. **修正分 174/500——精确复现。** PR #100（`refs/pull/100/head`，
   `26abafd`，当时未合入）审计器 `score_integrity.py` 的 SHA-256
   `2724bf8b…` 与其提交产物一致；在仓库 `tracking_table_20261108.csv`
   （输入 SHA `fe799435…`，与审计产物所记一致）上重跑，
   summary 与提交的 `score-integrity-20261109.json` **完全一致**：
   legacy 177 → corrected 174，3 个误判为
   `gpt4_a1b77f9c`（temporal-reasoning，ChunkFailure 却判对、空 hypothesis）、
   `6456829e_abs`（拒答题却作答且判对）、
   `gpt4_372c3eed_abs`（ChunkFailure 却判对、空 hypothesis）。
   三题在 `results.json` 原始证据里逐一核对属实。
   审计器自带 12 个单元测试离线全过；其提交 JSON 的 `rows` 只含这 3 行误判，
   不含全部 500 行（完整 500 行输出只存在于本次验证的 /tmp，不在仓库）。
   注意：审计器不证明语义支撑（`semantic_support` 恒为 null），只做
   执行成功 / 空答案 / 拒答一致性 / 引用资格的形式核查。
4. **冻结文件——哈希一致。** 9034cde 的 `worker.py` / `evaluate.py` /
   `PROTOCOL.md` / `manifest.json` 的 SHA-256 与 `evaluation-lock.json`
   所记逐一相同；`freeze.py` 在 9034cde 之后改过（加了 `verify_frozen_files`
   字节数核对），不影响本轮。
5. **缓存完整性——逐文件哈希全等。** 重放缓存副本与 4 个分片缓存的并集：
   8,859 文件、路径集合相同、内容 SHA-256 零差异
   （并集哈希 `cb83c3f9…`，见 `results-20261108-hashes.json`）。
   重放日志：`ran=495 skipped=5 errors=0 physical_calls_total=0 fidelity_mismatch=0`；
   重放的 32 个 error 与 `results.json` 的 main 臂 32 个 error 逐题相同。
   这证明 **main 臂投影重放零新调用**。
6. **验证脚本自检。** `verify_replay.py` 在真实数据上 10/10 通过
   （加审计器时 11/11）；把 1 个 judge 标签翻转为 True 的篡改版
   `results.json` 能被正确判 FAIL。

## 4. 归档策略：什么进 git，什么只留哈希

| 产物 | 处理 | 理由 |
| --- | --- | --- |
| `results-20261108-summary.json`（556 KB） | ✅ 进 git | 每题 verdict（judge/exact/拒答/召回/错误/调用数）+ summary + by_category + cost；**不含答案文本、judge hypothesis、usage 日志、session/turn id 列表**，与冻结 `evaluate.py` 的“不发布对话或预测文本”规则一致；可独立重算 headline |
| `results-20261108-hashes.json`（6 KB） | ✅ 进 git | 全部原始产物的 SHA-256 + 字节数 + 目录级哈希（含缓存并集、sidecar 目录、冻结文件） |
| `verify_replay.py`（15 KB） | ✅ 进 git | 离线验证脚本，标准库 only，零模型调用 |
| 本文件 | ✅ 进 git | 复现说明 |
| 完整 `results.json`（8.8 MB） | ❌ 只留哈希 + 磁盘位置 | 体积大且含答案文本/usage 日志；SHA `dee1d928…`，位置 `~/workspace/longmemeval-rerun3-20261108/work/full/results.json`；可由分片 + usage.jsonl 逐字节重建（§3.1） |
| 数据集 oracle（15 MB） | ❌ 只留哈希 | 上游 LongMemEval 数据，非本仓库产物；SHA `821a2034…` |
| 模型调用缓存（内容 6.7 MB / 磁盘 ~35 MB，8,859 文件；另有 1 份完整副本） | ❌ 只留哈希 | 文件数多、含小文件块开销；并集哈希 `cb83c3f9…`，位置见上表 |
| 投影 sidecar（469 文件，内容 2.9 MB） | ❌ 只留哈希 | 目录哈希 `bb404727…`；其内容已汇总进仓库的 claim CSV |
| raw worker 输出（2,000 文件） | ❌ 不归档 | 含模型原始答案文本；合并结果已覆盖其 verdict |
| 拒答验证/实验结果（各 16 KB） | ❌ 本包不含 | 是事后实验产物，不属于第四轮主记录；磁盘上有，哈希见清单 |

## 5. 不能复现 / 有缺口的部分（直说）

1. **完整端到端重跑不能零成本。** 判分调用（2,000 次，$0.0486）不走
   worker 缓存：`full.py` 的 `judge()` 经 `chat_via_proxy` 直调，
   proxy 只记 usage、不存响应；判分输出只存在于 `results.json` 的
   `judge.usage_calls` 结构里（响应文本在 `judge_response`，无独立缓存键）。
   因此 `full.py` 全量重跑至少需要约 2,000 次新判分调用（约 $0.05），
   且 temperature=0 也不保证判分标签逐题不变——重跑是**新的测量**，
   不是对 177/500 的复现。Worker 侧（提取/回答）只对 main 臂证明了
   零新调用重放；candidate/none/direct 三臂未单独重跑过（缓存文件在，
   但没跑过验证）。整轮重跑仍须用户另批（任务红线：本次未跑）。
2. **`merge.py` 写死了已消失的路径。** 它 `sys.path.insert(0,
   "/tmp/stellamem-lme-rerun3/benchmark/longmemeval")`；该 /tmp 克隆在
   2026-11-08 虚拟机重启时被清空，报告 §5 的两次续跑也是现场重拉的。
   本次复现是在 /tmp 重建该路径后才跑通的。以后任何人复现都必须先重建
   9034cde 的克隆并放到该路径，或改 `merge.py` 用相对路径（改脚本本身
   不在本包范围，且 `merge.py` 不是冻结文件、但改动须另行评审）。
3. **重放跳过 5 题。** `claim-replay-full` 的 `ran=495 skipped=5`：
   被跳过的 5 题（`0bb5a684`、`2a1811e2`、`f0853d11`、`gpt4_2655b836`、
   `gpt4_93159ced`，即 manifest 前 5 条）因 sidecar 已存在而跳过，
   其 sidecar 确实在磁盘上，但本次核查没能确定它们是哪一次运行写下的
   （smoke 重放目录里没有 sidecar；可能是 full 重放中途续跑所留）。
   不影响结论（495 题 0 物理调用 + 5 题 sidecar 齐全），但来源没完全闭环。
4. **smoke 重放无产物。** `work/claim-replay-smoke/` 只有 `usage.jsonl`，
   没有 replay 结果、manifest、sidecar——smoke 重放的投影结果无从复核。
5. **审计器当时未合入。** PR #100 在核查时只存在于 `refs/pull/100/head`，
   main `e4f5cd9` 里没有 `score_integrity.py`。本包的 `verify_replay.py`
   因此把审计步骤做成可选：有审计器时跑，无时 SKIP、不判 FAIL。
   若 PR #100 合入后路径变化，须同步。
6. **前三轮未纳入本次验证。** `longmemeval-full-20261106` /
   `longmemeval-rerun-20261106` / `longmemeval-rerun2-20261106`
   各有自己的 `results.json`（8.7–8.8 MB）与缓存（各 ~84 MB），
   本次只清点了第四轮；第三轮的 175→172 修正随 PR #100 的审计 JSON
   一并复现过（summary 一致），但未对第三轮做合并级复现。

## 6. 复现步骤（零成本）

```sh
# 前提：磁盘上有 ~/workspace/longmemeval-rerun3-20261108/；仓库在 main e4f5cd9 或更新
# 1. headline + 费用 + 列表重算（只读，不写回工作区）
python benchmark/longmemeval/verify_replay.py \
  --results ~/workspace/longmemeval-rerun3-20261108/work/full/results.json \
  --usage-dir ~/workspace/longmemeval-rerun3-20261108/work/full \
  --tracking-csv benchmark/longmemeval/tracking_table_20261108.csv \
  --hashes benchmark/longmemeval/results-20261108-hashes.json \
  --cache-root ~/workspace/longmemeval-rerun3-20261108/work/full \
  --diagnostics ~/workspace/longmemeval-rerun3-20261108/work/claim-replay-full/diagnostics
# 预期：11 passed（含审计器时）/ 10 passed（无审计器时，审计 SKIP），0 failed

# 2. 合并逐字节复现（只读输入；输出写 /tmp）
#    先在 /tmp/stellamem-lme-rerun3 放一份 9034cde 的 benchmark/longmemeval
#    （evaluate.py + freeze.py），再把 merge.py 与 work/full/shard*/{results.json,usage.jsonl}
#    复制到 /tmp 后跑 merge.py，对比 work/full/results.json 的 SHA-256 dee1d928…
# 3. 审计器复现（需 PR #100 的 score_integrity.py）
python benchmark/longmemeval/score_integrity.py \
  benchmark/longmemeval/tracking_table_20261108.csv --out /tmp/score-audit.json
# 预期：legacy 177、corrected 174、3 个误判；exit 1 表示“检出已知误判”，不是失败
```

## 7. 隐私 / 密钥核查

- 拟归档的 4 个文件（精简 summary、哈希清单、验证脚本、本文件）已用仓库
  `tools/secret_scan.py` 的规则扫过：无密钥、无 token、无私钥、无 PII 值。
  精简 summary 不含任何答案文本与 usage 日志。
- 完整 `results.json` / oracle / 缓存 / sidecar / raw 均含合成评测数据
  （LongMemEval 虚构 persona），无真实用户记忆、无密钥；即便如此，
  本包仍不把它们塞进 git，只留哈希。
