# Roadmap（规划）

> 近端落地顺序（P0 生产核验、P1 评估、P2 候选 A、P3 备选）在 README。那张表和本文件的 P1/P2 不是同一套编号。合入 main 不是生产发布。Stage E 未通过，Stage F 关闭。
>
> 本文件列尚未完成的产品项，也保留已经落地、容易被误读成「还没做」的条目。
>
> 优先级：`P0` 近期核心 · `P1` 重要增强 · `P2` 远期 / 待评估。

---

## 安全与治理

| 项 | 优先级 | 说明 |
|---|---|---|
| 规则式（非模型）敏感度检测增强 | P1 | 当前规则式检测不完整（尤其英文临时情绪/反讽）；需扩充与调优 |
| ✅ 自动关系裁决接入主流程 | 已完成 | `judge.py` 已接入 `store.store.MemoryStore.ingest`，REINFORCES / REFINES / SUPERSEDES / CONTRADICTS 自动落版本链与冲突日志 |
| ✅ 密钥自动识别与拦截 | 已完成 | 确定性识别 API Key / Token / 密码等，在任何模型调用**之前**剔除，不写不入 |
| ✅ 敏感数据分级 | 已完成 | `PUBLIC / PRIVATE / SENSITIVE / SECRET`，并分离 storage policy 与 injection policy |
| ✅ Context Projection | 已完成 | Adapter 只注入任务相关的单条 Claim，有全局字符预算 |
| 第三方 Adapter 签名校验 | P2 | 安装前校验 Adapter 来源可信度 |

---

## 框架适配

| 项 | 优先级 | 说明 |
|---|---|---|
| Claude Code Adapter | P1 | 计划支持 |
| Cursor Adapter | P2 | 计划支持 |
| 其他框架 Adapter | P2 | 欢迎社区贡献 |

---

## 体验与分发

| 项 | 优先级 | 说明 |
|---|---|---|
| 可复现 Benchmark 发布 | P1 | 仓库里已有 `benchmark/` 与 LongMemEval 脚手架，没有公开分数。计划见 `docs/evaluation-plan.md`，状态是未验证 |
| 保留与释放流程（`distill.py`） | P1 | `schema.md` 描述的归档/蒸馏/准释放尚未实现 |
| 真实宿主端到端验证 | P1 | 在隔离 OpenClaw 宿主跑通 会话→hook→编译→记忆→adapter→下一轮 |
| GUI | P2 | 桌面产品 GUI 未做。收件箱是 `compiler/inbox.py` CLI。桥接环回页在 `compiler/bridge_gui.py`，本仓库里只被测试导入 |
| 多设备同步 | P2 | 待评估（当前本地优先，不强依赖） |
| 云端加密同步 | P2 | 待评估 |
| 自动 SBOM / 签名发布 | P2 | 软件物料清单与发布物签名 |

---

## 团队与企业（远期）

| 项 | 优先级 | 说明 |
|---|---|---|
| 团队记忆 | P2 | 多人共享记忆空间 |
| 企业权限 | P2 | 角色与访问控制 |

---

## 说明

- 本 Roadmap 会随开发进度更新；已完成的项会移入 [CHANGELOG.md](CHANGELOG.md)。
- 优先级可能调整，不构成承诺或发布保证。
