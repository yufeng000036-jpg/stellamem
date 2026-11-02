# CHANGELOG — 改进轮（工作副本，非生产）

## 基线
- 来源：2026-11-02 收到的 Stellamem 代码包副本（`original/`，未动）。
- 基线测试：Ran 187 tests，OK（Python 标准库 only，无新增依赖）。

## 改动
### P0 修复
- `compiler/search.py`：`as_of()` 默认不截断（`limit=None`）；新增
  `as_of_paged()`，返回 total/returned/truncated。旧默认 limit=100
  在 412 条 active 时静默截断，是 2026-11-02 上线第 10 步的实测缺陷。
- `compiler/gate.py`：低分非 weak 条目同样拦截（overlap=0 且
  semantic < 0.45，或 score < 0.10）；`allow_weak` 仍可注入并打
  低置信度标记；`gate_log.jsonl` 超 1 MB 或 10,000 行时轮转，最多 3 个。
- `compiler/reliability.py`：`snapshot()` 先收集路径、查重名，
  重名抛错且不留部分 zip；另增 `_check_duplicate_names()`。

### P1 融合
- `compiler/subjects.py`（新增）：user / persona / viewer 命名空间，
  同主体默认检索，跨主体默认拒绝，桥接副本是独立的目标主体记录。
- `compiler/bridge.py`（新增）：默认拒绝 + 三模板 + 全文批准 +
  72h 过期（低置信度猜测）+ 整批失败 + 单条撤回 + 全程审计；
  关键词黑名单仅为噪声过滤器。
- `compiler/persona_adapter.py`（新增，内存态）：record_event
  （pending_confirmation）/ retrieve_pack（persona、预算上限、卡片在前）
  / confirm_spoken（显式确认才升级）；拒绝 user 主体写入。
- `compiler/promises.py`（新增）：SQLite sidecar 承诺表，create /
  fulfil（需证据）/ expire / list_open；过期后 fulfil 被拒绝且可审计。

### P2 只读
- `compiler/pending_report.py`（新增）：待审只读筛查报告，
  不改状态、不激活、不删除。

### 测试
- 新增 `tests/test_improved_p0.py`（13）、`test_improved_p1.py`（24）、
  `test_improved_p2.py`（2），合计 39 个。
- 改动后：Ran 226 tests，OK（187 原有 + 39 新增，0 失败）。
- 没有削弱原有测试；`as_of` 签名改动无需更新原有测试（原有测试
  中无 `as_of` 调用，已 grep 核实），因此不存在为通过而改测试的情况。

## 未改动
- `original/` 全树、生产记忆、生产索引、生产代码路径均未动。
- 没有生产部署脚本，没有宣称生产已更新。
