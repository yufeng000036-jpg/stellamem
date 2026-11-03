# 可读记忆：这次偷了什么、没偷什么

背景：Ant International《Text as a universal interface for transferable
personalisation》(EMNLP 2026, SAC Highlight Award) 提出"可读 AI 记忆" ——
记忆应该是纯文本、可读、可审计、可改；跨系统传递时只传提炼后的摘要，
不搬原始行为数据。Stellamem 从第一天就是文本 claim + 审计链 + 人工批准，
所以这次是**印证 + 补两小件**，不是路线大改。

## 偷了 1：桥接"只传摘要、不搬原文"明文规则

- 位置：`docs/adr/0004-summary-only-bridge.md`。
- 内容：过桥的永远是提炼后的摘要文本；原始日志、原始行为数据永不过桥；
  人工批准检查清单（无原文混入 / 摘要忠实 / 无推断 / 只含模板字段）。
- 代码：`compiler/bridge.py` 新增 `raw_source_hits(fields)` ——
  噪音过滤器，标记疑似原始日志行/聊天 JSON 原文/礼物事件原始行，
  只给审批人提示，不自动拒绝（与 BLACKLIST 同级，真正边界永远是人工看全文）。
- 实践本来已是如此（6 张常客桥接卡都是摘要），这次是把实践写成明文。

## 偷了 2：偏好摘要做成一等记忆类型

- 新 kind：`preference_summary`（`compiler/preference_summary.py`）。
- 字段：subject、text（短，plain language，上限 500 字）、
  evidence（每条陈述的证据引用，`[{statement, evidence}]`）、
  updated_at、version、status（active/withdrawn）。
- 铁律（用户 2026-11-03 亲定，写进校验）：每条陈述必须有证据支撑，
  禁止想当然式推断。证据缺失 = 拒绝写入 + 记审计。
  - 反例 1："送了 235 个小心心" → 不许写成"喜欢小心心特效"。
  - 反例 2："晚上来" → 不许写成"偏好晚上"。
  - 证据只能支持证据本身。
- 模块强制的是结构（evidence 非空）；证据是否真正支撑陈述（忠实度）
  是人工评审的判断 —— 模块管"有没有"，人管"对不对"。
- 能力：create / update（version+1）/ withdraw（撤回，历史保留）/
  get / list_active；审计 JSONL 只增，拒绝写入同样记审计。
- 适用：常客偏好卡、用户偏好画像等"短、活、只讲偏好"的记忆。

## 没偷：AlignXplore+ 与两阶段训练

论文训练了一个 8B 模型（AlignXplore+），两阶段训练"写出好的偏好摘要"：
先生成候选摘要、用用户未来真实选择做压力测试、只保留被证实正确的；
再强化长期更新后依然有用的摘要。不偷的原因：

1. 模型不开源，无代码可抄；
2. 训练管线（候选生成 + 未来选择验证 + 强化）是研究级工程，
   当前阶段投入产出比不划算；
3. 它的"压力测试"思想可用轻量版替代：偏好卡被实际召回/使用的频率
   本身就是质量信号（与 decay 的 last_access 思路相通），以后再做。

## 后续可做（不阻塞本次）

- `preference_summary` 接入 recall/注入（排序与展示）；
- 偏好卡的使用频率回流为质量信号（轻量版"压力测试"）；
- 桥接摘要的忠实度抽查流程（人工抽检比例）。
