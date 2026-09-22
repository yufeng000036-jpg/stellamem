# 星忆（Stellamem）

### 给 AI 记忆一个 Git。

**Stellamem 是一个 Markdown-first、Local-first 的 AI 长期记忆编译器。**
它让 AI 的记忆可以被查看、解释、版本化和回滚。

本地运行 · 用户拥有数据 · 免费开源

Stellamem — Compilable, auditable, local-first long-term memory for AI agents.

> ⚠️ **Alpha 阶段**：当前为 `v0.1.0-alpha`，安全边界仍在完善，使用前请读 [SECURITY.md](SECURITY.md)。

---

## 它解决什么问题

- **给 AI 装消化系统**：每天的对话日记，自动编译成结构化长期记忆（偏好 / 决策 / 教训 / 项目），自动去重、路由、入库。
- **解决记忆腐烂**：长期运行的 AI 记忆会越积越乱、自相矛盾。本系统已具备**冲突日志、版本链、关系判断组件**（`judge.py` 支持 8 种关系判断）；
  自动 Relation Engine 接入主流程仍在开发中（见 [ROADMAP.md](ROADMAP.md)），目前冲突裁决由人工确认。
- **白盒透明**：一切记忆都是 markdown 文件，人能读、Git 能 diff、每次认知变化都有证据链。
- **不绑定模型**：提取层是标准化的 Claim 抽取任务，换更强的模型，记忆质量更高。4B 是推荐起点，不是天花板。

---

## 核心思路

```text
AI 对话
→ 自动日记（hooks/daily-autolog）
→ 日记 daily/*.md
→ 编译（compiler/extract.py，本地模型）
→ 结构化 Claim（kind / subject / scope / predicate / value / confidence）
→ 路由（compiler/sleep_purify.py）
→ markdown 记忆（core / long-term / semantic）
→ adapter 注入 AI 上下文（adapters/openclaw）
```

写入侧和注入侧是两个独立组件，缺一不可：
`hooks/` 负责「AI 对话 → 日记」，`adapters/` 负责「记忆 → AI 上下文」。
详见 `docs/INTEGRATION.md`。

---

## 目录结构

```text
compiler/      编译与路由脚本
hooks/         写入侧：自动日记 hook
adapters/      注入侧：框架适配器
templates/     空记忆结构模板
demo/          虚构用户 7 天日记 + 一键演示
benchmark/     评测工具
docs/          文档
schema.md      Claim 规范
config.example.json  配置模板
```

---

## 快速开始（一键安装）

```bash
# 1. 环境自检（只读，不改任何文件）
python setup.py check

# 2. 干跑，看清会改什么
python setup.py install --dry-run

# 3. 真安装（幂等，重复跑安全）
python setup.py install
```

装完后按 `setup-out/install-report.md` 的「下一步」操作（合并 openclaw.json 片段、配模型服务、重启）。

> 把整个仓库丢给 AI，让它读 [AI_BOOTSTRAP.md](AI_BOOTSTRAP.md) 也能自动装。

---

## 手工安装（高级用法）

不想用一键安装，也可以手动逐步来：

1. 复制配置：`cp config.example.json config.json`，填你的路径和模型名。
2. 建记忆结构：参考 `templates/` 建 `core / long-term / semantic`。
3. 启动模型服务，运行 `compiler/extract.py` 编译日记。
4. 装写入侧：`hooks/daily-autolog/`。
5. 装注入侧：`adapters/openclaw/`。

完整步骤见 `docs/INTEGRATION.md`。

---

## 性能上限：模型可替换，上限不封顶

星忆的记忆编译质量不绑定任何单一模型。提取层是标准化 Claim 抽取任务，任何支持 JSON 输出的文本模型都能接。

| 配置 | 模型 | 硬件 | 单篇耗时 | 说明 |
|------|------|------|---------|------|
| 当前默认 | Qwen3-4B Q5_K_M | 8GB 显存 | 约 7 秒 | 本地可跑，零成本 |
| 轻量档 | Qwen3-1.7B Q5 | 4GB 显存 | 约 3 秒 | 低配设备，质量略降 |
| 进阶档 | Qwen3-14B / 32B | 16–24GB 显存 | 视硬件 | 质量优先 |
| 云端档 | GPT-4o / Claude / DeepSeek | API | 视网络 | 质量最高（API 鉴权适配开发中，暂需自行加 header） |

上限不固定。模型越强，抽取越准、去重越干净、冲突裁决越可靠。
整个架构、schema、路由逻辑都是模型无关的——换模型不用改记忆结构。

---

## 当前配置与实测数据

- 默认模型：Qwen3-4B Q5_K_M（llama.cpp）
- 推理后端：llama.cpp + CUDA，n_ctx = 8192
- 实测速度：单篇约 7 秒，73 token/s
- 显存占用：8GB 可全量 offload
- 成本：零（本地）

---

## 硬件门槛

- 推荐：8GB 显存 + llama.cpp，跑 Qwen3-4B Q5_K_M。
- 进阶：16GB 显存，可上更大模型。
- 无 GPU：CPU 也能跑，速度慢 10 倍以上；或改用本地 OpenAI 兼容服务（Ollama / LM Studio）。

---

## 量化效果（Benchmark）

30 篇真实日记评测：召回率 0.705，False Write 0。

> 注：该数据来自早期内部评测，匹配方法仍在迭代，后续会发布可复现的 benchmark 结果，欢迎自行验证。

---

## 适用 / 不适用

适合：本地优先、要审计、要版本控制、长期运行的 AI 助手记忆治理。

不适合：追求开箱即用 SaaS、不想碰命令行、只需短期上下文。

---

## FAQ

**能接 Claude / GPT 吗？**
目前**直接支持本地 OpenAI-compatible 模型服务**（llama.cpp / Ollama / LM Studio 等），
默认不需要把记忆发送给第三方模型服务。
远程 API Provider（GPT / Claude / DeepSeek 等）**适配计划中**：当前 `extract.py` 未带
`Authorization` 头，直连远程 API 会 401。想要远程跑，需要自行加鉴权头，或等官方适配。
实际质量差异需自行 Benchmark 验证，本仓不声称「某个模型一定最好」。

**记忆存在哪？会上传吗？**
默认全本地 markdown 文件，Git 可 diff，不联网。只有你主动配置远程模型 API 时，
编译所需内容才会发给对应服务商（该能力见上一条，尚未内置）。

**换更强的模型会更好吗？**
会。模型越强，召回越高、误写越少。4B 是默认起点，不是上限。

**和 Mem0 / Zep 有什么区别？**
markdown-first、可 diff、可回滚、白盒可审计，不是黑盒数据库。

**支持哪些 Agent 框架？**
第一公民宿主是 **OpenClaw**（有 hooks / plugins / agent workspace 的环境），开箱即用。
其他框架（Claude Code / Cursor / 自研 Agent）**核心编译器可以直接用**，只需自己实现两个薄适配层：

| 组件 | 非 OpenClaw 用户需要做什么 |
|------|--------------------------|
| 编译侧 `compiler/` | **完全不用改**，框架无关。配好 `config.json` 即可跑 `extract.py` → `sleep_purify.py` → markdown 记忆。 |
| 写入侧 | 自己写「对话结束 → 落盘 daily」的钩子，格式 `[HH:MM] role: content` → `daily/YYYY-MM-DD.md` |
| 注入侧 | 在 system prompt 构建处读 `core/*.md` 拼进去，按关键词命中读 `long-term/` 和 `semantic/` |

即：**不装 OpenClaw，也能用这份记忆编译器**（写入/注入自实现）。完整说明见 [INSTALL.md 第 6 节](INSTALL.md)。

---

## 还在持续更新中

星忆还在持续更新中，技能还会逐渐增加，还会逐渐变得更完善、更加完美。请持续支持我们。

如果觉得有用，欢迎 Star、提 Issue、贡献 adapter 或模型配置。

---

## License

MIT，见 LICENSE。

---

Made by 宇枫

---

## 评测口径更正（2026-09-22）

现有 30 篇单用户日记评测得到：标准答案 393 条、命中 277 条、漏检 116 条、多检 272 条；在当前标准答案和单次 LLM 匹配器口径下，召回率为 0.705，精确率为 0.505。

`False Write = 0` 只表示既定对抗样本中未观察到对应错误，不能推出“所有写入都正确”，也不能代表真实运行环境的错误写入率为零。该数据只适合版本内部比较，不能外推为通用准确率。

Scope 说明：Schema 与本地编译器已改为四字段稳定 ID，并兼容读取 legacy 三字段 ID；9 项安全测试已通过，定时任务恢复后的真实编译仍未验证。
