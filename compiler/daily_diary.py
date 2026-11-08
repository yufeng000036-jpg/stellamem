"""日档五段 + 长期“我变了什么”。

每天一份日档，五段是触点、我的想、变、我的回、回声；长期只记
“我变了什么”，只从真实变化里提炼。按主体分开写，绝不串主体。

文件布局（都在记忆根目录下）：

- 日档：``<root>/daily/<subject>/<YYYY-MM-DD>.md``
- 长期：``<root>/long-term/self-changes-<subject>.md``

这两处都**不在索引里**：索引器只抓扁平的 ``daily/*.md``
（见 ``compiler/indexer.py`` 的 ``source_fingerprint`` 与建库循环，
都是 ``(root / "daily").glob("*.md")``，不递归），MANAGED 清单里
也没有 ``long-term/self-changes-*.md``。日档与长期是派生层，
不进召回，避免同一内容在原文与派生里重复出现、互相顶权重。

合成红线——**一律本地做，个人记忆不许出本机**：
v1 是规则版（摘录 + 拼接 + 标来源），不调任何模型，更不调云端。
DeepSeek 只跑过公开 benchmark 数据，绝不能用来写私人日记。
以后就算接本地模型，也只在 v1 的规则输出上做同一件事的升级，
合成行照样标明来源，云端模型永远不进这条链。

22:35 那步（``synthesize_long_term``，即 CLI ``--synthesize``）：

- 当天没日档 → 什么都不做，返回 noop，CLI 正常退出（exit 0），
  不建文件、不报错——没日记的那天本来就不该有长期变化。
- 日档在、但“变”一段为空 → 同样 noop。
- 有变化才写：把“变”一段的行原样摘到长期文件，每行自带
  ``（规则合成·来源：YYYY-MM-DD 日档“变”）``，已写过的行不再
  重复写（按原文去重，重复跑也是幂等的）。

写操作一律原子写（``file_lock.atomic_write_text``：tmp + rename +
锁 + fsync），崩溃不留半截文件。追加是单写者假设：每主体每天的
日档只由一条日间流程写，22:35 那步只读日档、只写长期文件——
两者写的是不同文件，不抢同一把锁。

借鉴致谢见 ``docs/DAILY-DIARY.md``（外部致谢节）。
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date as _date
from pathlib import Path

try:  # 包内与扁平导入都要能用（测试只把 compiler/ 加进 sys.path）
    from . import subjects as subjects_mod
    from .cli_output import ensure_utf8_stderr, ensure_utf8_stdout
    from .file_lock import atomic_write_text
except ImportError:  # pragma: no cover - flat layout
    import subjects as subjects_mod  # type: ignore
    from cli_output import ensure_utf8_stderr, ensure_utf8_stdout  # type: ignore
    from file_lock import atomic_write_text  # type: ignore

SEGMENTS = ("触点", "我的想", "变", "我的回", "回声")
"""五段，固定顺序；只记这五段，不另起段名。"""

_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_SYNTH_MARK = "规则合成"
"""合成行标记：v1 是本地规则摘录拼接，不是模型写，标记写实话。"""


# ---------------- 路径与校验 ----------------


def validate_date(value) -> str:
    """校验 YYYY-MM-DD（必须是真实日历日），返回原样字符串。"""
    if not isinstance(value, str):
        raise ValueError("date must be text like YYYY-MM-DD")
    m = _DATE_RE.match(value)
    if not m:
        raise ValueError(f"bad date: {value!r} (expected YYYY-MM-DD)")
    try:
        _date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        raise ValueError(f"bad date: {value!r} (not a real calendar day)") from None
    return value


def diary_path(root, subject, day) -> Path:
    """日档路径：<root>/daily/<subject>/<YYYY-MM-DD>.md。"""
    canon = subjects_mod.validate_subject(subject)
    return Path(root) / "daily" / canon / f"{validate_date(day)}.md"


def long_term_path(root, subject) -> Path:
    """长期文件路径：<root>/long-term/self-changes-<subject>.md。"""
    canon = subjects_mod.validate_subject(subject)
    return Path(root) / "long-term" / f"self-changes-{canon}.md"


# ---------------- 日档读写 ----------------


def _skeleton(subject, day) -> str:
    canon = subjects_mod.validate_subject(subject)
    lines = [f"# 日档（{canon} · {validate_date(day)}）", ""]
    for seg in SEGMENTS:
        lines.append(f"## {seg}")
        lines.append("")
    return "\n".join(lines)


def parse_diary(text: str) -> dict[str, list[str]]:
    """把日档文本按五段切开，只取 `- ` 行（不含前缀），未知段落忽略。"""
    out: dict[str, list[str]] = {seg: [] for seg in SEGMENTS}
    current = None
    for line in text.splitlines():
        if line.startswith("## "):
            name = line[3:].strip()
            current = name if name in SEGMENTS else None
        elif current is not None and line.startswith("- "):
            out[current].append(line[2:])
    return out


def read_diary(root, subject, day) -> dict[str, list[str]] | None:
    """读某日的日档；文件不存在返回 None（不是空 dict）。"""
    path = diary_path(root, subject, day)
    try:
        return parse_diary(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def _insert_line(text: str, segment: str, bullet: str) -> str:
    """把一行 `- ...` 插进指定段尾；段不存在时在文件末尾补段再插。

    其余内容原样保留，不动别的段。
    """
    lines = text.splitlines()
    head = f"## {segment}"
    try:
        at = lines.index(head)
    except ValueError:
        if lines and lines[-1] != "":
            lines.append("")
        lines.append(head)
        lines.append(bullet)
        return "\n".join(lines) + "\n"
    nxt = len(lines)
    for i in range(at + 1, len(lines)):
        if lines[i].startswith("## "):
            nxt = i
            break
    lines.insert(nxt, bullet)
    return "\n".join(lines) + "\n"


def append_diary(root, subject, day, segment, text) -> dict:
    """往某日日档的指定段追加一行。文件不存在时先按五段骨架建出来。

    单行文本：含换行或空白一律拒绝（日档一行就是一个要点，
    多行请分多次追加）。返回回执：路径 / 段 / 写入的行 / 写后字节数。
    """
    if segment not in SEGMENTS:
        raise ValueError(f"unknown segment: {segment!r} (expected one of {SEGMENTS})")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("diary line must be non-empty text")
    if "\n" in text or "\r" in text:
        raise ValueError("diary line must be a single line (no newlines)")
    path = diary_path(root, subject, day)
    current = path.read_text(encoding="utf-8") if path.exists() else _skeleton(subject, day)
    new_text = _insert_line(current, segment, f"- {text.strip()}")
    atomic_write_text(path, new_text)
    return {
        "path": str(path),
        "subject": subjects_mod.validate_subject(subject),
        "date": validate_date(day),
        "segment": segment,
        "line": text.strip(),
        "bytes": len(new_text.encode("utf-8")),
    }


def diary_status_text(root, subject, day) -> str:
    """一行状态：有档报段与行数，无档报“无日档”，不编数。"""
    parsed = read_diary(root, subject, day)
    canon = subjects_mod.validate_subject(subject)
    if parsed is None:
        return f"{canon} {day}：无日档"
    total = sum(len(v) for v in parsed.values())
    filled = ", ".join(f"{seg}{len(parsed[seg])}" for seg in SEGMENTS if parsed[seg])
    return f"{canon} {day}：有日档，共 {total} 行（{filled or '各段均空'}）"


# ---------------- 长期“我变了什么” ----------------


def _long_term_header(subject) -> str:
    canon = subjects_mod.validate_subject(subject)
    return (
        f"# 我变了什么（{canon}）\n"
        "\n"
        "<!-- 由本地规则从当日日档“变”一段摘录拼接而成，不经过任何云端模型；"
        "每行自带来源日，只记真实变化，不编造。 -->\n"
        "\n"
    )


def read_long_term(root, subject) -> str | None:
    """读长期文件全文；不存在返回 None。"""
    path = long_term_path(root, subject)
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


# ---------------- 已总结标记（持久化 + 启动重建） ----------------
# 布雷登 3.2：增量总结不能只按容量压缩，要带可持久化的「已总结」标记，
# 并在启动时从备份重建断层，否则重启后历史总结会静默丢失。我方之前
# 只靠每晚结算报数事后发现（条数对不上才知道丢了）；标记 + 重建把
# 事后发现升级为主动防丢。
# 标记文件是派生状态，不是权威库：权威永远是日档 + 长期文件本身。
# 标记丢了或坏了，rebuild_summary_state() 就从文件重建它。

_SUMMARY_STATE_FILENAME = "diary-summary-state.json"
_SOURCE_DAY_RE = re.compile(r"来源：(\d{4}-\d{2}-\d{2})")


def summary_state_path(root) -> Path:
    """标记文件路径：<root>/system/diary-summary-state.json。"""
    return Path(root) / "system" / _SUMMARY_STATE_FILENAME


def _read_summary_state(root) -> dict:
    path = summary_state_path(root)
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("subjects"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"schema_version": 1, "subjects": {}}


def _write_summary_state(root, state: dict) -> None:
    import json
    from datetime import datetime, timezone

    state["schema_version"] = 1
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    path = summary_state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def record_summary(root, subject, day) -> dict:
    """把一天记为已总结（synthesize_long_term 成功后调用）。"""
    canon = subjects_mod.validate_subject(subject)
    day = validate_date(day)
    state = _read_summary_state(root)
    entry = state["subjects"].setdefault(canon, {"summarized_days": []})
    days = entry.setdefault("summarized_days", [])
    if day not in days:
        days.append(day)
        days.sort()
    entry["last_summarized_day"] = days[-1] if days else None
    _write_summary_state(root, state)
    return {"subject": canon, "summarized_days": list(days)}


def rebuild_summary_state(root, subject) -> dict:
    """启动时从文件重建标记：找出断层（有变化但未进长期的日子）。

    权威是文件：长期文件里合成行的「来源：YYYY-MM-DD」给出已总结
    的日子；日档里「变」一段非空的日子是有变化的日子。两者之差就
    是断层。重建后把标记文件写回去；调用方按 gap_days 补跑
    synthesize_long_term 即可。
    """
    canon = subjects_mod.validate_subject(subject)
    root = Path(root)
    summarized: set[str] = set()
    text = read_long_term(root, canon)
    if text:
        for line in text.splitlines():
            if _SYNTH_MARK in line:
                m = _SOURCE_DAY_RE.search(line)
                if m:
                    summarized.add(m.group(1))
    with_changes: set[str] = set()
    diary_dir = root / "daily" / canon
    if diary_dir.exists():
        for path in sorted(diary_dir.glob("*.md")):
            day = path.stem
            try:
                validate_date(day)
            except ValueError:
                continue
            parsed = read_diary(root, canon, day)
            if parsed and [x for x in parsed.get("变", []) if x.strip()]:
                with_changes.add(day)
    gap = sorted(with_changes - summarized)
    state = _read_summary_state(root)
    state["subjects"][canon] = {
        "summarized_days": sorted(summarized),
        "last_summarized_day": sorted(summarized)[-1] if summarized else None,
        "gap_days": gap,
    }
    _write_summary_state(root, state)
    return {
        "subject": canon,
        "summarized_days": sorted(summarized),
        "gap_days": gap,
        "state_path": str(summary_state_path(root)),
    }


def _already_recorded(existing_text: str | None, raw_line: str) -> bool:
    """这行变化是不是已经写进长期了（按原文去重，不看来源日）。"""
    if not existing_text:
        return False
    prefix = f"- {raw_line}（"
    return any(line.startswith(prefix) for line in existing_text.splitlines())


def synthesize_long_term(root, subject, day) -> dict:
    """22:35 那步：从当日日档“变”一段提炼长期变化。

    - 当天没日档 → noop，什么都不做、不建文件。
    - “变”一段为空 → noop。
    - 有变化 → 只把没写过的行追加进长期文件，每行自带
      ``（规则合成·来源：YYYY-MM-DD 日档“变”）``；重复跑不重复写。
    只读日档、不改日档；只写长期文件。
    """
    canon = subjects_mod.validate_subject(subject)
    day = validate_date(day)
    parsed = read_diary(root, canon, day)
    if parsed is None:
        return {"status": "noop", "reason": "no-diary", "subject": canon, "date": day}
    changes = [line for line in parsed.get("变", []) if line.strip()]
    if not changes:
        return {"status": "noop", "reason": "no-changes", "subject": canon, "date": day}
    path = long_term_path(root, canon)
    existing = path.read_text(encoding="utf-8") if path.exists() else None
    added, skipped = [], 0
    for line in changes:
        raw = line.strip()
        if _already_recorded(existing, raw):
            skipped += 1
            continue
        added.append(f"- {raw}（{_SYNTH_MARK}·来源：{day} 日档“变”）")
    if added:
        base = existing if existing is not None else _long_term_header(canon)
        if base and not base.endswith("\n"):
            base += "\n"
        atomic_write_text(path, base + "\n".join(added) + "\n")
    total = 0
    if path.exists():
        total = sum(
            1
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.startswith("- ") and _SYNTH_MARK in line
        )
    # 持久化「已总结」标记：这一天的变化已经进长期了。标记写失败不
    # 能拖垮合成本身（标记是派生状态，重建可恢复），静默跳过。
    try:
        record_summary(root, canon, day)
    except OSError:
        pass
    return {
        "status": "ok",
        "reason": "synthesized",
        "subject": canon,
        "date": day,
        "added": len(added),
        "skipped": skipped,
        "path": str(path),
        "lines_in_file": total,
    }


# ---------------- CLI ----------------


def main(argv=None) -> int:
    """无头 CLI：写日档、看日档、跑 22:35 合成、看长期。"""
    # Windows 控制台默认编码不是 UTF-8，印中文会炸（桌面页 CLI 在
    # CI run 42171748727 的 Windows 两档上就是这么红的）。启动时先切。
    ensure_utf8_stdout()
    ensure_utf8_stderr()
    parser = argparse.ArgumentParser(description="日档五段 + 长期“我变了什么”。")
    parser.add_argument("--root", required=True, help="记忆根目录")
    parser.add_argument(
        "--subject", required=True, help="主体：user / persona / viewer（别名也认）"
    )
    parser.add_argument(
        "--date", default=_date.today().isoformat(), help="日期 YYYY-MM-DD，默认今天"
    )
    parser.add_argument("--append", action="store_true", help="往指定段追加一行")
    parser.add_argument(
        "--segment", default=None, help="五段之一：触点 / 我的想 / 变 / 我的回 / 回声"
    )
    parser.add_argument("--text", default=None, help="要追加的一行文本")
    parser.add_argument("--show", action="store_true", help="打印当日日档")
    parser.add_argument(
        "--synthesize", action="store_true", help="22:35 那步：从当日日档提炼长期变化"
    )
    parser.add_argument("--long-term", action="store_true", help="打印长期“我变了什么”")
    parser.add_argument(
        "--rebuild-summary-state",
        action="store_true",
        help="启动重建：从日档 + 长期文件重建「已总结」标记并报告断层",
    )
    args = parser.parse_args(argv)
    try:
        if args.append:
            if args.segment is None or args.text is None:
                parser.error("--append 需要 --segment 与 --text")
            receipt = append_diary(args.root, args.subject, args.date, args.segment, args.text)
            print(
                f"已写入 {receipt['subject']} {receipt['date']} “{receipt['segment']}”：{receipt['line']}"
            )
            return 0
        if args.synthesize:
            result = synthesize_long_term(args.root, args.subject, args.date)
            if result["status"] == "noop":
                if result["reason"] == "no-diary":
                    print(f"{args.date} 无日档，什么都不做")
                else:
                    print(f"{args.date} 日档“变”一段为空，什么都不做")
            else:
                print(
                    f"已合成：新增 {result['added']} 行，跳过重复 {result['skipped']} 行"
                    f" → {result['path']}"
                )
            return 0
        if args.show:
            parsed = read_diary(args.root, args.subject, args.date)
            if parsed is None:
                print(diary_status_text(args.root, args.subject, args.date))
            else:
                print(diary_path(args.root, args.subject, args.date).read_text(encoding="utf-8"))
            return 0
        if args.long_term:
            text = read_long_term(args.root, args.subject)
            if text is None:
                print(f"{subjects_mod.validate_subject(args.subject)}：长期文件还没有内容")
            else:
                print(text)
            return 0
        if args.rebuild_summary_state:
            result = rebuild_summary_state(args.root, args.subject)
            print(
                f"已总结 {len(result['summarized_days'])} 天"
                f"（{result['summarized_days'][-1] if result['summarized_days'] else '无'} 为止）"
            )
            if result["gap_days"]:
                print(f"断层 {len(result['gap_days'])} 天未进长期：{', '.join(result['gap_days'])}")
            else:
                print("无断层")
            return 0
        parser.error("请指定 --append、--show、--synthesize 或 --long-term 其中一个")
        return 2
    except ValueError as exc:
        print(f"日档失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
