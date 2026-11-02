# Quick Start

目标：从零到能跑通 Stellamem 的核心流程，约 5 分钟。

---

## 1. 要求

- **Python 3.10+**（核心只用标准库，无第三方运行时依赖）
- **本地模型服务**（OpenAI 兼容接口：llama.cpp / Ollama / LM Studio 等）
  - ⚠️ **只接受环回地址**（`127.0.0.1` / `localhost`）；远程与云端 API 会被拒绝
- **Node 20+**（可选，仅在启用 OpenClaw 集成时需要）
- 一个记忆根目录（例如 `~/stellamem-memory`）

---

## 2. 环境自检（只读）

```bash
python setup.py check --offline
```

这一步**不改任何文件**，只报告 Python / OS / Node 等环境情况。
此时提示 `Config: FAIL` 是正常的 —— 因为还没安装。

---

## 3. 干跑（看清会创建什么）

```bash
python setup.py install --mem-root <记忆根目录> --dry-run
```

列出计划创建的目录与文件，**不写盘**。确认无误后再继续。

> 💡 安装器**不会**读写或合并宿主（如 OpenClaw）的配置；它只输出一份
> `setup-out/openclaw.snippet.json` 供你自行审阅。

---

## 4. 真安装

```bash
python setup.py install \
  --mem-root <记忆根目录> \
  --config  <config.json 的路径> \
  --model-url http://127.0.0.1:8081
```

可选参数：

| 参数 | 作用 |
|---|---|
| `--oc-root <OpenClaw 根目录>` | 同时安装 hook 与 adapter 副本 |
| `--sessions-dir <会话目录>` | 指定宿主会话目录（默认 `<oc-root>/agents/main/sessions`） |
| `--model-id <模型名>` | 指定模型 |
| `--tz-offset <小时>` | 时区偏移（如中国 `8`） |
| `--upgrade` | 仅当目标文件与安装清单一致时才备份并更新（保护你的自定义） |

安装器是**幂等**的：重复运行安全，**不覆盖**已存在的自定义文件。

---

## 5. 跑离线演示（虚构数据，不需要模型）

```bash
python demo/run_demo.py --offline
```

用合成数据跑通真实管线，验证安装是否正确。**这不是准确率测试。**

---

## 6. 自检

```bash
python compiler/memory.py doctor
```

---

## 7. 编译真实来源

```bash
# 从来源清单编译出 Claim 并入库 / 进收件箱
python compiler/sleep_purify.py --mode review
```

> `compiler/extract.py` 是一个**单独的调试 CLI**，把单篇来源的 Claim 打到
> stdout，**不参与主流程**。主流程统一走 `sleep_purify.py`。

---

## 8. 人工复核收件箱

敏感 / 身份 / 低置信度候选会进入收件箱等待人工确认：

```bash
python compiler/inbox.py list              # 查看待审候选
python compiler/inbox.py show    <id>      # 查看单条完整记录
python compiler/inbox.py approve <id>      # 批准（写入 canonical memory，带 provenance）
python compiler/inbox.py reject  <id>      # 拒绝（不进入 canonical memory）
python compiler/inbox.py edit    <id> --value "..."/--predicate "..."   # 修改后再定
```

> ⚠️ `core/identity.md` 与 `core/user-core.md` 是**硬保护**文件，无法通过
> `approve` 写入；对这类候选会**明确报错**，需手动编辑。

---

## 9. 接入宿主（以 OpenClaw 为例）

OpenClaw 是**第一个完整验证的宿主**。安装时加 `--oc-root`：

```bash
python setup.py install --mem-root <记忆根目录> --oc-root <OpenClaw 根目录>
```

然后**审阅** `setup-out/openclaw.snippet.json`，确认后再手动应用到宿主配置。
安装器不会自动修改宿主的 `openclaw.json`。

其它框架（Claude Code / Cursor / 自研 Agent）见
[`docs/INTEGRATION.md`](INTEGRATION.md)。

---

## 下一步

- 架构与设计：[`docs/ARCHITECTURE.md`](ARCHITECTURE.md)
- 应用场景：[`docs/USE_CASES.md`](USE_CASES.md)
- 安全边界：[`SECURITY.md`](../SECURITY.md)
- 参与贡献：[`CONTRIBUTING.md`](../CONTRIBUTING.md)
