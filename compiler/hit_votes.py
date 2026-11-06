"""Hit 投票：召回真用上才算命中；投票只影响已获准召回内的排序。

设计来源：外部记忆系统 v17 定稿的“hit 投票”（项目名/仓库链接待补，
用户 2026-11-05 发来的截图）；落到 Stellamem 的三处改动见
``docs/HIT-VOTES.md`` 的借鉴致谢节。

规则（铁律）：
- 自动命中：只有实际进入注入文本的记录才记一次（``search.recall()``
  在 budget 裁剪之后记；被 budget 截掉的、没注入的不记）。
- 手动投票只做补充：只能投赞成（+1），没有反对票、没有下沉票；
  手动票按半权计入排序加分，永远投不沉一条好记忆。
- 按主体分开计票：key 里带标准主体命名空间；persona 的命中绝不
  抬高 user 的记录，反之亦然。未分类主体（自由文本旧 subject）
  不计票、不加分，失败关闭。
- 投票只排序、不删不沉：加分只作用于已经通过 min_score、进入
  top-k 的记录——没进候选池的记录投了票也不会被召回；投票不绕过
  主体隔离、确认门、预算门、注入门。
- 只增信号，不碰内容：本模块没有任何删除 / 降权 / 自动清理路径，
  不提供 delete / clear / reset。沉没与否的解读权在人，不在代码。
- 命中数是参考信号，不是质量判决：排序加分封顶 0.10，盖不过
  相关度本身。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

try:  # 包内与扁平导入都要能用（测试只把 compiler/ 加进 sys.path）
    from . import subjects as subjects_mod
    from .file_lock import atomic_write_text
except ImportError:  # pragma: no cover - flat layout
    import subjects as subjects_mod  # type: ignore
    from file_lock import atomic_write_text  # type: ignore

# 排序加分：每 1 个有效命中 +0.02，封顶 0.10；手动票半权。
BOOST_PER_HIT = 0.02
BOOST_CAP = 0.10
MANUAL_WEIGHT = 0.5

STORE_VERSION = 1
DEFAULT_FILENAME = "votes.json"


def _today_iso() -> str:
    return datetime.now().astimezone().date().isoformat()


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _key(subject: str, record_id: str) -> str:
    """投票 key：主体命名空间 + 记录 id，写死进 key，串不了主体。"""
    return f"record:{subjects_mod.validate_subject(subject)}:{record_id}"


def validate_vote(subject, record_subject) -> bool:
    """手动投票的目标校验：投票主体与记录主体必须是同一标准命名空间。

    两边都先 canonical（旧拼写“用户”与“user”是同一命名空间）；
    任一边不是合法主体（未分类自由文本）都返回 False，失败关闭。
    调用方（MCP / HTTP）先查库拿到记录主体，再调本函数。
    """
    if not isinstance(subject, str) or not isinstance(record_subject, str):
        return False
    subj = subjects_mod.canonical_subject(subject.strip())
    rec_subj = subjects_mod.canonical_subject(record_subject.strip())
    if subj not in subjects_mod.SUBJECTS or rec_subj not in subjects_mod.SUBJECTS:
        return False
    return subj == rec_subj


class HitVotes:
    """命中投票计数器：JSON 可选持久化，内存默认。

    path 给出时做 write-through 持久化；不给则纯内存。
    文件结构：{"version": 1,
      "entries": {"record:<subject>:<id>": {"hits": int, "manual": int,
        "kind": str, "first_hit": iso, "last_hit": iso}},
      "days": {"YYYY-MM-DD": {"user": {"auto": n, "manual": n}, ...}}}
    days 只用于结算“今日命中”：每天一个小计数，不记事件明细。
    """

    def __init__(self, path: str | Path | None = None, strict: bool = False):
        self._path = Path(path) if path else None
        self._strict = strict
        self._entries: dict[str, dict] = {}
        self._days: dict[str, dict] = {}
        if self._path and self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
            except (ValueError, OSError) as exc:
                if strict:
                    raise ValueError(f"投票文件损坏，无法读取：{self._path}：{exc}") from exc
                print(f"hit-votes: 投票文件损坏，从空开始：{self._path}", file=sys.stderr)
                return
            if not isinstance(data, dict):
                if strict:
                    raise ValueError(f"投票文件损坏，无法读取：{self._path} 不是对象")
                print(f"hit-votes: 投票文件损坏，从空开始：{self._path}", file=sys.stderr)
                return
            entries = data.get("entries", {})
            if isinstance(entries, dict):
                self._entries = {
                    str(k): v
                    for k, v in entries.items()
                    if isinstance(v, dict)
                    and isinstance(v.get("hits"), int)
                    and isinstance(v.get("manual"), int)
                }
            days = data.get("days", {})
            if isinstance(days, dict):
                self._days = {str(k): v for k, v in days.items() if isinstance(v, dict)}

    # ---------------- 记票 ----------------

    def record_hit(self, subject: str, record_id: str, kind: str = "") -> dict:
        """自动命中：记录一次实际注入。返回该条目的最新计数快照。"""
        key = _key(subject, str(record_id))
        now = _now_iso()
        entry = self._entries.get(key)
        if entry is None:
            entry = {
                "hits": 0,
                "manual": 0,
                "kind": str(kind or ""),
                "first_hit": now,
                "last_hit": now,
            }
            self._entries[key] = entry
        entry["hits"] += 1
        entry["last_hit"] = now
        if kind and not entry.get("kind"):
            entry["kind"] = str(kind)
        self._bump_day(subjects_mod.canonical_subject(subject), "auto")
        self._save()
        return self._snapshot(key, entry)

    def vote(self, subject: str, record_id: str, kind: str = "") -> dict:
        """手动投票：只加赞成票，没有反对票。返回该条目的最新计数快照。"""
        key = _key(subject, str(record_id))
        now = _now_iso()
        entry = self._entries.get(key)
        if entry is None:
            entry = {
                "hits": 0,
                "manual": 0,
                "kind": str(kind or ""),
                "first_hit": now,
                "last_hit": now,
            }
            self._entries[key] = entry
        entry["manual"] += 1
        entry["last_hit"] = now
        if kind and not entry.get("kind"):
            entry["kind"] = str(kind)
        self._bump_day(subjects_mod.canonical_subject(subject), "manual")
        self._save()
        return self._snapshot(key, entry)

    def _bump_day(self, subject: str, field: str) -> None:
        day = self._days.setdefault(_today_iso(), {})
        counters = day.setdefault(subject, {"auto": 0, "manual": 0})
        counters[field] = int(counters.get(field, 0)) + 1

    # ---------------- 读 ----------------

    def get(self, subject: str, record_id: str) -> dict | None:
        try:
            key = _key(subject, str(record_id))
        except ValueError:
            return None
        entry = self._entries.get(key)
        return self._snapshot(key, entry) if entry else None

    def boost(self, subject: str, record_id: str) -> float:
        """排序加分：有效命中 = 自动 + 0.5 × 手动，0.02 一个，封顶 0.10。

        主体非法或记录无票时返回 0.0，不抛错——加分是增强项，
        任何异常都不能影响召回本身。
        """
        try:
            entry = self._entries.get(_key(subject, str(record_id)))
        except ValueError:
            return 0.0
        if not entry:
            return 0.0
        effective = entry.get("hits", 0) + MANUAL_WEIGHT * entry.get("manual", 0)
        return round(min(BOOST_CAP, BOOST_PER_HIT * effective), 4)

    def report(self, limit: int = 50) -> list[dict]:
        """只读看板：按（自动 + 手动）倒序。不含任何记录正文。"""
        rows = [self._snapshot(k, v) for k, v in self._entries.items()]
        rows.sort(key=lambda r: (-(r["hits"] + r["manual"]), r["item_id"]))
        return rows[: max(0, limit)]

    def daily_summary(self, date: str | None = None) -> dict:
        """某日按主体分的命中数：{"user": {"auto": n, "manual": n}, ...}。"""
        day = self._days.get(date or _today_iso(), {})
        out = {}
        for subject in subjects_mod.SUBJECTS:
            counters = day.get(subject, {})
            out[subject] = {
                "auto": int(counters.get("auto", 0)),
                "manual": int(counters.get("manual", 0)),
            }
        return out

    def summary(self, date: str | None = None, top_kinds: int = 5) -> dict:
        """结算用汇总：今日 + 累计（分主体）+ 命中最多的主体 × 类型。

        top_kinds 只含主体 + 类型 + 次数，不含记录 id 与正文。
        """
        today = self.daily_summary(date)
        total: dict[str, dict] = {s: {"auto": 0, "manual": 0} for s in subjects_mod.SUBJECTS}
        by_kind: dict[tuple[str, str], int] = {}
        for key, entry in self._entries.items():
            subject = key.split(":", 2)[1] if key.count(":") >= 2 else ""
            if subject not in total:
                continue
            total[subject]["auto"] += int(entry.get("hits", 0))
            total[subject]["manual"] += int(entry.get("manual", 0))
            kind = str(entry.get("kind") or "其他")
            by_kind[(subject, kind)] = (
                by_kind.get((subject, kind), 0)
                + int(entry.get("hits", 0))
                + int(entry.get("manual", 0))
            )
        top = [
            {"subject": s, "kind": k, "hits": n}
            for (s, k), n in sorted(by_kind.items(), key=lambda z: (-z[1], z[0]))[
                : max(0, top_kinds)
            ]
        ]
        return {"today": today, "total": total, "top_kinds": top}

    # ---------------- 内部 ----------------

    def _snapshot(self, key: str, entry: dict) -> dict:
        parts = key.split(":", 2)
        return {
            "item_id": key,
            "subject": parts[1] if len(parts) > 1 else "",
            "record_id": parts[2] if len(parts) > 2 else "",
            "kind": entry.get("kind", ""),
            "hits": int(entry.get("hits", 0)),
            "manual": int(entry.get("manual", 0)),
            "first_hit": entry.get("first_hit"),
            "last_hit": entry.get("last_hit"),
        }

    def _save(self) -> None:
        if not self._path:
            return
        payload = {
            "version": STORE_VERSION,
            "entries": self._entries,
            "days": self._days,
        }
        atomic_write_text(self._path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
