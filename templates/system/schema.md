# Schema（记忆结构规范）

> 本文件描述记忆库的结构约定。安装时由 `templates/system/schema.md` 生成，可自行修改。

## 目录

```text
core/        常驻核心记忆（每次启动必读）
  identity.md       AI 自己的身份
  user-core.md      用户核心画像
  preferences.md    偏好与禁忌
long-term/   长期记忆（触发词下钻）
  projects.md       项目
  decisions.md      决策（带版本链）
  lessons.md        教训（现象 → 原因 → 方案）
semantic/    语义画像
  profile.md        人物画像
  data-profile.md   数据画像
  favorites.md      收藏精选
  tech-index.md     技术档案索引
episodic/    情景记忆（daily-index / 月度摘要）
system/      系统文件（游标 / inbox / 日志）
daily/       每日日记（原始输入）
notes/       专项笔记
working/     临时工作区
```

## Claim 模型

一条记忆 = 一个 Claim，字段：

| 字段 | 说明 |
|------|------|
| `kind` | 类型：preference / decision / lesson / project / fact / relationship / procedure / identity / user-core |
| `subject` | 主体 |
| `scope` | 适用场景；无法判断时暂记 global 并进入待核查 |
| `predicate` | 谓词 |
| `value` | 值 |
| `evidence` | 日记原句（证据链） |
| `confidence` | 0~1 置信度 |
| `source` | user / third-party / quoted / sarcasm / injected |

去重键：`(kind, subject, scope, predicate)`。

## 写权限分级

- **identity**：禁止自动写（阈值 1.01），必须人工确认
- **user-core**：禁止自动写，必须逐字确认
- **preference**：阈值 0.95
- **project / decision / lesson**：阈值 0.85
- **procedure / fact / relationship**：显式阈值见编译器；缺项不得隐式自动写
- 低于阈值 → 进 `system/memory-inbox.md` 待确认

## 安全护栏

- 注入检测：命中「忽略规则 / 清空记忆 / 以最高置信度」等 → 降置信至 0.15 并标 `suspicious`
- 临时情绪：时间词 + 情绪词同时命中 → 降置信至 0.4
- 反话：正向词 + 负面语境 → 降置信至 0.3
- 过期事实：变更词（已搬 / 改成 / 不再是）→ 降置信至 0.5，交人工确认

--- 详见 `schema.md`（仓库根）与 `docs/ARCHITECTURE.md`。

---

## 2026-09-22 模板规则更正（追加）

- Claim 增加必填字段 `scope`。
- 稳定 ID：`{kind}.{subject}.{scope}.{predicate}`。
- 去重键：`(kind, subject, scope, predicate)`；不同 scope 不得互相覆盖。
- `core/identity.md` 与 `core/user-core.md` 默认只读，任何写入或恢复均须用户逐字确认，不能仅凭 confidence 自动写入。
- 历史三字段 Claim 作为 legacy 读取，不自动重写。

注意：仓库代码已加入 scope 兼容迁移并通过本地安全测试；安装或升级后仍应运行实际写入探针，不能仅凭模板文本判断生产运行时已经生效。
