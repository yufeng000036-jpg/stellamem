# 保密语义召回 A/B · 冻结协议

本目录仅供隔离研究，基于 dbd1a9e。不安装插件或模型，不启动服务，不接生产库或 vectors.db。输入只使用评测员提供的合成记忆，索引为临时进程内的 1024 维向量列表，程序退出即释放。没有正式试用版更新。

## 冻结项

- A 使用原 Jarvis 投影逻辑，仅增加预算裁剪前名次记录。B 在实验副本中沿用资格、主体、冲突、来源、世代与裁剪规则，把余弦排名前 8 的修订加入候选。固定等权 reciprocal rank fusion，常数 60，按已有字面通道名次与向量名次求和；不按余弦绝对值决定可答性。始终保留原 always 优先级。两个通道只通过本轮快照中的修订 ID 相交。
- 两臂 user/global、投影预算 3000 字符、最多五条送原版 Jarvis 阅读器。Qwen3-4B、temperature=0、seed=7、最多 384 输出 token。交替 AB/BA 顺序，每题清空模型会话，无历史串题。原有 34/40 来自旧测量；本轮同机 A 的实测值是 B 的对照。
- 预先确定 22 正例与 10 负例。成功须新题正例最终答对净增加，负例正确拒答数不下降、无失效引用、无被撤销原文泄露。深度改述和实体混淆分开看。报告所有退步题；不删除超时或协议错误。中途故障的 JSONL 是不完整运行，不具有验收分数。
- 只支持每题一个足以独立回答的当前原文 ID，负例为 null。当前阅读器回显已核验原文，程序据所选 ID 评分。验收员另核查语义相关性。无效模型 ID 输出可被阅读器安全拒答，invalid_selection 单独留痕；引用有效并不证明答案相关。
- 用户本机报告的 BGE-M3 文件大小 467662912 字节、完整 SHA-256 由脚本核验。以报告为据，非考题上的深度改述相似度 0.70/0.72，同题不同事实约 0.60。服务进程实际加载的权重由本机验收员核对；模型列表只能确认公开的 ID。短批测速不能替代整套链路的延迟测量。
- 全程只访问 127.0.0.1。嵌入服务使用 8082；阅读服务端口另传。服务生命周期及 GPU 资源由验收员管理。运行器不改系统 PATH、不读取原个人记忆，也不启动或停掉现有服务。

## 真机执行

在已确认两个服务均为本机推理且实际加载指定权重后，用两个服务各自 /v1/models 列出的实际 ID 替换下面示例。先用公开 public.json 走完双服务、双臂，再在同一冻结提交下打开密封题。示例 PowerShell 从解压目录或仓库根目录执行。

```powershell
$py = 'E:\Python\python.exe'
$run = 'benchmark\jarvis_semantic\run.py'
$base = @('--gguf','E:\kvmem\embed\bge-m3-Q5_K_M.gguf','--embed-model','实际BGE模型ID','--reader-model','实际Qwen3-4B模型ID','--reader-port','实际阅读端口')
& $py $run @base --public-smoke --manifest benchmark\jarvis_semantic\public.json --out "$env:TEMP\jarvis-public-ab.jsonl"
```

正式验收时，原始封存文件留在验收员电脑上。若原数据格式不同，逐字映射成 public.json 同结构的私有 JSON，不把题目、原文或逐题答案发给研发端。notes 含 id/text/status（active 或 superseded）；questions 含 id/text/category/gold，gold 为对应 note ID 或 null。按封存顺序录入；两条旧记忆走真实人工批准接口后显式遗忘。运行前由验收员对照原件核对映射，工具本身不证明格式转换逐字正确。CLI 校验原件完整哈希，并单独记录转换件哈希；给研发端报告两者摘要和冻结提交即可。

```powershell
& $py $run @base --sealed-original '私有原件路径' --sealed-hash '封存时保存的完整64位小写摘要' --manifest '私有转换件路径' --out '新的私有结果文件.jsonl'
```

报告含建索引时间、逐题查询编码和投影时间（包含研究驱动的进程与文件开销）、正确原文裁剪前排名、裁剪原因、实际前五、模型原始选择、最终原文、引用有效性、逐臂统计、得失题 ID，以及 complete 标记。完整报告留在本机。盲测期间保持实现、端口对应模型、语料、参数不变。已揭晓的 94 题仅作回归，不作为新考卷。

公开回归可把任意一批旧题和对应已批准笔记按相同格式送入 --public-smoke，不限制 32 题。协议替身与撤销负对照运行命令为 python -m unittest discover -s tests -p test_jarvis_semantic_study.py。本目录不包含封存题，不改 #148/#149。
