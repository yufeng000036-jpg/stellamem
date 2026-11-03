"""睡眠巩固（文本版）：把情景记忆蒸馏成抽象规律。

设计来源：港中大 / 浙大 Memo 论文（arXiv:2604.27707）的"值得做的"部分 ——
保留检索式情景记忆（海马体），加一条异步巩固通道，把情景记忆逐步整合为
抽象规律。论文原版走模型权重（LoRA / MEMIT / TTT）；Stellamem 走文本：
定期把近 N 期 episode 小结蒸馏成 pattern 记忆项。

为什么是文本版而不是权重版（用户已拍板，写死在这里）：
    1. 人格跑的是 DeepSeek API，模型权重摸不着；
    2. 本地微调要 GPU 时间 + 数据管线 + expertise，成本超预算；
    3. 权重写入不可逆 —— "撤回某一条记忆"在权重里是未解难题，
       与用户"能查能改能撤回"的要求直接冲突。
    等以后本地小模型 + LoRA 成本下来再谈权重那条路。

本模块的巩固是确定性的（无模型调用，沿用 nightly.py 的先例）：
    同一 (subject, predicate, value) 在 >= min_episodes 个不同 episode 里
    反复出现 -> pattern 候选。pattern 永远带 provenance（来源 episode 列表），
    可追溯、可删除；status=pending，需具名人类批准才生效。

recall 权重约定（文档级，不碰 rank.py 生产路径）：
    pattern 参与 recall，但得分乘以 PATTERN_WEIGHT(0.8) —— 低于原始记录。
    调用方用 pattern_candidates() 拿候选，自行决定是否注入。
"""

from __future__ import annotations

import copy
import itertools
import json
from datetime import datetime, timezone
from pathlib import Path

PATTERN_KIND = "pattern"
# 规律项权重低于原始记录：这是约定，不是调优结果。
PATTERN_WEIGHT = 0.8


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(text) -> str:
    return "".join(str(text or "").split()).lower()


def mine_patterns(episodes: list[dict], min_episodes: int = 2) -> list[dict]:
    """从 episode 小结里挖"反复出现"的规律。

    episodes: [{"episode_id": str, "date": str,
                "claims": [{"subject":..,"predicate":..,"value":..}]}]
    同一 (subject, predicate, norm(value)) 在 >= min_episodes 个不同 episode
    出现 -> 一条 pattern 候选（status=pending，provenance=来源 episode 列表）。
    """
    groups: dict[tuple, dict] = {}
    for ep in episodes or []:
        eid = str(ep.get("episode_id") or "")
        seen_in_ep: set[tuple] = set()
        for c in ep.get("claims") or []:
            key = (_norm(c.get("subject")), _norm(c.get("predicate")), _norm(c.get("value")))
            if not key[0] or not key[1] or not key[2]:
                continue
            if key in seen_in_ep:
                continue
            seen_in_ep.add(key)
            g = groups.setdefault(
                key,
                {
                    "subject": str(c.get("subject") or ""),
                    "predicate": str(c.get("predicate") or ""),
                    "value": str(c.get("value") or ""),
                    "episodes": [],
                },
            )
            if eid and eid not in g["episodes"]:
                g["episodes"].append(eid)
    out = []
    for key, g in sorted(groups.items()):
        if len(g["episodes"]) >= max(2, min_episodes):
            out.append(
                {
                    "kind": PATTERN_KIND,
                    "subject": g["subject"],
                    "predicate": g["predicate"],
                    "value": g["value"],
                    # 规律是"第 N 次观察到"，value 保持原文表述。
                    "text": "%s %s：%s（在 %d 期 episode 中反复出现）"
                    % (g["subject"], g["predicate"], g["value"], len(g["episodes"])),
                    "provenance": sorted(g["episodes"]),
                    "support": len(g["episodes"]),
                    "status": "pending",
                    "mined_at": _now_iso(),
                }
            )
    return out


def _bigrams(text: str) -> set:
    import re

    raw = (text or "").lower()
    grams: set[str] = set()
    for run in re.findall(r"[\u4e00-\u9fff]+", raw):
        if len(run) == 1:
            grams.add(run)
        for i in range(len(run) - 1):
            grams.add(run[i : i + 2])
    for word in re.findall(r"[a-z0-9]{2,}", raw):
        grams.add(word)
    return grams


def pattern_candidates(
    patterns: list[dict], prompt: str, weight: float = PATTERN_WEIGHT, min_score: float = 0.18
) -> list[dict]:
    """给 prompt 打分，返回按加权分排序的 pattern 候选。

    只收 status=active 的；得分 = bigram overlap × weight（<1，低于原始记录）。
    """
    grams = _bigrams(prompt)
    if not grams:
        return []
    out = []
    for p in patterns or []:
        if p.get("status") != "active":
            continue
        blob = "%s %s" % (p.get("subject") or "", p.get("value") or "")
        overlap = len(grams & _bigrams(blob)) / len(grams)
        score = overlap * weight
        if overlap > 0 and score >= min_score * weight:
            out.append({"pattern": p, "score": score, "overlap": overlap, "weight": weight})
    out.sort(key=lambda r: (-r["score"], str(r["pattern"].get("subject"))))
    return out


class PatternStore:
    """pattern 的内存存储 + 可选 JSON 持久化 + 只增审计。

    approve / withdraw 必须具名人类；拒绝 / 重复操作记审计。
    """

    def __init__(self, path: str | Path | None = None, audit_path: str | Path | None = None):
        self._path = Path(path) if path else None
        self._audit_path = Path(audit_path) if audit_path else None
        self._patterns: dict[str, dict] = {}
        self._counter = itertools.count(1)
        if self._path and self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._patterns.update(data)
            except (OSError, ValueError):
                pass

    def _save(self) -> None:
        if not self._path:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._patterns, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def _audit(self, action: str, pid: str, note: str = "") -> None:
        entry = {"ts": _now_iso(), "action": action, "kind": PATTERN_KIND, "id": pid, "note": note}
        if self._audit_path:
            self._audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self._audit_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def add_mined(self, mined: list[dict]) -> list[dict]:
        """把 mine_patterns 的产出入库（status=pending）。返回带 id 的副本。"""
        out = []
        for m in mined or []:
            pid = "pat-%06d" % next(self._counter)
            rec = dict(m)
            rec["id"] = pid
            rec["status"] = "pending"
            self._patterns[pid] = rec
            out.append(copy.deepcopy(rec))
            self._audit(
                "mined",
                pid,
                "support=%s episodes=%s" % (m.get("support"), len(m.get("provenance") or [])),
            )
        self._save()
        return out

    def approve(self, pid: str, by: str) -> dict:
        by = (by or "").strip()
        if not by:
            raise ValueError("approve 需要具名人类（by 非空）")
        rec = self._patterns.get(pid)
        if rec is None:
            raise KeyError("no such pattern: %r" % pid)
        if rec["status"] != "pending":
            raise ValueError("pattern %s 状态为 %s，不能批准" % (pid, rec["status"]))
        rec["status"] = "active"
        rec["approved_by"] = by
        rec["approved_at"] = _now_iso()
        self._save()
        self._audit("approved", pid, "by=%s" % by)
        return copy.deepcopy(rec)

    def withdraw(self, pid: str, by: str, reason: str = "") -> dict:
        """撤回：status=withdrawn，provenance 保留，可回查、可恢复。"""
        by = (by or "").strip()
        if not by:
            raise ValueError("withdraw 需要具名人类（by 非空）")
        rec = self._patterns.get(pid)
        if rec is None:
            raise KeyError("no such pattern: %r" % pid)
        rec["status"] = "withdrawn"
        rec["withdrawn_by"] = by
        rec["withdraw_reason"] = reason
        self._save()
        self._audit("withdrawn", pid, "by=%s reason=%s" % (by, reason))
        return copy.deepcopy(rec)

    def get(self, pid: str) -> dict | None:
        rec = self._patterns.get(pid)
        return copy.deepcopy(rec) if rec else None

    def list(self, status: str | None = None) -> list[dict]:
        rows = list(self._patterns.values())
        if status:
            rows = [r for r in rows if r["status"] == status]
        return [copy.deepcopy(r) for r in rows]
