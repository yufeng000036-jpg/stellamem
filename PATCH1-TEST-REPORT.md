# PATCH1 报告（2026-11-02，仅 staging）

> 基线：`stellamem-improved-20261102.zip`（改进轮）
> 依据：`STAGING-REPORT-20261102.md` + 独立审计 `audit-claims.md`
> 范围：只修 D2 / D6 + 包卫生；**未部署生产**

## 1. 为什么有这个补丁

改进轮的 staging 测试（226/226 单测、187 基线、70/70 手工核对）全部通过，但独立
代码审计把 11 条声明判为 9 成立、2 部分成立：

1. **D2（中）**：gate 的低信号规则要求 `overlap` 与 `semantic` **同时**存在；
   只带其一的“部分字段”条目绕得过，等于 P0 想补的“低分缺口”仍留了一半。
2. **D6（中）**：“批量批准整批全有或全无”在重复 req_id、或两阶段之间时钟推进时
   会部分落地后抛错——声明强于代码。

另有一项包卫生问题：`SHA256SUMS.txt` 是改进前生成的（3 条不符、37 条幽灵条目、
12 个文件未覆盖），且包内 `config.json` 指向生产机真实路径。

## 2. 改了什么

| 文件 | 改动 |
|---|---|
| `compiler/gate.py` | `_num()` 接受数字字符串、拒 bool/NaN/inf；`is_low_signal()` 改为“只要 overlap/semantic 之一存在就判定，缺的按 0”；三个字段都没有仍不判定。 |
| `compiler/bridge.py` | `approve_batch()` 第一阶段先拒绝重复 id，再逐条校验；第二阶段只做状态落地（不 refresh、只读一次时钟），不存在半批抛错。 |
| `config.json` | 改为安全默认（`./stellamem-data`、`127.0.0.1:8081`），不再指向生产路径。 |
| `config.production-local.json` | 原生产机配置（加 `_note`；不会被 `config_loader` 自动加载）。 |
| `tests/test_patch1.py` | 新增 16 项测试（gate 9 项 / bridge 7 项）。 |
| `SHA256SUMS.txt` | 重新生成，覆盖实际交付文件（规则见 `MANIFEST-NOTES.md`）。 |
| `README-交接.md`、`STAGING-TEST.md` | 同步配置说明、242 测试、PATCH1 核对项。 |
| `CHANGELOG-PATCH1.md`、`MANIFEST-NOTES.md`、本文件 | 新增。 |

**流程与红线**：改动只在 staging 副本；原有 187 + 39 项测试文件一行未改；没有生产
部署脚本；没有读写生产记忆/索引。

## 3. 验证结果

| 项目 | 结果 |
|---|---|
| 全量单测（Python 3.13.12） | **Ran 242 tests，OK**（187 + 39 + 16） |
| 原有测试是否被削弱 | **否**：`tests/` 里除新增 `test_patch1.py` 外，其余文件与改进轮逐字节相同 |
| 手工核对（扩展版 76 项） | **76/76 通过**，含新增 D2/D6 项 |
| 打包后自校验 | 解压副本上 `sha256sum -c SHA256SUMS.txt` 全 OK；在解压副本上重跑 242 测试 OK |
| 生产面 | 记忆 529 文件 / 最新 mtime、index.db SHA256、8082 PID 前后一致 |

### 3.1 行为变化（有意）

- gate：以前只传 `overlap=0`（无 semantic）或只传 `semantic=0.2`（无 overlap）的条目
  会注入，现在**被拦**。这是 fail-closed 收紧。主路径 `search()` 同时设置两字段，
  不受影响；只有 text、无任何信号字段的条目仍不判定（边界保留）。
- bridge：批量里出现重复 req_id 现在直接抛错（以前会批准一次后再抛错）；
  成功批次所有条目 `approved_at` 相同（批量只读一次时钟）。

## 4. 没修的东西（明确，避免误读）

- D1 `as_of_paged(limit=None, offset)` 忽略 offset；D3 轮转阈值措辞；D4
  `blocked_weak` 统计口径；D5 `allow_weak` 对缺 text 条目抛 KeyError；
  D7–D13 边界。均在审计报告里，留给下一轮。
- 未改 `as_of()` / `as_of_paged()` 行为、未改 `search.py`、`subjects.py`、
  `persona_adapter.py`、`promises.py`、`pending_report.py`。
- 旧记录主体标签方案、persona_adapter 持久化方案仍**未决**（见 staging 报告 §4）。
- **本补丁不是生产批准**：生产部署仍需单独批准（全量备份 + SHA 基线、staging↔生产
  diff、迁移检查点、真实替代验证）。

## 5. 复现

```bash
# 全量测试（包内安全默认配置即可，不碰生产路径）
cd <解压目录>
python -m unittest discover -s tests          # → Ran 242 tests, OK

# 手工核对（76 项）
PYTHONIOENCODING=utf-8 python manual_checks.py  # 脚本在补丁交付目录旁的 checks 目录

# 清单校验
sha256sum -c SHA256SUMS.txt
```

## 6. 与改进轮的关系

`docs/IMPROVED.md`、`CHANGELOG-IMPROVED.md` 保持原样（历史记录不追改）；
本文件与 `CHANGELOG-PATCH1.md` 是之后的一层补丁记录。
