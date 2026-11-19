# 验收记录 · 2026-11-19

审查起点为 main 412d5e0262fb8d0f4daf50b9e026115f7050ee17。README 将 DSH 定位为既有生产宿主；本轮无用户机器访问权限，未核实机器的部署版本。OpenClaw 的宿主采集、上下文投影与生命周期和模型推理是不同职责。Jarvis 作为独立客户端调用同一 compiler/store、inbox、Transaction、deletion_chain 和 adapters/openclaw/project；它不挂载 DSH 数据，不安装 OpenClaw 钩子，不另设向量库。SQLite 只存待办、控制状态和操作日志。现有 HTTP /v1/recall、受限主体令牌和证据接口也经源码审查，本版选用隔离文件根上的核心接口。

| 上游 PR | 核查时状态 | head 对应的 PR 工程检查与边界 |
|---|---|---|
| [130](https://github.com/yufeng000036-jpg/stellamem/pull/130) | 已合并，投影相关性、排序和名额分配 | [失败](https://github.com/yufeng000036-jpg/stellamem/actions/runs/44248485394)，两个 Windows 核心回归 job。PR 的投出率不能用作回答正确率 |
| [140](https://github.com/yufeng000036-jpg/stellamem/pull/140) | 草稿，schema 25 有界读取 | [通过](https://github.com/yufeng000036-jpg/stellamem/actions/runs/44350958192)。隔离的真实宿主进程和替身模型已有传输证据，宿主生命周期、撤销和真实模型语义仍待验收 |
| [141](https://github.com/yufeng000036-jpg/stellamem/pull/141) | 草稿，叠在 #139 上，调用方确认游标 | [失败](https://github.com/yufeng000036-jpg/stellamem/actions/runs/44351016803)，macOS 3.10 job。尚无跨组件事务保证 |
| [145](https://github.com/yufeng000036-jpg/stellamem/pull/145) | 草稿，冻结语料与本地阅读器配对研究 | [通过](https://github.com/yufeng000036-jpg/stellamem/actions/runs/44354499666)。不能外推出用户 Windows 或 DeepSeek 答对率 |
| [146](https://github.com/yufeng000036-jpg/stellamem/pull/146) | 已合并，证据充分性，以及受限主体过滤后重算 | [失败](https://github.com/yufeng000036-jpg/stellamem/actions/runs/44390425506)，quality 的格式检查 |
| [147](https://github.com/yufeng000036-jpg/stellamem/pull/147) | 已合并，结构化证据与引用检查 | [失败](https://github.com/yufeng000036-jpg/stellamem/actions/runs/44392425426)，quality 的格式检查 |

## 本轮实测

Linux、Python 3.12.14、Node 24.22.3、SQLite 3.53.1。测试创建真实文件，调用真实治理、投影、数据库与备份路径；跨会话场景启动新 Python 进程，投影运行在独立 Node 进程。没有用户的 Windows 麦克风、LM Studio 实例或生产 DSH。未启动 OpenClaw 宿主。新增代码的 Ruff 检查通过。另跑上游 isolated_lifecycle 8 项、deletion_chain 9 项，全部通过。

| 指标 | 结果 | 分母含义 |
|---|---:|---|
| 固定受控任务成功 | 44/44 | 控制、权限、文件、跨进程和恢复等断言，含预期拒绝；逐项记录见 linux-run.txt |
| 样本记忆召回@5 | 2/4 | 同一条更正记忆的四种问法；“星桥会议”“周六下午”命中，“星桥”“改期后的聚会时间”漏召 |
| 实际本地模型最终回答正确率 | 未测 | 协议替身不具备语言理解能力；成功传递上下文、选中有效编号或排除伪造编号只计协议行为 |
| 隔离主体检查 | 所测样本无泄露 | 同主题 user、persona、viewer 记录并存；合法 user 为阳性对照。仅覆盖该实验路径 |

44 项共同运行在一个有状态的虚构工作区，表明受控验收超过 90% 目标；样本量和相关性不足以估算开放任务的真实成功率。没有执行独立人工验收。真实用户输入的 Windows 随机码、LM Studio 回答正确率、OpenClaw 撤销传播及两端完整宿主验收仍待做。Windows CI 即使通过，仅代表托管运行器的软件场景。

## 优先修复方向

下一轮先在用户同意的本地离线模型上冻结一组多主题中文问题，加入改述、两字关键词、同名冲突、已纠错和无证据案例。逐题记录应召回的来源、实际前五、最终回答与耗时。先改造成漏召的部分，再看改善是否传到最终答案。对已投出但选错的案例单独处理阅读器。期间保留所有负对照和成本边界。
