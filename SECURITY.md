# Security Policy（安全策略）

Stellamem 是一个 Markdown-first、Local-first 的 AI 长期记忆编译器。安全对我们来说不是「事后补丁」，而是产品的一部分。本文说明：如何报告漏洞、秘密如何处理、数据边界在哪、以及当前 Alpha 阶段已知的限制。

---

## 报告安全漏洞

如果你发现了安全漏洞，请**不要**在公开 Issue 里贴细节或真实密钥。

请通过私密渠道联系我们（安全邮箱：**2024981488@qq.com**），并提供：

1. 受影响版本（当前 `v0.1.0-alpha`）；
2. 复现步骤或 PoC；
3. 影响范围评估；
4. 如果你愿意，修复建议。

我们会在 **3 个工作日**内确认收到，并在修复后公开披露。感谢你以负责任的方式报告。

---

## 不要在任何公开渠道粘贴真实密钥

无论是 Issue、讨论区、还是 PR 里的示例代码，**永远不要**粘贴真实的 API Key、Token、Cookie、密码或私钥。示例请一律使用占位符：

```yaml
# 错误
openai_api_key: 在这里粘贴真实密钥会泄露

# 正确
openai_api_key: <YOUR_OPENAI_API_KEY>
# 或
openai_api_key: env://OPENAI_API_KEY
```

如果你已经不小心贴过，请立即轮换（revoke）该密钥，而不是只删除评论——一旦公开过，就视为已泄露。

---

## Secret is not Memory

**API Key、密码、Token、Cookie、Private Key、App Secret、Refresh Token 等秘密，不应该成为长期记忆。**

- 系统不应直接把它们写入长期记忆；
- 若检测到疑似秘密，应提示用户检查；
- 必要时只保存「凭据存在」的**引用**，而非凭据内容：

```yaml
service: feishu
secret_ref: env://FEISHU_APP_SECRET
```

> ⚠️ **当前状态**：自动密钥识别与脱敏规则**尚未实现**（在 [ROADMAP.md](ROADMAP.md)）。现阶段这是一条**使用约定**，不是一条代码强制执行的拦截。请用户自查：不要把密钥写进会被编译成记忆的对话里。

---

## 数据边界

- **默认可完全本地运行**：使用 llama.cpp / Ollama / LM Studio 等本地模型时，记忆编译过程不离开本机。
- **远程模型 API 当前不可用（硬性拒绝）**：`compiler/model_client.py` 只接受环回地址
  （`127.0.0.1` / `localhost`），会拒绝任何远程/云端 endpoint，并拒绝重定向、忽略系统代理。
  因此即使误填远程地址，内容也不会被发送出去。若要支持托管服务商，必须显式修改代码与策略，
  届时与模型交互的内容（可能含日记或记忆片段）才会发往对应服务商。
- Stellamem **不会**在用户不知情的情况下自动把记忆发送到远程服务。

---

## Adapter 权限说明

Adapter 是高权限组件（它能影响 AI 最终看到的上下文）。当前 Stellamem Adapter 的约定：

- 默认只读，不修改 canonical memory；
- 不自动读取与任务无关的文件；
- 不静默联网；
- 不自动上传完整 memory vault；
- **fail-soft：注入失败时返回空、Agent 继续运行，不因记忆层故障而阻断对话**；
- 有单文件注入字符上限与敏感档案摘要策略（可用 plugin config 调整）；
- 有超时、有日志、可禁用。

> 更严格的 Context Projection（读取隔离）尚未完全实现，正在收紧中。

---

## Single Writer, Multiple Readers

Stellamem 遵循 **Single Writer, Multiple Readers**：Memory Compiler 是 canonical memory 的**唯一写入者**。

如果你同时启用了 OpenClaw 自带的 `memory-core` / `dreaming`，可能产生**双写者**问题。安装时若检测到相关插件，请谨慎评估（详见 [THREAT_MODEL.md](THREAT_MODEL.md)）。

---

## 当前已知限制（Alpha 阶段）

以下是 `v0.1.0-alpha` 已知、尚未解决的安全边界，请在使用时知悉：

| 限制 | 说明 |
|---|---|
| 密钥自动识别 | 未实现，靠用户自查 |
| 敏感数据分级 | `public/private/sensitive/secret` 尚未落地 |
| Context Projection | 读取隔离尚未完全实现 |
| 远程 API 内容审计 | 发送给服务商的内容未做自动脱敏 |
| 第三方 Adapter | 尚无签名/校验机制 |
| 供应链 | 无自动 SBOM / 签名发布 |

这些都会随版本演进，见 [ROADMAP.md](ROADMAP.md)。

---

## Alpha 阶段免责声明

Stellamem 当前是 **Alpha Software**，不提供任何形式的「绝对安全」保证。请在重要数据上使用前：

1. 保持 Git 版本控制；
2. 保持本地备份；
3. 不要依赖它来处理无人监管的高敏感信息。
