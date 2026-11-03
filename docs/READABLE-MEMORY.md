# 可读记忆：外部致谢与采用说明

背景：Ant International《Text as a universal interface for transferable
personalisation》（EMNLP 2026，Senior Area Chair Highlight Award）提出
"可读 AI 记忆"——记忆应该是纯文本、可读、可审计、可改；跨系统传递时
只传提炼后的摘要，不搬原始行为数据。Stellamem 从第一天就是文本 claim +
审计链 + 人工批准，所以这次是**印证 + 补两小件**，不是路线大改。

## 外部致谢：Ant International 可读记忆研究

- **来源项目**：Ant International 研究《Text as a universal interface for
  transferable personalisation》（EMNLP 2026，获 Senior Area Chair Highlight Award）
- **采用的功能**："记忆就该是人话"的设计原则——记忆以纯文本呈现，可读、
  可审计、可改；跨系统传递时只传提炼后的摘要，不搬运原始行为数据
- **为什么采用**：该研究以获奖论文的形式印证了 Stellamem 的基础路线
  （文本 claim + 审计链 + 人工批准）；"只传摘要、不搬原文"恰好是桥接治理
  一直缺少的那条明文原则；把偏好写成简短纯文本摘要，是常客偏好卡的天然形态
- **我们的适配与差异**：①桥接规则写成 ADR-0004 明文原则，并配人工批准
  检查清单；②偏好摘要做成一等记忆类型 `preference_summary`，且每条陈述
  强制带证据（无证据拒绝写入）——这是原文没有、我们按自身教训加严的；
  ③未采用其 AlignXplore+ 模型与两阶段训练（不开源、投入产出比不划算），
  见"未采用部分"一节

## 采用 1：桥接"只传摘要、不搬原文"明文规则

- 位置：`docs/adr/0004-summary-only-bridge.md`。
- 内容：过桥的永远是提炼后的摘要文本；原始日志、原始行为数据永不过桥；
  人工批准检查清单（无原文混入 / 摘要忠实 / 无推断 / 只含模板字段）。
- 代码：`compiler/bridge.py` 新增 `raw_source_hits(fields)` ——
  噪音过滤器，标记疑似原始日志行/聊天 JSON 原文/礼物事件原始行，
  只给审批人提示，不自动拒绝（与 BLACKLIST 同级，真正边界永远是人工看全文）。
- 实践本来已是如此（6 张常客桥接卡都是摘要），这次是把实践写成明文。

## 采用 2：偏好摘要做成一等记忆类型

- 新 kind：`preference_summary`（`compiler/preference_summary.py`）。
- 字段：subject、text（短，plain language，上限 500 字）、
  evidence（每条陈述的证据引用，`[{statement, evidence}]`）、
  updated_at、version、status（pending/active/withdrawn）。
  新建和更新都是 pending，具名人类 `approve` 之后才是 active。
- 铁律（用户 2026-11-03 亲定，写进校验）：每条陈述必须有证据支撑，
  禁止想当然式推断。证据缺失 = 拒绝写入 + 记审计。
  - 反例 1："送了 235 个小心心" → 不许写成"喜欢小心心特效"。
  - 反例 2："晚上来" → 不许写成"偏好晚上"。
  - 证据只能支持证据本身。
- 模块强制的是结构（evidence 非空）；证据是否真正支撑陈述（忠实度）
  是人工评审的判断 —— 模块管"有没有"，人管"对不对"。
- 能力：create / update（version+1，status 回到 pending）/ approve（具名人类，
  pending → active）/ withdraw（撤回，历史保留）/ rollback（追加一条 pending，
  正文来自指定旧版本，不改旧记录）/ get / history / list_active。
  每个版本保存当时的 text、evidence、status，只追加。
  `audit_path` 上的 JSONL 是这份历史的账本：只追加、写后 fsync、
  不覆盖已写字节。新的 `PreferenceCardStore` 从同一路径重建全部版本、
  状态和批准人。最后一行残缺时跳过并记 fail-closed 日志，更早的版本保留。
  拒绝写入同样记审计。批准仍然要求非空的具名 approver。
- 适用：常客偏好卡、用户偏好画像等"短、活、只讲偏好"的记忆。

## 未采用部分：AlignXplore+ 与两阶段训练

论文训练了一个 8B 模型（AlignXplore+），两阶段训练"写出好的偏好摘要"：
先生成候选摘要、用用户未来真实选择做压力测试、只保留被证实正确的；
再强化长期更新后依然有用的摘要。未采用的原因：

1. 模型不开源，无代码可借鉴；
2. 训练管线（候选生成 + 未来选择验证 + 强化）是研究级工程，
   当前阶段投入产出比不划算；
3. 它的"压力测试"思想可用轻量版替代：偏好卡被实际召回/使用的频率
   本身就是质量信号（与 decay 的 last_access 思路相通），以后再做。

## 后续可做（不阻塞本次）

- `preference_summary` 接入 recall/注入（排序与展示）；
- 偏好卡的使用频率回流为质量信号（轻量版"压力测试"）；
- 桥接摘要的忠实度抽查流程（人工抽检比例）。
