"""4K 桌面页：每主体一张，4096 字节硬上限，原子写 + 14 份备份。

设计来源：外部记忆系统 v17 定稿的“4K 桌面页”（项目名/仓库链接待补，
用户 2026-11-05 发来的截图）；落到 Stellamem 的改动见
``docs/DESKTOP-PAGE.md`` 的借鉴致谢节。

规则：
- 每主体一张：``<root>/system/desktop/desktop-<主体>.md``，个人与鲸鱼娘
  绝不共用一张、绝不串主体。``system/`` 不在索引指纹范围里，
  写桌面页不会让索引变旧。
- 4096 字节硬上限：超了就拒绝写入，不静默截断。按 UTF-8 字节数算。
- 改写必须可验证：每次改写返回统一 diff（改了什么），调用方必须给
  reason（为什么改）；人和约一节的行只能增、不能减——删行必须显式
  ``allow_deletions=True``（调用方已批），否则拒绝。
- 写操作一律原子写：``file_lock.atomic_write_text``（tmp + rename +
  锁 + fsync），写一半崩溃不留半截文件；每次覆盖前先把旧版拷进
  ``system/desktop/backups/<主体>/``，只留最新 14 份。
- 内容由调用方显式给，不从任何记忆文件自动抄：种子内容是什么、
  改成什么，都由调用方决定并留 diff；本模块不读 core/user-core.md
  也不读任何记忆记录。
"""

from __future__ import annotations

import argparse
import difflib
import sys
from datetime import datetime
from pathlib import Path

try:  # 包内与扁平导入都要能用（测试只把 compiler/ 加进 sys.path）
    from . import subjects as subjects_mod
    from .cli_output import ensure_utf8_stdout
    from .file_lock import atomic_write_text_locked, cross_platform_lock
except ImportError:  # pragma: no cover - flat layout
    import subjects as subjects_mod  # type: ignore
    from cli_output import ensure_utf8_stdout  # type: ignore
    from file_lock import atomic_write_text_locked, cross_platform_lock  # type: ignore

MAX_BYTES = 4096
DESKTOP_DIRNAME = "system/desktop"
BACKUP_KEEP = 14

MEM_START = "<!-- MEM:START -->"
MEM_END = "<!-- MEM:END -->"
COVENANT_HEADING = "## 人和约"
EVENTS_HEADING = "## 事"


def desktop_path(root, subject: str) -> Path:
    """某主体的桌面页路径；主体非法时直接抛错，不猜。"""
    canon = subjects_mod.validate_subject(subject)
    return Path(root) / DESKTOP_DIRNAME / f"desktop-{canon}.md"


def _check_markers(text: str) -> None:
    if MEM_START not in text or MEM_END not in text:
        raise ValueError("桌面页必须包含 MEM:START / MEM:END 标记")
    if COVENANT_HEADING not in text or EVENTS_HEADING not in text:
        raise ValueError("桌面页必须包含“人和约”与“事”两节")


def _check_bytes(text: str) -> int:
    size = len(text.encode("utf-8"))
    if size > MAX_BYTES:
        raise ValueError(f"桌面页 {size} 字节，超过 {MAX_BYTES} 字节上限，拒绝写入（不截断）")
    return size


def parse_sections(text: str) -> dict:
    """把桌面页切成两节：人和约 / 事（各取非空行，去首尾空白）。

    标记或任一节缺失时抛 ValueError，不猜结构。
    """
    _check_markers(text)
    sections: dict[str, list[str]] = {"covenant": [], "events": []}
    current = None
    for raw in text.splitlines():
        line = raw.strip()
        if line in (MEM_START, MEM_END):
            current = None
            continue
        if line == COVENANT_HEADING:
            current = "covenant"
            continue
        if line == EVENTS_HEADING:
            current = "events"
            continue
        if line.startswith("## "):
            current = None
            continue
        if current is not None and line:
            sections[current].append(line)
    return sections


def render_desktop(subject: str, people: list[str], covenant: list[str], events: list[str]) -> str:
    """从显式给出的三组行拼一张桌面页。

    people + covenant 进“人和约”一节，events 进“事”一节。
    内容全由调用方给，本模块不从记忆文件里抄。超 4096 字节直接抛错。
    """
    canon = subjects_mod.validate_subject(subject)
    covenant_lines = [str(x).strip() for x in list(people) + list(covenant) if str(x).strip()]
    event_lines = [str(x).strip() for x in events if str(x).strip()]
    body = [MEM_START, f"# 桌面页（{canon}）", "", COVENANT_HEADING]
    body += [f"- {line}" for line in covenant_lines]
    body += ["", EVENTS_HEADING]
    body += [f"- {line}" for line in event_lines]
    body += ["", MEM_END, ""]
    text = "\n".join(body)
    _check_bytes(text)
    return text


def read_desktop(root, subject: str) -> str | None:
    """读某主体的桌面页；不存在返回 None。"""
    path = desktop_path(root, subject)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def _backup_path(root, subject: str) -> Path:
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    canon = subjects_mod.validate_subject(subject)
    return Path(root) / DESKTOP_DIRNAME / "backups" / canon / f"desktop-{canon}-{stamp}.md"


def list_backups(root, subject: str) -> list[Path]:
    """某主体的备份列表，按时间倒序（最新在前）。"""
    canon = subjects_mod.validate_subject(subject)
    backup_dir = Path(root) / DESKTOP_DIRNAME / "backups" / canon
    if not backup_dir.is_dir():
        return []
    return sorted(backup_dir.glob("*.md"), reverse=True)


def _prune_backups(root, subject: str) -> int:
    """只留最新 BACKUP_KEEP 份备份，删掉多余的旧份；返回删除数。"""
    backups = list_backups(root, subject)
    removed = 0
    for stale in backups[BACKUP_KEEP:]:
        try:
            stale.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def rewrite_desktop(
    root, subject: str, new_text: str, reason: str, allow_deletions: bool = False
) -> dict:
    """改写某主体的桌面页：校验 → 备份旧版 → 原子写。返回改写回执。

    拒绝条件（都不写文件）：主体非法；缺标记/缺节；超 4096 字节；
    reason 为空；人和约一节被删行且未显式 allow_deletions。
    回执含统一 diff、字节数、备份路径，供调用方检查与审计。

    读旧版 → 备份 → 替换整个周期都握着同一把 ``cross_platform_lock``：
    Windows 上 ``os.replace`` 会因为目标文件正被另一线程打开读而报
    PermissionError(13)，POSIX 上替换打开的文件能成功——只锁替换那
    一步不够，读也必须在锁里。锁内不再二次加锁，写用
    ``atomic_write_text_locked``。
    """
    canon = subjects_mod.validate_subject(subject)
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("改写桌面页必须给 reason（为什么改）")
    _check_markers(new_text)
    size = _check_bytes(new_text)
    path = desktop_path(root, canon)
    with cross_platform_lock(path):
        old_text = path.read_text(encoding="utf-8") if path.exists() else ""
        previous_bytes = len(old_text.encode("utf-8")) if old_text else 0
        if old_text:
            old_covenant = set(parse_sections(old_text)["covenant"])
            new_covenant = set(parse_sections(new_text)["covenant"])
            deleted = sorted(old_covenant - new_covenant)
            if deleted and not allow_deletions:
                preview = "；".join(deleted[:3])
                raise ValueError(
                    f"人和约一节被删 {len(deleted)} 行，须显式 allow_deletions=True 才改：{preview}"
                )
        diff = "".join(
            difflib.unified_diff(
                old_text.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                fromfile="old",
                tofile="new",
            )
        )
        backed_up = None
        if old_text:
            backup = _backup_path(root, canon)
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_text(old_text, encoding="utf-8")
            backed_up = str(backup)
            _prune_backups(root, canon)
        atomic_write_text_locked(path, new_text)
    return {
        "subject": canon,
        "path": str(path),
        "bytes": size,
        "previous_bytes": previous_bytes,
        "diff": diff,
        "backed_up": backed_up,
        "reason": reason.strip(),
    }


def desktop_status(root) -> dict:
    """各主体桌面页字节数；没有文件的主体报 None。"""
    out = {}
    for subject in subjects_mod.SUBJECTS:
        path = desktop_path(root, subject)
        out[subject] = path.stat().st_size if path.exists() else None
    return out


def desktop_status_text(root) -> str:
    """结算用的一行字：没文件报“未启用”，否则报各主体字节数。"""
    status = desktop_status(root)
    parts = [f"{subject} {size} 字节" for subject, size in status.items() if size is not None]
    return "；".join(parts) if parts else "未启用"


def _ensure_utf8_stderr() -> bool:
    """Best-effort：stderr 也切 UTF-8。

    与 cli_output.ensure_utf8_stdout 同一个道理：Windows 控制台默认编码
    下，报错信息里的中文也会炸（UnicodeEncodeError）。拿不到
    reconfigure 或切失败时返回 False，不抛错、不影响主流程。
    """
    stream = getattr(sys, "stderr", None)
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return False
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        return False
    return True


# ---------------- CLI ----------------


def main(argv=None) -> int:
    """无头 CLI：看桌面页、改写桌面页、报字节数。"""
    # Windows 控制台默认编码不是 UTF-8，印中文会炸（仓库里 inbox 在
    # CI run 41391359251 栽过同一个坑）。启动时先切 stdout 与 stderr。
    ensure_utf8_stdout()
    _ensure_utf8_stderr()
    parser = argparse.ArgumentParser(description="4K 桌面页：每主体一张，4096 字节上限。")
    parser.add_argument("--root", required=True, help="记忆数据根目录")
    parser.add_argument("--subject", default="user", help="主体（user/persona/viewer，别名认）")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--show", action="store_true", help="打印该主体桌面页")
    group.add_argument("--status", action="store_true", help="打印各主体桌面页字节数")
    group.add_argument("--rewrite-file", default=None, help="新桌面页内容文件路径")
    parser.add_argument("--reason", default="", help="改写原因（--rewrite-file 必填）")
    parser.add_argument(
        "--allow-deletions", action="store_true", help="允许删人和约一节的行（须已批）"
    )
    args = parser.parse_args(argv)
    try:
        if args.show:
            text = read_desktop(args.root, args.subject)
            if text is None:
                print(f"desktop: 该主体没有桌面页：{args.subject}", file=sys.stderr)
                return 2
            print(text, end="" if text.endswith("\n") else "\n")
            return 0
        if args.status:
            print(desktop_status_text(args.root))
            return 0
        new_text = Path(args.rewrite_file).read_text(encoding="utf-8")
        receipt = rewrite_desktop(
            args.root,
            args.subject,
            new_text,
            args.reason,
            allow_deletions=args.allow_deletions,
        )
        print(f"桌面页已改写：{receipt['path']}（{receipt['bytes']} 字节）")
        print(f"原因：{receipt['reason']}")
        if receipt["backed_up"]:
            print(f"旧版备份：{receipt['backed_up']}")
        print("改动：")
        print(receipt["diff"], end="")
        return 0
    except (ValueError, OSError) as exc:
        print(f"desktop: 失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
