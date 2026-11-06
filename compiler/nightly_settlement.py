"""夜间结算报数：每天一次、看得见的记忆自报。

只读统计：读 MANAGED 记忆文件与 append-only 写入链
（``system/version-chain.json``），产出四个数 + 两行状态。
另可接 Hit 投票文件（``--votes``，见 ``docs/HIT-VOTES.md``），
多报一节命中数（今日 / 累计，分主体；只报数，不报内容）。
投票文件不存在时命中一节报“未启用”，损坏时结算失败，不报不准的数。
只写两样东西：带日期的结算日志，与下次结算用的基线状态文件。
绝不写记忆库、不改任何记录、不碰注入策略、不碰主体隔离与桥治理。

四个数：

1. 各层几条：active 记录按主体 × 类型计数。user 主体分
   preference / lesson / decision / project / fact + 其他；
   persona / viewer 只报总数（不报内容）；pending（inbox 未审）单列；
   superseded / rejected 只计入“历史”，不进各层。
2. 桌面多少字节：按主体报桌面页真实字节数（见 compiler/desktop_page.py）；
   没有桌面页的主体不报，全部没有时报“未启用”，不编数。
3. 今晚动了几条：自上次结算以来写入链新增的事件数。基线存在
   上次结算的状态文件里（时间戳 + 事件总数）；首次运行没有基线，
   报“首次结算、无上次基线”，不猜。批准/驳回等状态流转不经过写入链，
   不计入这一条，它们体现在各层计数的变化里。
4. 日志叫什么：本次结算日志的文件名与路径。

两行状态：

- 后台重建：服务对象在场时用它本进程的真实失败记录；冷启动的 CLI
  没有那个进程，只能用索引新鲜度推断——新鲜 = 最近一次重建成功，
  过期 = 源已变化、待重建，不存在 = 未建索引。
- 索引行数与生产库最后写入时间：生产库最后写入时间取各管理文件与
  写入链中最新的 mtime。

失败处理：管理文件读不懂（损坏的 canonical 记录块）或写入链/基线
文件损坏时，本次结算直接失败（CLI 非零退出，不写日志、不更新基线），
绝不拿不准的数当真的报。

借鉴致谢见 ``docs/NIGHTLY-SETTLEMENT.md``（外部致谢节）。
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

try:  # 包内与扁平导入都要能用（测试只把 compiler/ 加进 sys.path）
    from . import desktop_page
    from . import hit_votes
    from . import indexer
    from . import subjects
    from .storage import load_records
except ImportError:  # pragma: no cover - flat layout
    import desktop_page
    import hit_votes
    import indexer
    import subjects
    from storage import load_records

KINDS_REPORTED = ("preference", "lesson", "decision", "project", "fact")
SETTLEMENT_DIRNAME = "nightly-settlement"
BASELINE_FILENAME = "baseline.json"
LOG_PREFIX = "settlement-"
VERSION_CHAIN_REL = "system/version-chain.json"


def _parse_time(value):
    """ISO 时间字符串 → aware datetime；解析不了返回 None。"""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # 写入链里的旧事件可能没有时区：按 UTC 理解，不静默换算。
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _atomic_write_text(path: Path, text: str) -> None:
    """先写同目录 tmp 再改名：写一半断电也不会留半个文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# ---------------- 计数 ----------------


def collect_counts(root) -> dict:
    """读 MANAGED 文件，按主体 × 类型数 active 记录。

    user 主体按 kind 分五类 + 其他；persona / viewer 只报总数；
    pending 单列；superseded / rejected 计入 history。
    管理文件读不懂时直接抛 ValueError，不拿不准的数报。
    """
    root = Path(root)
    user = {kind: 0 for kind in KINDS_REPORTED}
    user["other"] = 0
    user["total"] = 0
    counts = {
        "user": user,
        "persona": 0,
        "viewer": 0,
        "unclassified": 0,
        "pending": 0,
        "history": 0,
        "total_active": 0,
    }
    for rel_path, _layer in indexer.MANAGED:
        path = root / rel_path
        if not path.exists():
            continue
        _prefix, records = load_records(path)
        for record in records:
            status = record.get("status")
            if status == "pending":
                counts["pending"] += 1
                continue
            if status != "active":
                counts["history"] += 1
                continue
            subject = subjects.subject_of(record)
            counts["total_active"] += 1
            if subject == subjects.USER:
                kind = record.get("kind")
                if kind in KINDS_REPORTED:
                    user[kind] += 1
                else:
                    user["other"] += 1
                user["total"] += 1
            elif subject == subjects.PERSONA:
                counts["persona"] += 1
            elif subject == subjects.VIEWER:
                counts["viewer"] += 1
            else:
                # 未分类主体（自由文本旧 subject）：不归入任何一方。
                counts["unclassified"] += 1
    return counts


# ---------------- 写入链与基线 ----------------


def read_version_chain(root) -> list:
    """读 append-only 写入链；文件不存在当作空链；损坏直接抛错。"""
    path = Path(root) / VERSION_CHAIN_REL
    if not path.exists():
        return []
    try:
        events = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise ValueError(f"写入链损坏，无法结算：{path}：{exc}") from exc
    if not isinstance(events, list):
        raise ValueError(f"写入链损坏，无法结算：{path} 不是列表")
    return events


def count_changed_since(root, since) -> dict:
    """数写入链里 date 晚于 since 的事件数；since 为 None 时不数。

    返回 changed / total_events / undated（date 解析不了的事件数）。
    """
    events = read_version_chain(root)
    changed = None if since is None else 0
    undated = 0
    for event in events:
        if not isinstance(event, dict):
            undated += 1
            continue
        event_time = _parse_time(event.get("date"))
        if event_time is None:
            undated += 1
            continue
        if since is not None and event_time > since:
            changed += 1
    return {"changed": changed, "total_events": len(events), "undated": undated}


def read_baseline(path) -> dict | None:
    """读基线状态文件；不存在返回 None；损坏直接抛错，不猜。"""
    path = Path(path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise ValueError(f"基线文件损坏，无法结算：{path}：{exc}") from exc
    if not isinstance(data, dict) or "last_settlement_at" not in data:
        raise ValueError(f"基线文件损坏，无法结算：{path} 缺 last_settlement_at")
    return data


# ---------------- 索引状态 ----------------


def index_status(root, db_path) -> dict:
    """只读方式读索引库：行数、构建时间、新鲜度。

    用 mode=ro 打开，不建库、不改库。库不存在或无记录表时报不可用，
    不把“没有索引”说成“索引是空的”。
    """
    if db_path is None:
        return {"available": False, "reason": "未指定索引库"}
    db_path = Path(db_path)
    if not db_path.exists():
        return {"available": False, "reason": "索引不存在"}
    uri = db_path.resolve().as_uri() + "?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True)
        try:
            meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
            records = con.execute("SELECT COUNT(*) FROM records").fetchone()[0]
            docs = con.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error as exc:
        return {"available": False, "reason": f"索引库不可读：{exc}"}
    built_at = meta.get("built_at")
    stored_fp = meta.get("source_fingerprint")
    stored_hash = meta.get("source_content_hash")
    fresh = False
    if stored_fp is not None and stored_fp == indexer.source_fingerprint(root):
        fresh = True
    elif stored_hash is not None and stored_hash == indexer.source_content_hash(root):
        # 只 touch、不改内容：指纹变了、内容没变，不算过期。
        fresh = True
    return {
        "available": True,
        "records": records,
        "docs": docs,
        "built_at": built_at,
        "fresh": fresh,
    }


def rebuild_status(service=None, root=None, db_path=None) -> str:
    """后台重建最近一次成功还是失败。

    服务对象在场（同进程）时用它的真实失败记录；否则用索引新鲜度推断，
    措辞上不把推断说成实测。
    """
    if service is not None:
        error = getattr(service, "_rebuild_error", None)
        if error is not None:
            return f"失败：{error}"[:200]
        rebuilt_at = getattr(service, "rebuilt_at", None)
        if rebuilt_at:
            return f"成功（构建于 {rebuilt_at}）"
        return "本进程内尚未重建"
    status = index_status(root, db_path)
    if not status["available"]:
        return f"未知（{status['reason']}）"
    if status["fresh"]:
        built = status["built_at"] or "未知时间"
        return f"成功（索引新鲜，构建于 {built}）"
    return "待重建（索引过期，源已变化）"


def last_write_time(root) -> str | None:
    """生产库最后写入时间：各管理文件与写入链中最新的 mtime。"""
    root = Path(root)
    candidates = [root / rel_path for rel_path, _ in indexer.MANAGED]
    candidates.append(root / VERSION_CHAIN_REL)
    latest = None
    for path in candidates:
        if path.exists():
            mtime = path.stat().st_mtime
            if latest is None or mtime > latest:
                latest = mtime
    if latest is None:
        return None
    return datetime.fromtimestamp(latest, tz=timezone.utc).isoformat(timespec="seconds")


# ---------------- 报告 ----------------


def build_report(
    root, db_path=None, now=None, service=None, baseline=None, log_path=None, votes_path=None
) -> dict:
    """产出本次结算的结构化报告与文本；不写任何文件。

    votes_path：Hit 投票文件路径（可选）。给出的文件不存在时命中一节报
    “未启用”；文件存在但损坏时直接抛错，结算失败，不报不准的数。
    """
    root = Path(root)
    now = now or datetime.now().astimezone()
    counts = collect_counts(root)
    since = None
    since_text = None
    if baseline is not None:
        since = _parse_time(baseline.get("last_settlement_at"))
        since_text = baseline.get("last_settlement_at")
    delta = count_changed_since(root, since)
    idx = index_status(root, db_path)
    hits = None
    if votes_path is not None:
        votes_file = Path(votes_path)
        if votes_file.exists():
            hits = hit_votes.HitVotes(votes_file, strict=True).summary(date=now.date().isoformat())
    report = {
        "settlement_date": now.date().isoformat(),
        "generated_at": now.isoformat(timespec="seconds"),
        "root": str(root),
        "db_path": str(db_path) if db_path is not None else None,
        "counts": counts,
        "desktop": desktop_page.desktop_status_text(root),
        "changed": delta["changed"],
        "changed_since": since_text,
        "total_events": delta["total_events"],
        "undated_events": delta["undated"],
        "hits": hits,
        "log_path": str(log_path) if log_path is not None else None,
        "rebuild": rebuild_status(service=service, root=root, db_path=db_path),
        "index_records": idx.get("records"),
        "index_docs": idx.get("docs"),
        "index_available": idx["available"],
        "index_reason": idx.get("reason"),
        "last_write": last_write_time(root),
    }
    report["text"] = render_report(report)
    return report


def render_report(report: dict) -> str:
    """把结构化报告渲染成给人阅读的文本；顺序固定，便于 diff。"""
    counts = report["counts"]
    user = counts["user"]
    lines = [
        f"夜间结算 {report['settlement_date']}",
        f"数据目录：{report['root']}",
        f"索引库：{report['db_path'] or '未指定'}",
        "",
        "各层计数（active 记录）：",
        f"- user：共 {user['total']} 条",
    ]
    for kind in KINDS_REPORTED:
        lines.append(f"  - {kind}：{user[kind]}")
    lines.append(f"  - 其他：{user['other']}")
    lines.append(f"- persona：共 {counts['persona']} 条（仅总数）")
    lines.append(f"- viewer：共 {counts['viewer']} 条（仅总数）")
    if counts["unclassified"]:
        lines.append(f"- 未分类主体：共 {counts['unclassified']} 条（仅总数）")
    lines.append(f"- 待审（inbox）：{counts['pending']} 条")
    if counts["history"]:
        lines.append(f"- 历史（superseded/rejected）：{counts['history']} 条")
    lines.append(f"- 合计 active：{counts['total_active']} 条")
    lines.append("")
    lines.append(f"桌面：{report['desktop']}")
    lines.append("")
    if report["changed"] is None:
        lines.append("今晚动了：首次结算、无上次基线（已建立基线，下次起报数）")
    else:
        lines.append(
            f"今晚动了：{report['changed']} 条（自 {report['changed_since']} 起写入链新增事件）"
        )
    if report["undated_events"]:
        lines.append(f"（另有 {report['undated_events']} 个事件无有效日期，未计入）")
    hits = report.get("hits")
    if hits is None:
        lines.append("命中投票：未启用（无投票记录文件）")
    else:
        lines.append("命中投票（今日 / 累计，分主体；只报数，不报内容）：")
        for subject in ("user", "persona", "viewer"):
            today = hits["today"].get(subject, {"auto": 0, "manual": 0})
            total = hits["total"].get(subject, {"auto": 0, "manual": 0})
            lines.append(
                f"- {subject}：今日 {today['auto'] + today['manual']} 次"
                f"（自动 {today['auto']} / 手动 {today['manual']}），"
                f"累计 {total['auto'] + total['manual']} 次"
                f"（自动 {total['auto']} / 手动 {total['manual']}）"
            )
        if hits["top_kinds"]:
            top = "、".join(
                f"{row['subject']} {row['kind']} ×{row['hits']}" for row in hits["top_kinds"]
            )
            lines.append(f"- 命中最多：{top}（仅主体 + 类型 + 次数）")
    if report["log_path"]:
        lines.append(f"结算日志：{report['log_path']}")
    lines.append("")
    lines.append(f"后台重建：{report['rebuild']}")
    if report["index_available"]:
        index_line = f"索引行数：{report['index_records']} 条记录 / {report['index_docs']} 篇文档；"
    else:
        index_line = f"索引行数：不可用（{report['index_reason']}）；"
    last_write = report["last_write"] or "无写入记录"
    lines.append(f"{index_line}生产库最后写入：{last_write}")
    return "\n".join(lines) + "\n"


def run_settlement(
    root, db_path=None, out_dir=None, now=None, service=None, date_override=None, votes_path=None
) -> dict:
    """跑一次结算：写当日日志 + 更新基线，返回报告。

    日志与基线默认写在 <root>/system/nightly-settlement/ 下——
    该目录不在索引指纹范围里，结算本身不会让索引变旧。
    """
    root = Path(root)
    now = now or datetime.now().astimezone()
    out_dir = Path(out_dir) if out_dir is not None else (root / "system" / SETTLEMENT_DIRNAME)
    out_dir.mkdir(parents=True, exist_ok=True)
    date_text = date_override or now.date().isoformat()
    log_path = out_dir / f"{LOG_PREFIX}{date_text}.txt"
    baseline_path = out_dir / BASELINE_FILENAME
    old_baseline = read_baseline(baseline_path)
    report = build_report(
        root,
        db_path=db_path,
        now=now,
        service=service,
        baseline=old_baseline,
        log_path=log_path,
        votes_path=votes_path,
    )
    report["log_path"] = str(log_path)
    report["baseline_path"] = str(baseline_path)
    _atomic_write_text(log_path, report["text"])
    new_baseline = {
        "last_settlement_at": now.isoformat(timespec="seconds"),
        "event_count": report["total_events"],
        "settlement_date": date_text,
    }
    _atomic_write_text(baseline_path, json.dumps(new_baseline, ensure_ascii=False, indent=2) + "\n")
    return report


# ---------------- CLI ----------------


def main(argv=None) -> int:
    """无头 CLI：对指定数据目录跑一次结算，报告打到 stdout，同时写日志文件。"""
    parser = argparse.ArgumentParser(description="夜间结算报数：只读统计记忆库，报四个数。")
    parser.add_argument("--root", required=True, help="记忆数据根目录")
    parser.add_argument("--db", default=None, help="索引库路径（可选；不给则不查索引）")
    parser.add_argument(
        "--out-dir", default=None, help="日志与基线目录（默认 <root>/system/nightly-settlement）"
    )
    parser.add_argument(
        "--date", default=None, help="结算日期 YYYY-MM-DD（默认今天；只影响日志文件名）"
    )
    parser.add_argument(
        "--votes",
        default=None,
        help="Hit 投票文件路径（可选；不给则命中一节报“未启用”）",
    )
    args = parser.parse_args(argv)
    root = Path(args.root)
    if not root.is_dir():
        print(f"settlement: 数据目录不存在：{root}", file=sys.stderr)
        return 2
    try:
        report = run_settlement(
            root,
            db_path=args.db,
            out_dir=args.out_dir,
            date_override=args.date,
            votes_path=args.votes,
        )
    except (ValueError, OSError) as exc:
        print(f"settlement: 结算失败：{exc}", file=sys.stderr)
        return 1
    print(report["text"], end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
