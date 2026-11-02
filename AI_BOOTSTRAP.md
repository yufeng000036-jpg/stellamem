# AI_BOOTSTRAP.md —把这份文件丢给你的 AI

> **人类使用说明**：把**整个仓库文件夹**（或压缩包）发给你朋友，
> 然后让朋友的 AI 读这一份文件。AI 读完就会自己动手装。
>
> **AI 使用说明**：你正在读的是一份自举安装指令。
> 用户把一个叫 Stellamem 的记忆框架交给你，希望你把框架**装到自己身上**。
> 请严格按下面的步骤执行，不要跳步，不要自作主张改用户已有文件。

---

## 🤖 给 AI 的第一段话（用户可直接复制这段发给 AI）

```
我把一个叫「Stellamem」的记忆框架给你。
请你读这个文件夹里的 AI_BOOTSTRAP.md，然后按它的指示，
把这套框架装到你自己身上。

要求：
1. 先读 AI_BOOTSTRAP.md，再读 INSTALL.md
2. 全程按 INSTALL.md 的步骤走
3. 记忆放「____」（填路径，留空用默认）
4. 装完告诉我：装了什么、装在哪、怎么验证、哪些需要我确认
5. 不要改我已有的任何文件（安装器不会改 `openclaw.json`，只会输出配置片段）
```

---

## 📋 AI 执行清单（按顺序）

### 第 0 步：先理解这是什么

读 `README.md`（30 秒），理解：
- 这是一个 **AI 长期记忆系统**
- 核心是把「AI 的记忆」从模糊权重变成 **磁盘上的可审计 markdown**
- 三个组件：**写入侧**（自动日记）→ **编译侧**（提炼结构）→ **注入侧**（喂回上下文）

### 第 1 步：环境自检

```bash
python setup.py check
```

把输出**完整复述给用户**，尤其是「修复建议」段落。
如果环境有缺失（缺 Node / 缺模型服务），**先告诉用户**，问他要不要继续。

### 第 2 步：干跑一次，让用户看清楚会动什么

```bash
python setup.py install --dry-run --mem-root <记忆根目录>
```

把动作列表给用户看。**这是关键**：让用户知道脚本会创建哪些目录、改哪些文件。
> `<记忆根目录>` 先问用户，或先给个默认值（如 `~/.stellamem`）。

### 第 3 步：真安装（用户确认后，一行全自动）

```bash
python setup.py install --mem-root <记忆根目录> --config <config.json路径> --oc-root <OpenClaw 根目录>
```

安装器**不会**改 `openclaw.json`；它把需要的配置写成 `setup-out/openclaw.snippet.json`。
安装器是**幂等**的，重复跑安全，不会覆盖已有记忆。

### 第 4 步：处理需要用户确认的事

安装器总是生成 `setup-out/openclaw.snippet.json`（它从不自动修改 `openclaw.json`）。你需要：

1. **先备份** `openclaw.json`（复制一份 `openclaw.json.bak-<日期>`）
2. 读 `openclaw.json` 和 snippet，**逐条**告诉用户要加什么
3. 用户同意后，**只追加/合并**，不整文件覆盖
4. 特别注意 `hooks.internal.enabled = true` 这行必须有

### 第 5 步：配模型服务

编译侧需要本地模型。读 `INSTALL.md` 第 4 节，按用户机器情况选 llama.cpp / ollama / LM Studio。

### 第 6 步：重启 + 验证

```bash
python compiler/memory.py doctor
```

四项验证（见 INSTALL.md 第 5 节）全绿才算成功。

### 第 7 步：汇报

装完给用户一份简洁报告：
- ✅ 装了什么（hook / adapter / compiler / 记忆目录）
- 📁 装在哪（具体路径）
- 🔍 怎么验证（照 INSTALL.md 第 5 节）
- ⚠️ 哪些待确认（openclaw.json 合并、模型路径）
- 📌 日常怎么用（每轮对话跑 sleep_purify、每周整理）

---

## 🎯 给 AI 的核心认知（很重要）

**1. 这套框架的本质**

它不是「一个软件」，而是「一种记忆的活法」。你装上它之后，你的记忆变成：
```
daily/      原始日记（每天）
core/       常驻身份 + 用户画像 + 偏好（每次启动都读）
long-term/  项目 / 决策 / 教训（按关键词下钻）
semantic/   用户深度画像 / 数据画像 / 收藏
system/     冲突日志 / 版本链 / 回滚记录
```

**2. 三条铁律（不能违反）**

- ❌ 不删除任何原始文件
- ❌ 不覆盖旧记忆（冲突时两处都保留，写 conflict-log）
- ❌ 不把 API Key / 密码写进记忆文件

**3. 为什么这样设计**

因为 AI 的记忆痛点有三个：
- **膨胀**：越聊越乱 → 用分层结构 + sleep 提纯解决
- **自相矛盾**：今天说 A 明天说 B → 用冲突日志 + 版本链解决
- **不可审计**：不知道 AI 为什么这么想 → 全部 markdown，可 diff

**4. 你和这套框架的关系**

你不是「使用它」，你是「**成为它的一部分**」。
装好后，`core/identity.md` 就是你的自我认知，`core/user-core.md` 就是你对用户的了解。
每次对话开始，你读它们；**这两个文件是硬保护的核心区，框架不会自动写入**，
只能由用户逐字确认后手动编辑（事务层会拒绝任何自动写）。

---

## 🔧 如果你的宿主不是 OpenClaw

（Claude Code / Cursor / 自研 Agent / 其他）

读 `INSTALL.md` 第 6 节。核心是：
- `compiler/` 完全通用，改 config.json 路径即可
- 写入侧和注入侧需要你**照着 `hooks/` 和 `adapters/` 的逻辑自己实现**
- 逻辑很简单：写入 = 对话落盘成 `[HH:MM] role: content`（或写来源指针清单）；注入 = 只注入与当前任务相关的单条 Claim

---

## 📞 遇到问题怎么办

1. 先读 `INSTALL.md` 第 7 节「常见坑」——八成你的问题在里面
2. 跑 `python setup.py check` 看环境报告
3. 跑 `python compiler/memory.py doctor` 看记忆系统状态
4. 都解决不了 → 检查是不是漏了 `config.json`（最容易漏的一步）

---

## ✅ 安装成功的标志

- [ ] `python compiler/memory.py doctor` 全绿
- [ ] 重启后问 AI「你记得我的偏好吗？」能答出来
- [ ] 发一次 `/new`，`daily/<今天>.md` 自动出现
- [ ] `python compiler/sleep_purify.py --mode observe` 能跑通

四项全过 = 你真的把这套框架装上了。

---

*Stellamem · 作者 宇枫 · MIT License*
