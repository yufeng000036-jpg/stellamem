# v4 单条支持检查 · Windows 本机验收

完整题集只留在验收员电脑上。正式运行使用 20/500/2000 三档嵌套库，各有相同的 40 道题。A 使用试用版字面检索，B0 用 #150 的字面加语义融合；两个选择器共享 #152 之前的旧提示和 #153 已送达编号解析规则。B1 对 B0 已经选中的一条原文做一次核验，复用 B0 的原始输出，无第二次选证。B0 没有选中时标为 SKIP，核验调用为 0。试用版实际的裸编号解析另从 A 同一次原始输出计算，附完整产品门对照。

| 冻结项 | 版本 |
|---|---|
| 选证提示 UTF-8 SHA-256 | 014cb6757cc675d2b4ee48945e9f2d4126400a96c37c83d16a305e629af1282a |
| 解析契约 | #153，ca7ccac5992a5e6bdd5c14a90bd32a481198843e 的 classify |
| 核验提示 UTF-8 SHA-256 | d6f74392bc778f899d91efa32e4e3bdac1182c2e1971c604a4dc7ef5fb56cba3 |

## 公开预检

确认显存可用，与本机生图错开。在本机启动先前使用的 BGE-M3、Qwen3-4B，核对实际加载权重和 /v1/models 的 ID。从解压后含 benchmark 的目录打开 PowerShell。替换实际 ID 和阅读器端口。服务只访问回环地址，嵌入端口固定为 8082。

```powershell
$py = 'E:\Python\python.exe'
$dir = 'benchmark\jarvis_semantic'
$run = "$dir\support_v4.py"
$models = @('--gguf','E:\kvmem\embed\bge-m3-Q5_K_M.gguf','--embed-model','实际BGE模型ID','--reader-model','实际Qwen3-4B模型ID','--reader-port','实际阅读器端口')
& $py $run @models --public-smoke --manifest "$dir\reader_public.json" --out "$env:TEMP\jarvis-support-v4-public.jsonl"
```

使用先前公开的 6 题作流程检查。末行 complete 应为 18 组三臂、54 行，passed 为 null。逐档核对 B1 的 checker_calls 总数等于 B0 的选中条数，B1 的每次上游选择编号和 raw_output 与同题 B0 一致。公开预检的分数不参与正式判定。

## 建清单和离线预览

沿用 #151 的 notes/questions 与实体、属性替换标注。每个交叉负例增加 `"cross_negative": true` 和 `"cross_sources": ["实体父原文ID","属性父原文ID"]`。实体父是该题点名对象的记录，属性父是另一实体上提及所问属性的记录。按每一道题的方向标注，不能按 ID 排序。普通题省略这两项。请验收员对照封存件人工核对标签语义、两个更正对和金标唯一性。工具可检查 8 个不同的已知原文对，但无法替人判断两个父角色是否填反。

```powershell
$private = 'E:\jarvis-sem\private'
$map = "$private\v4-map.json"
& $py $run --validate-map $map
& $py "$dir\scale_corpus.py" $map "$private\v4-inventory.jsonl"
```

上面两条命令都不调用模型。保留原件和映射完整摘要，核对三档语料预览。正式结果 corpus 行的哈希须与预览一致。若新增记录改变金标唯一性，记为数据阻塞。

## 一次性真机运行

```powershell
& $py $run @models --manifest $map --sealed-original "$private\holdout-v4-sealed.txt" --sealed-hash '封存时保存的完整64位小写SHA256' --out "$private\v4-result.jsonl"
```

运行前核对完整原件哈希，同时约束前后缀 249c06ed…7bc8。输出新建，原件、映射、输出均在解压目录之外。正式完整运行应有 120 组三臂、360 条 question 行。中断或超时没有完整成绩。任何档出现跨主体、已撤销原文泄露或失效引用立即判失败并停止。结束后由验收员停掉实验服务。

## 计分与回报

每题每臂的 parents 分别记录实体父、属性父的实际编号及其是否在 reader_candidates 中。B0 记录 bare_delivered、bracket_delivered、model_none、program_no_material、未送达编号等原始类型。B1 另存上游选择和角色、核验输入摘要、原始输出、YES/NO/INVALID/SKIP 及最终选择。核验器的用户消息仅有问题和这条原文文本。YES 原样保留选择，NO 和 INVALID 都阻止放行。核验服务超时或中断记为运行故障，不能冒充模型明确否决。

- **检查器贡献**逐档用真实 B0 交叉误选为分母。小于 4 次时，该档记 insufficient_evidence。达到 4 次时显式 NO 必须拦下至少一半，且原本正确的正例损失最多 1 道。INVALID 阻止的交叉误答单列，不计显式拦截；INVALID 造成的正例损失计入误拦。实体父、属性父和其他来源分别列分母、NO、INVALID 和 YES 的题号。三档中任一档分母不足，整轮贡献为“尚不确定”，各档的失败仍单独保留。
- **产品门**独立计算，T1 参照；T2、T3 分别要求正例净增至少 3，召回@5 不低于 A，实体净差 ≥−1 且丢题 ≤2，负例误答 ≤A 且 ≤2。product 为同一 #153 解析的 A，product_strict_A 为相对原试用版严格解析的对照。两份报告均保留。
- 每档另报核验调用数、实测中位数和 P95。P95 对实际调用延迟排序后取第 ceil(0.95 × 调用数) 个样本；无调用时为 null。B1.reader_calls=0 表示没有重复运行选择器。B1 的端到端时间包含共享的 B0 链路加本次核验。
- 末行 contribution、product_passed 分别作结。整体 passed 仅在两者都通过时为真；贡献不确定也不自动采用。完整未达标的退出码为 1，须结合末行区别于运行中断。回传汇总与匿名题号即可，逐题数据保留在本机。
