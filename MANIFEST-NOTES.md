# SHA256SUMS.txt 说明（PATCH2 生成）

## 为什么换过

改进轮包里自带的 `SHA256SUMS.txt` 生成于改进**之前**：3 个被改文件哈希不符、
37 条列了但包内不存在（`docs/personal-edition/*`、`docs/p0/credential-map.json`），
12 个新文件未列入。用 `sha256sum -c` 会失败，不能作为交接凭据。
PATCH1 重新生成过一次；PATCH2 改用 text-mode 清单（见下）。

## 生成方式（PATCH2）

```python
# make_manifest.py（等价逻辑，跨平台稳定）
import hashlib
import subprocess
from pathlib import Path

root = Path("<包根目录>")
skip_parts = {"__pycache__", ".git", ".venv", "staging-data", ".ruff_cache"}
skip_prefix = ("docs/personal-edition/", "docs/p0/credential-map.json",
               "memory_service/work/", "memory_service/reports/")
# 只列已跟踪文件。rglob 会把 .ruff_cache 等未跟踪/忽略路径写进清单，
# 新鲜克隆上 sha256sum -c 会因为文件不存在而 FAILED。
raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=root)
paths = sorted(item.decode("utf-8") for item in raw.split(b"\0") if item)
lines = []
for rel in paths:
    parts = rel.split("/")
    if any(part in skip_parts for part in parts):
        continue
    if rel == "SHA256SUMS.txt" or rel.endswith(".pyc"):
        continue
    if any(rel == x or rel.startswith(x) for x in skip_prefix):
        continue
    data = (root / rel).read_bytes()
    lines.append(f"{hashlib.sha256(data).hexdigest()}  {rel}\n")
(root / "SHA256SUMS.txt").write_text("".join(lines), encoding="utf-8", newline="\n")
```

- 格式：`<sha256><两个空格><包内相对路径>`，**text-mode、LF**，不带 `*` 或 `./` 前缀；
- 因此 `sha256sum -c SHA256SUMS.txt` 在 Linux/macOS/Git Bash 上都可直接使用；
- PATCH1 的清单是 `find|xargs sha256sum` 生成（`hash *./path`），GNU `sha256sum -c`
  也能校验通过（已验证 251/251），但格式不统一，PATCH2 起统一为上面的格式。

## 校验

```bash
cd <解压目录>
sha256sum -c SHA256SUMS.txt        # 全部应为 OK
```

## 排除项与理由

| 排除 | 理由 |
|---|---|
| `SHA256SUMS.txt` 自身 | 自指哈希不可能稳定 |
| `docs/personal-edition/`（36 文件） | 生产机的隐私/审计报告，未随包交付 |
| `docs/p0/credential-map.json` | 凭据映射（敏感），未随包交付 |
| `__pycache__/`、`*.pyc` | 运行副产物 |
| `.ruff_cache/` | ruff 本地缓存，已在 `.gitignore`，不进版本库 |
| 未跟踪 / 被 gitignore 的路径 | 清单只覆盖 `git ls-files`；工作区里的缓存不能进交接凭据 |
| `.git/`、`_bak/` | 历史/备份，按交接说明不打包 |
| `memory_service/work`、`memory_service/reports` | 数据与浏览器 profile，按交接说明不打包 |
| `staging-data/` | 仅 staging 测试现场，不属于交接包 |

清单只覆盖“实际交付的代码与文档”。如果日后补打隐私文件，请重新生成并同步更新本说明。
