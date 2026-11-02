# Changelog

本文件记录「星忆 / Stellamem」的所有主要更新，按时间倒序。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 语义化版本规范。

---

## Unreleased - large diary reliability

- 默认编译分块调整为 2500 字符，llama.cpp 上下文窗口调整为 16384。
- 将 `finish_reason=length` 与连接、超时错误分开；输出截断时递归重切。
- 完整响应若 JSON 无效，原块只重试一次；连续无效再按无损边界重切，到下限仍无效则硬失败，不修补残缺 JSON。
- 删除固定 `text[:6000]` 输入截断和残缺 JSON 补齐路径。
- 同一来源全部 chunk 成功后才事务提交并推进内容游标；失败留下状态供重试。
- 所有重试仍执行 Secret Gate；逐字证据保留消息级 provenance，未核实证据保留 chunk 级 offset 并进入待审核。

---

## [0.1.0-alpha] - 2026-09-20

### 新增

- 自动日记 hook（daily-autolog）：`/new` 写今天 + 网关启动补昨天 + 幂等进度标记
- 记忆注入 Adapter（OpenClaw）：`before_prompt_build` 注入 core 记忆 + 关键词下钻
- benchmark 评测：30 篇真实日记，召回 0.705 / 精确 0.505 / False Write 0
- memory CLI：只读体检（索引同步 / 冲突检测 / 健康检查 / 版本链校验）
- **AI 自举安装**：`AI_BOOTSTRAP.md` + `INSTALL.md` + `setup.py` 三件套，AI 读完可自行安装
- **可分发安装器**：`setup.py` 支持 `--mem-root` / `--config` / `--oc-root` / `--model-url` / `--dry-run` / `--upgrade` 参数；**不会**修改 `openclaw.json`，只生成配置片段。
- **发布前安全文档**：新增 `SECURITY.md`（安全策略）、`THREAT_MODEL.md`（12 项威胁模型）、`ROADMAP.md`（规划中能力）
- **README 重写为发布版**：Alpha 声明、安全模型（Secret is not Memory / Single Writer / Adapter 边界）、Your Memory Is Yours、Git for AI Memory、退出机制、数据边界

### 改进

- 星忆支持 identity / user-core 类型
- 安全防护：注入 / 反话 / 临时情绪 / 过期检测
- 配置化：路径 / 模型 / 端口抽到 config.json
- 权威数据源收口：旧 MEMORY.md / USER.md 等归档，事实以 core / long-term / semantic 为准
- 安装器不污染源文件：只改拷贝到目标位置的副本，源文件保持模板状态可重复分发
- `openclaw.json` 自动合并：深度合并（只追加 keys、列表去重），自动备份

### 修复

- 修复 session 归档文件读取（`.jsonl.reset.*` 被漏）
- 修复幂等（改用隐藏进度标记，不再依赖模型输出时间戳）
- 修复旧文件污染（MEMORY.md / USER.md 归档为 .bak）
- 修复 sessions 目录探测（优先默认 agent main）
- 修复 memorySearch 位置（对齐真实 openclaw.json 的 agents.defaults 结构）
- 修复安装器更新 hook/adapter 时悄悄删除旧目录的问题（改为先备份旧目录再替换）

---

## 说明

本项目的版本节奏以「阶段 / 任务」推进，而非严格的语义化版本号。
上表按日期归档，后续每个开发日追加一节即可。

---

## 2026-09-22 审计更正

- 评测口径：`False Write 0` 只描述既定对抗样本，不能解释为所有写入正确或真实环境零错误；同次评测还包含 272 条 extra，并受单用户样本与单次 LLM 匹配器限制。
- Schema：稳定 ID 与去重键增加 scope；本地编译器已加入四字段实现和 legacy 读取，78 项 Python 测试与 3 组 JS 测试在合成数据上通过，真实模型仅完成工程性验证（非准确率声明）；安装后的 OpenClaw hook/adapter 尚未在真实宿主端到端验证。
- 模板：补齐编译器和适配器已引用但此前未随安装模板创建的四个 semantic 文件。
