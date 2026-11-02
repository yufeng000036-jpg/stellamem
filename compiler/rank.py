"""时效排序（遗忘曲线 v1 · 只影响排序，不删不改数据）。

设计来源：Ido1um/DeepSeek-memory-Ido1um 的二维遗忘模型（MIT），
按宇枫要求只取第一步：**只在排序里加时效衰减，不动数据结构**。

核心公式：
    R = 0.5 ^ (未提及天数 / S)
    score = 基础相关度 × R

S 按 kind 定：
    identity / user-core          -> Infinity（永不衰减）
    preference / relationship     -> 90 天
    decision / project / lesson / procedure -> 30 天
    fact                          -> 3 天
    其他 / 未知                    -> 30 天

"未提及天数"的近似：用记录自带的 updated_at / ts，没有就用 created_at。
不写回任何字段，不增表，不迁移。

回退方法：删除本文件 + 移除调用点即可，无残留。
"""

from __future__ import annotations

import math
import time
from typing import Any, Iterable

# ---------------------------------------------------------------- 配置

DAY_MS = 86_400_000
SECOND_MS = 1_000

# kind -> 稳定度 S（天）；None 表示永不衰减
STABILITY_BY_KIND: dict[str, float | None] = {
    "identity": None,
    "user-core": None,
    "core": None,
    # 偏好与关系：久一点，别让"喜欢什么"三两天就沉底
    "preference": 90.0,
    "relationship": 90.0,
    # 项目/决策/教训：中长周期
    "decision": 30.0,
    "project": 30.0,
    "lesson": 30.0,
    "procedure": 30.0,
    # 事实类：更新快
    "fact": 3.0,
}

DEFAULT_STABILITY_DAYS = 30.0

# 衰减强度：1.0 = 按公式原样；<1 更温和；0 = 关闭
DECAY_STRENGTH = 1.0


# ---------------------------------------------------------------- 工具


def _kind_of(record: dict[str, Any]) -> str:
    """从记录里取 kind；兼容 claim 结构与已渲染的 markdown 记录。"""
    for key in ("kind", "claim_kind", "type"):
        v = record.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip().lower()
    # 兜底：从 claim_id / claim 文本里猜不到就返回空
    return ""


def stability_days(kind: str) -> float | None:
    """返回稳定度（天）；None = 永不衰减。"""
    k = (kind or "").strip().lower()
    if k in STABILITY_BY_KIND:
        return STABILITY_BY_KIND[k]
    return DEFAULT_STABILITY_DAYS


def _timestamp_ms(record: dict[str, Any]) -> int | None:
    """取"最后提及时间"的近似值（毫秒）。

    优先级：last_at / lastAt / hit_at > updated_at > ts > created_at > since
    支持 int/float 毫秒、秒级时间戳，以及 ISO 字符串。
    """
    for key in (
        "last_at",
        "lastAt",
        "hit_at",
        "updated_at",
        "updatedAt",
        "ts",
        "created_at",
        "createdAt",
        "since",
    ):
        v = record.get(key)
        ms = _to_ms(v)
        if ms is not None:
            return ms
    return None


def _to_ms(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        n = float(value)
        if not math.isfinite(n) or n <= 0:
            return None
        # 10 位左右是秒，13 位左右是毫秒
        return int(n * 1000) if n < 1_000_000_000_000 else int(n)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # 纯数字字符串
        try:
            return _to_ms(float(s))
        except ValueError:
            pass
        # ISO 8601
        try:
            from datetime import datetime, timezone

            iso = s.replace("Z", "+00:00")
            dt = datetime.fromisoformat(iso)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except Exception:
            return None
    return None


def retention(record: dict[str, Any], now_ms: int | None = None) -> float:
    """保持度 R ∈ (0, 1]。永不衰减的 kind 返回 1.0。"""
    if DECAY_STRENGTH <= 0:
        return 1.0
    s_days = stability_days(_kind_of(record))
    if s_days is None:
        return 1.0
    ts = _timestamp_ms(record)
    if ts is None:
        # 没有时间信息 -> 认为"刚提过"，不惩罚
        return 1.0
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    days = max(0.0, (now - ts) / DAY_MS)
    s = max(0.0001, s_days)
    r = math.pow(0.5, (days / s) * DECAY_STRENGTH)
    return max(0.0, min(1.0, r))


def age_days(record: dict[str, Any], now_ms: int | None = None) -> float | None:
    """未提及天数（用于调试/展示）。"""
    ts = _timestamp_ms(record)
    if ts is None:
        return None
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    return round(max(0.0, (now - ts) / DAY_MS), 2)


# ---------------------------------------------------------------- 排序


def rank(
    items: Iterable[tuple[dict[str, Any], float]],
    now_ms: int | None = None,
) -> list[tuple[dict[str, Any], float, float]]:
    """按「基础相关度 × 时效系数」重排。

    参数 items: [(record, base_score), ...]
    返回:       [(record, base_score, adjusted_score), ...]，已降序排好

    注意：不修改传入的 record，也不删除任何条目。
    """
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    out: list[tuple[dict[str, Any], float, float]] = []
    for record, base in items:
        try:
            b = float(base)
        except (TypeError, ValueError):
            b = 0.0
        out.append((record, b, b * retention(record, now)))
    out.sort(key=lambda x: x[2], reverse=True)
    return out


def explain(record: dict[str, Any], now_ms: int | None = None) -> dict[str, Any]:
    """单条记忆的时效诊断，给排查用。"""
    kind = _kind_of(record)
    s = stability_days(kind)
    return {
        "kind": kind or "(unknown)",
        "stability_days": "inf" if s is None else s,
        "age_days": age_days(record, now_ms),
        "retention": round(retention(record, now_ms), 4),
        "decay_strength": DECAY_STRENGTH,
    }
