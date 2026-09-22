# Benchmark（评测工具）

给记忆提取装一把「量尺」——量化准不准，而不是「感觉不错」。

## 指标

1. **提取准确率（Recall / Precision）**：该提取的记忆，提没提到、有没有多提
2. **False Write Rate**：该不记的内容（临时情绪/反话/注入），有没有被自信写进长期记忆
3. **路由准确率**：提取出的 Claim，有没有路由到正确文件
4. **冲突识别率**：与旧记忆冲突时，有没有正确识别

## 目录约定

```
benchmark/
├── samples/    # 输入日记（自己准备，脱敏后）
├── golden/     # 标准答案（人工标注：draft 草稿 → confirmed 确认）
├── results/    # 跑分输出
└── match.py    # 语义匹配工具（temp=0 确定性匹配）
```

## 流程

1. `samples/` 放输入日记
2. 生成 `golden/*_draft.json`（AI 初标，`status: draft`）
3. **人工逐条确认**，改 `status: confirmed`
4. 用 confirmed 跑分，得到可信指标

## ⚠️ 重要

- `*_draft.json` 是 AI 草稿，未经人工确认，**不可当最终标准**。
- 只有 `*_confirmed.json` 才能用于可信评分。
- **绝不拿系统自己的产出当标准答案。**

## 语义匹配

`match.py` 提供 `strict_match(expected, predicted)`，用本地模型做语义判断（temp=0 确定性），不依赖字符串相等。

---

## 2026-09-22 解释边界（追加）

- 当前最终结果：expected 393、matched 277、missed 116、extra 272、recall 0.705、precision 0.505。
- `False Write = 0` 只适用于已定义并实际运行的对抗样本，不代表真实环境零错误。
- extra 尚未全部经过独立人工复核，不能全部视为误提取，也不能全部视为标准答案漏标。
- 样本来自单一用户且按文件大小选择，匹配器包含单次 LLM 判断；结果仅用于内部版本比较。
