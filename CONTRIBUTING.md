# Contributing to Stellamem

感谢你有兴趣参与 Stellamem。本项目处于 **Alpha**，接口和 schema 仍可能调整。

---

## 环境准备

- **Python 3.10+**（核心只依赖标准库，无第三方运行时依赖）
- **Node 20+**（可选，仅在开发 OpenClaw hook / adapter 时需要）
- 开发工具（可选）：`ruff`（lint / format）

```bash
git clone https://github.com/yufeng000036-jpg/stellamem.git
cd stellamem
python -m unittest discover -s tests      # Python 测试
node --test tests/hooks.test.mjs          # JS 集成测试（需 Node）
```

---

## 跑测试

```bash
# Python 单元 / 集成测试
python -m unittest discover -s tests

# JS hook / adapter 集成测试
node --test tests/hooks.test.mjs

# 离线演示（虚构数据，不需要模型）
python demo/run_demo.py --offline

# 密钥扫描
python tools/secret_scan.py
python tools/secret_scan.py --history

# Lint（可选）
ruff check .
ruff format --check .
```

---

## 代码规范

- **缩进 / 行宽**：见 `pyproject.toml`（行宽 100）
- **禁止**在核心引入第三方运行时依赖（保持 `stdlib only`）
- **禁止**硬编码本机绝对路径、用户名、密钥
- 中文注释可以，但代码标识符用英文
- 日志 / 错误信息**不得**输出密钥、完整对话或凭据

---

## ⚠️ 记忆相关修改必须带回归测试

任何触及以下区域的改动，**必须**附带回归测试：

- Claim 抽取 / 规范化（`compiler/claim_normalizer.py`）
- 安全策略（`compiler/security.py`）
- 关系引擎（`compiler/judge.py`）
- 存储 / 事务 / 恢复（`compiler/storage.py`、`compiler/store.py`）
- 来源读取（`compiler/source_reader.py`）
- 注入投影（`adapters/openclaw/index.js`）
- 密钥门禁（`shared/policy-rules.json`）

重点验证的**不变量**：

1. 密钥在模型调用**之前**被阻断
2. 敏感 / 身份内容**永不自动写入**
3. 冲突**不静默覆盖**
4. 来源无法验证时**不伪造**
5. `core/identity.md` 与 `core/user-core.md` 不被自动写入
6. 事务失败后 canonical memory 不损坏、可恢复

---

## 提交 Pull Request

1. Fork 并新建分支（如 `fix/xxx`、`feat/xxx`）
2. 保持提交粒度清晰；commit message 说明**为什么**改
3. 跑通上面的测试
4. PR 描述里写明：改了什么、为什么、如何验证、有哪些风险
5. 涉及行为变化时，同步更新文档

> 请勿在 PR 中包含真实用户的记忆、对话、密钥或本机路径。

---

## 报告 Bug

请使用对应的 Issue 模板。报告时提供：

- 复现步骤
- 期望行为 vs 实际行为
- 环境（OS / Python 版本）
- **脱敏后**的日志片段（**不要粘贴密钥或真实记忆**）

---

## 报告安全问题

**请勿通过公开 Issue 提交安全漏洞。**

请按 [`SECURITY.md`](SECURITY.md) 中的渠道私下联系。我们会优先处理并及时回应。

---

## 行为准则

- 平实、直接、就事论事
- 尊重不同背景的贡献者
- 不要为了宣传而夸大能力

---

MIT © 宇枫
