# Stellamem 可靠性攻坚 · 实施记录（2026-11-01）

工作副本：`E:\Stellamem-Core`（从 `D:\Stellamem` 复制，生产未改动）
索引/证据：`E:\stellamem-index\`、`E:\stellamem-sandbox\`

## 一、改了什么

| 文件 | 改动 | 为什么 |
|---|---|---|
| `compiler/reliability.py` | 新增。归因硬约束、指纹去重、冲突检测、审计日志、快照恢复、外部事务 | 原代码允许模型自报 `source=user`；重复记忆无判定；事务 pre-image 会把脱敏内容写回磁盘 |
| `compiler/quarantine_vault.py` | 新增。隔离内容搬出记忆根 + 审计副本脱敏 + 内容传播 | `status=quarantined` 只是逻辑标记，`read`/`grep` 仍能取到正文 |
| `compiler/indexer.py` | 新增。SQLite + FTS5(trigram) + 二元组倒排，可整表重建 | 需要一个可丢弃、可重建的检索层 |
| `compiler/search.py` | 新增。混合检索、时间线、历史、关联、预算召回 | 中文检索不能靠空格分词；召回必须受字符预算约束 |
| `compiler/api.py` | 新增。`/v1` 本机 API，写操作 token + Origin 校验 | 需要稳定接口给不同客户端用 |
| `compiler/pipeline.py` | 归属判定改为 `enforce_attribution(c, speaker)` | 归因必须以说话人为准，不是模型意见 |
| `compiler/store.py` | 自动入库新增 `source == "user"` 条件 | assistant/quoted/unknown 不得自动成为正式记忆 |
| `tests/test_phase1_reliability.py`<br>`tests/test_phase2_index.py`<br>`tests/test_phase3_api.py` | 新增 45 个用例 | 覆盖本次全部修复 |

## 二、证据（全部脚本产出）

### 阶段一：隔离与归因

- 生产库副本 33 条 quarantined → 密封后：**派生残留 128 → 1 → 0**（L0 原文 55 处按设计保留）
- 密封后 `verify`：`stubs=33 / plaintext_remaining=0 / hash_matched=33`
- 反向验证：18 条探针，密封后命中 0，解封后命中 17（可逆）
- 归因：模型把 assistant 内容谎报为 `source=user`，管线强制改为 `assistant`，`auto_write=False`
- 事务快照：密封操作改用外部事务，`system/transactions/` 不再出现明文

### 阶段二：检索（`benchmark/bench_phase2.py`，30 条随机样本）

| 查询类型 | 旧（子串扫描） | 新（混合检索） |
|---|---|---|
| 精确子串 recall@1 / @5 / MRR | 1.00 / 1.00 / 1.00 | 0.90 / 0.93 / 0.92 |
| 打散词序 recall@1 / @5 / MRR | 0.00 / 0.00 / 0.00 | **0.43 / 0.71 / 0.55** |
| p50 延迟 | 0.57 ms | 5.2 ms |

索引：4002 条记录 + 1298 段日记，构建 0.65 s，库 16 MB。

规模：

| 规模 | 建库 | 检索 p50 / p95 | 单次召回 |
|---|---|---|---|
| 4,002 条（真实） | 0.65 s | 4.8 ms / 8.8 ms | 5 ms |
| 100,000 条（合成） | 2.8 s | 30.5 ms / 82.2 ms | 88 ms |

### 阶段三：长期运行（`benchmark/sim_365.py`）

| 项 | 结果 |
|---|---|
| 365 天写入 | 730 条，4.21 s |
| 重复导入 60 天 | 新增 **0** 条（幂等） |
| 索引重建前后 | 730 = 730，一致 |
| 崩溃恢复 | 补完到 after 态、journal 清空、未完成事务阻塞新写入 |
| 备份/恢复 | 验签通过，恢复后字节一致 |
| 保险库 | 40 条密封 0.2 s，派生残留 0 |

### 测试

`python -m unittest discover -s tests` → **187 passed**（原 142 + 新增 45）

## 三、接口示例

```bash
curl -s http://127.0.0.1:8793/v1/health
# {"ok":true,"version":"v1","records":33,"docs":1298,"model_required":false}

curl -s "http://127.0.0.1:8793/v1/search?q=记忆系统"
# 隔离记录不返回：{"count":0,"items":[]}

curl -s -X POST http://127.0.0.1:8793/v1/recall \
  -H 'Content-Type: application/json' -d '{"prompt":"我的记忆系统做到哪步","budget":200}'
# {"intent":"project","items":[...],"chars":0,"budget":200,"text":""}

curl -s -X POST http://127.0.0.1:8793/v1/approve -d '{"claim_id":"c-1"}'
# 401（无 token）
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Origin: http://evil.com" ...
# 403（跨站）
```

监听：`127.0.0.1:8793`，局域网 `192.168.1.3:8793` 直连被拒（curl 返回 000）。

## 四、回滚

```text
# 代码：副本内直接丢弃即可，生产没动
Copy-Item "D:\Stellamem\compiler\*.py" "E:\Stellamem-Core\compiler\" -Force

# 索引：删库重建
Remove-Item E:\stellamem-index\index.db
python compiler/indexer.py   # 或调用 Index.build

# 隔离：从保险库还原
python -c "import quarantine_vault as v; v.unseal(ROOT, VAULT, 'q1')"
```

## 五、已知风险（没解决，别当成已解决）

1. **生产仍是 0 条 active 记忆**：33 条全隔离，3969 条待审。索引能搜，但搜不出可注入内容。
2. **隔离保险库未上生产**：只在小副本验证过；应用到 `~/.dsh/memories` 需要单独授权。
3. **L0 原文（daily/_raw）不可脱敏**：删了就没出处，只能靠操作系统 ACL，本机尚未建立。
4. **10 万条以上延迟会到 100 ms 级**：百万级需要更强的倒排裁剪或分批召回，未验证。
5. **向量检索未接入**：模型离线可用（FTS 主路径），但语义近义仍弱。
6. **API 尚无速率限制与审计轮转**；`api-token` 明文存于记忆根内，需要移到 `E:\private\`。
