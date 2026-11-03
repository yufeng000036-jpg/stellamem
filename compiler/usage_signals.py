"""使用频率回流为质量信号（轻量版"压力测试"）。

设计来源：Ant 可读记忆的"摘要质量要用未来真实选择做压力测试" ——
轻量实现：只记录每条偏好卡 / 摘要 / 规律项被 recall 命中的次数与时间，
输出到只读看板。不删除、不修改内容、不做自动判定。

铁律：
    - 只增信号，不碰内容：本模块没有任何删除 / 降权 / 自动清理路径。
    - "被想起的频率"是参考信号，不是质量判决 —— 解读权在人。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class UsageTracker:
    """命中计数器：JSON 可选持久化，内存默认。

    path 给出时做 write-through 持久化（JSON 对象文件）；
    不给则纯内存。report() 只读，按命中次数倒序。
    """

    def __init__(self, path: str | Path | None = None):
        self._path = Path(path) if path else None
        self._hits: dict[str, dict] = {}
        if self._path and self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._hits = {
                        k: v
                        for k, v in data.items()
                        if isinstance(v, dict) and isinstance(v.get("hits"), int)
                    }
            except (OSError, ValueError):
                self._hits = {}

    def record_hit(self, item_id: str, kind: str = "") -> dict:
        """记录一次命中。返回该条目的最新计数快照。"""
        item_id = str(item_id)
        now = _now_iso()
        entry = self._hits.get(item_id)
        if entry is None:
            entry = {"hits": 0, "kind": str(kind or ""), "first_hit": now, "last_hit": now}
            self._hits[item_id] = entry
        entry["hits"] += 1
        entry["last_hit"] = now
        if kind and not entry.get("kind"):
            entry["kind"] = str(kind)
        self._save()
        return dict(entry)

    def get(self, item_id: str) -> dict | None:
        entry = self._hits.get(str(item_id))
        return dict(entry) if entry else None

    def report(self, limit: int = 50) -> list[dict]:
        """只读看板：按命中次数倒序。不做任何写入或判定。"""
        rows = [{"item_id": k, **v} for k, v in self._hits.items()]
        rows.sort(key=lambda r: (-r["hits"], r["item_id"]))
        return rows[: max(0, limit)]

    def _save(self) -> None:
        if not self._path:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._hits, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)
