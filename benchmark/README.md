# Benchmark（评测工具）

给记忆提取装一把「量尺」——量化准不准，而不是「感觉不错」。

## 脚本

| 脚本 | 作用 | 依赖 |
|---|---|---|
| `measure.py` | 存储层合成测量（解析/索引/查找耗时、峰值内存） | 无 |
| `match.py` | 确定性 Claim 匹配器（CJK bigram + 英文/数字 token + 可选 embedding） | 无；设 `STELLAMEM_EMBED_URL` 可启用向量相似度 |
| `report.py` | 四个指标分开报 | `match.py` |
| `enrich_gold.py` | 给标准答案回填原文依据，补不出的标 `weak` | `match.py` + 语料 |
| `audit_extras.py` | 复核「多检」条目，分真记忆/细节/无源 | `match.py` + 语料 |

语料（samples / golden / results）默认在 `<USER_HOME>\.dsh\memories\benchmark`，
可用 `STELLAMEM_BENCH_DIR` 指向别处。

## 四个指标

`report.py` 分开报，不再合成一个数字：

1. **提取召回**：该记的有没有被提取
2. **提取精确**：提出来的有多少是真的
3. **写入准确**：进入正式记忆的条目里，有多少对得上标准答案
4. **注入可用率**：写进去的条目里，有多少能通过投影闸门被真正注入

```bash
python benchmark/report.py --pred-dir <每篇一个 json> --out results/report.json
```

预测文件格式：

```json
{"sample": "sample_001", "claims": [{"kind": "fact", "value": "...", "status": "pending"}]}
```

## 匹配器为什么换了

旧口径用「LLM 单次判断」做匹配，同一对文本换次采样就可能变，且脚本未随仓发布，
因此 0.705 / 0.505 无法复现。现在的匹配器：

- 字符级 CJK bigram + 英文/数字 token，输出确定；
- 数字/路径完全一致时加权；
- 设 `STELLAMEM_EMBED_URL=http://127.0.0.1:8082` 时改用 bge-m3 向量余弦，
  中文/英文混排的召回明显更好（实测 sample_030：词面 3/12，向量 7/12）。

阈值 `MATCH_THRESHOLD = 0.52`，由 `tests/test_match.py` 的正负样本锁住。

## 标准答案的 `evidence_status`

`enrich_gold.py` 给每条标准答案回填原文句子：

- `anchored`：能在对应 sample 里找到高覆盖原句
- `weak`：找不到（可能是改写、跨篇、或标注时凭记忆写的）

2026-10-31 的结果：393 条中 **241 anchored / 152 weak**。`weak` 单独统计，
不混进可信分数。

## 历史口径（保留）

- 2026-09-20 报告：expected 393、matched 277、missed 116、extra 272、
  recall 0.705、precision 0.505。
- 该结果来自单用户私有样本与未随仓发布的匹配器，**不可在本仓独立复现**，
  不应作为证据引用。
- 272 条「多检」并未逐条判错。已复核的 205 条中：真记忆 62、细节 132、
  无源 7、原标无源但能在原文找到 4；另有 67 条未复核。
- 修正后的量级：精确率约 65%，召回率约 76%。

## 存储测量（measure.py）

用合成数据测量 canonical markdown 的解析、索引和查找耗时与峰值内存
（默认 10 / 100 / 1k / 10k / 100k 条），只描述存储层，不声称提取质量。
