# 4K 桌面页（2026-11-06）

每主体一张、4096 字节硬上限的桌面页：人和约一半、事一半；改写留 diff、
删行要批、写操作原子化、旧版留 14 份备份。

模块：`compiler/desktop_page.py`，CLI：

```bash
python compiler/desktop_page.py --root <记忆数据根目录> --subject user --show
python compiler/desktop_page.py --root <记忆数据根目录> --subject user --status
python compiler/desktop_page.py --root <记忆数据根目录> --subject user \
  --rewrite-file <新内容文件> --reason "<为什么改>" [--allow-deletions]
```

文件位置：`<root>/system/desktop/desktop-<主体>.md`（个人与鲸鱼娘各一张，
绝不共用）。`system/` 不在索引指纹范围里，写桌面页不会让索引变旧。
备份在 `<root>/system/desktop/backups/<主体>/`，只留最新 14 份。

## 格式

```markdown
<!-- MEM:START -->
# 桌面页（user）

## 人和约
- 宇枫：Stellamem 的主人
- 约：记忆不出本地

## 事
- Stellamem 正在补评审缺口

<!-- MEM:END -->
```

- `MEM:START` / `MEM:END` 标记与“人和约”“事”两节缺一不可，否则拒绝写入。
- “人和约”一节的行只能增、不能减：改写删了该节的行且未显式
  `--allow-deletions`（须已批）时拒绝写入，并列出被删的行。
- 内容由调用方显式给：种子内容是什么、改成什么，都由调用方决定；
  本模块不读 `core/user-core.md`，也不读任何记忆记录、不从记忆里抄。

## 规则

- **4096 字节硬上限**：按 UTF-8 字节数算；超了就拒绝写入，不静默截断。
- **改写必须可验证**：每次改写必须给 reason（为什么改），返回统一 diff；
  CLI 把 diff 打出来，调用方检查后再认。
- **原子写**：`file_lock.atomic_write_text`（tmp + rename + 锁 + fsync），
  写一半崩溃不留半截文件；覆盖前先把旧版拷进备份目录。
- **备份 14 份**：每次覆盖留一份旧版，按时间倒序只留最新 14 份，多余的删掉。

## 夜间结算

结算报告的“桌面”一行现在报真实字节数（按主体，如
`user 1234 字节；persona 567 字节`）；全部没有桌面页时仍报“未启用”，
不编数。详见 `docs/NIGHTLY-SETTLEMENT.md`。

## 外部致谢

- **来源项目**：另一套记忆系统 v17 定稿（用户 2026-11-05 发来的截图；
  项目名与仓库链接未知，来源待补）
- **采用的功能**：“4K 桌面页”的做法——AGENTS.md 里一段，总共 4096 字节，
  一半给人和约（永不自动删），一半给事
- **为什么采用**：调用方每次都要带的最精炼上下文，值得有一张受预算约束、
  改动可验证的固定页，而不是每次都从全库现拼
- **我们的适配与差异**：原版是单主体一张。我们改成每主体一张，绝不共用、
  绝不串主体；改写验证收紧（人和约删行要批、超字节拒绝不截断、每次改写
  必须给原因并留 diff）；写操作走 Stellamem 已有的原子写 + 锁 + 14 份备份，
  不另起一套
