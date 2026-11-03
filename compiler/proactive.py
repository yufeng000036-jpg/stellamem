"""SenseNova 三件套的本地、可审计、可关闭实现。

设计来源：商汤 SenseNova 6.8 Flash 的行为启发（主动提醒 / 跨会话任务接续 /
冲突消解），无代码可抄、不采用其 API。原版是云端黑盒 API —— 调用会把私人
记忆送给第三方且花钱；本模块是本地实现，每项可单独关闭，全程审计。

与原版的对比（"好在哪"，详见 docs/BATCH2-BEYOND.md）：
    - 原版：云端黑盒，记忆出本地，不可审计，不可关闭。
    - 我们：本地运行；每条提醒 / 冲突都是 pending 草稿，需具名人类确认；
      三个开关可独立关闭；全部写审计日志。

铁律（写死在代码里）：
    1. 不自动打扰：本模块没有任何推送 / 发送 / 自动执行路径。
       propose_* 只产出 pending 草稿；confirm_* 需要具名人类。
    2. 不自动覆盖：冲突裁决只记录人的决定，不自己改记忆；
       调用方在自己的批准流程里执行。
    3. 可关闭：ProactiveCenter(enabled=False) 或单个开关关闭时，
       对应 propose_* 返回空列表并注明 disabled。
"""

from __future__ import annotations

import copy
import itertools
import json
from datetime import datetime, timezone
from pathlib import Path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(text) -> str:
    return "".join(str(text or "").split()).lower()


class ProactiveDisabled(RuntimeError):
    """对应功能已关闭。"""


class ProactiveCenter:
    """三件套的统一入口：提醒 / 接续 / 冲突，各自独立开关。"""

    def __init__(
        self,
        store_dir: str | Path | None = None,
        enabled: bool = True,
        reminders_on: bool = True,
        briefing_on: bool = True,
        conflicts_on: bool = True,
    ):
        self.enabled = bool(enabled)
        self.reminders_on = bool(reminders_on)
        self.briefing_on = bool(briefing_on)
        self.conflicts_on = bool(conflicts_on)
        self._dir = Path(store_dir) if store_dir else None
        if self._dir:
            self._dir.mkdir(parents=True, exist_ok=True)
        self._reminders: dict[str, dict] = {}
        self._conflicts: dict[str, dict] = {}
        self._counter = itertools.count(1)
        self._audit_path = self._dir / "proactive-audit.jsonl" if self._dir else None
        self._load()

    # ---------- 持久化 ----------
    def _load(self) -> None:
        if not self._dir:
            return
        for name, slot in (
            ("reminders.json", self._reminders),
            ("conflicts.json", self._conflicts),
        ):
            p = self._dir / name
            if p.exists():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        slot.update(data)
                except (OSError, ValueError):
                    pass

    def _save(self) -> None:
        if not self._dir:
            return
        for name, slot in (
            ("reminders.json", self._reminders),
            ("conflicts.json", self._conflicts),
        ):
            p = self._dir / name
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(slot, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(p)

    def _audit(self, action: str, detail: dict) -> None:
        entry = {"ts": _now_iso(), "action": action, "detail": detail}
        if self._audit_path:
            with self._audit_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _new_id(self, prefix: str) -> str:
        return "%s-%06d" % (prefix, next(self._counter))

    def _check_on(self, flag: bool, name: str) -> None:
        if not self.enabled or not flag:
            raise ProactiveDisabled(
                "%s 已关闭（enabled=%s）。本模块不做任何事，不打扰。" % (name, self.enabled)
            )

    # ---------- ① 主动提醒 ----------
    def propose_reminders(self, new_claims: list[dict], existing: list[dict]) -> list[dict]:
        """输入新记忆 + 旧记忆，产出 pending 提醒草稿。

        两种触发（都是启发式，宁可漏报不误报）：
        - 矛盾：同一 (subject, predicate) 出现不同值 -> 提醒复核。
        - 过期：旧记忆 valid_to 已过但 status 仍为 active -> 提醒复核。
        返回的每条 status 都是 "pending"，需 confirm_reminder 具名确认。
        """
        self._check_on(self.reminders_on, "主动提醒")
        out = []
        for new in new_claims or []:
            nkey = (_norm(new.get("subject")), _norm(new.get("predicate")))
            nval = _norm(new.get("value"))
            for old in existing or []:
                if _norm(old.get("subject")) != nkey[0]:
                    continue
                if _norm(old.get("predicate")) != nkey[1]:
                    continue
                if old.get("status") not in (None, "active"):
                    continue
                if _norm(old.get("value")) and _norm(old.get("value")) != nval:
                    rid = self._new_id("rem")
                    rec = {
                        "id": rid,
                        "type": "contradiction",
                        "status": "pending",
                        "summary": "新记忆与旧记忆矛盾：%s / %s"
                        % (new.get("subject"), new.get("predicate")),
                        "new_ref": str(new.get("claim_id") or new.get("id") or ""),
                        "old_ref": str(old.get("claim_id") or old.get("id") or ""),
                        "new_value": str(new.get("value") or ""),
                        "old_value": str(old.get("value") or ""),
                        "created_at": _now_iso(),
                    }
                    self._reminders[rid] = rec
                    out.append(copy.deepcopy(rec))
        self._save()
        self._audit(
            "propose_reminders", {"proposed": len(out), "new_claims": len(new_claims or [])}
        )
        return out

    def check_expiry(self, memories: list[dict], now_iso: str | None = None) -> list[dict]:
        """valid_to 已过但仍为 active 的记忆 -> pending 提醒。"""
        self._check_on(self.reminders_on, "主动提醒")
        now = now_iso or _now_iso()
        out = []
        for m in memories or []:
            vt = m.get("valid_to")
            if not vt or m.get("status") != "active":
                continue
            if str(vt) < now:
                rid = self._new_id("rem")
                rec = {
                    "id": rid,
                    "type": "expired",
                    "status": "pending",
                    "summary": "记忆已过有效期但仍为 active，建议复核：%s"
                    % (m.get("subject") or m.get("claim_id") or ""),
                    "ref": str(m.get("claim_id") or m.get("id") or ""),
                    "valid_to": str(vt),
                    "created_at": _now_iso(),
                }
                self._reminders[rid] = rec
                out.append(copy.deepcopy(rec))
        self._save()
        self._audit("check_expiry", {"proposed": len(out)})
        return out

    def confirm_reminder(self, rid: str, by: str) -> dict:
        """具名人类确认提醒 -> confirmed（可推送）。匿名拒绝。"""
        self._check_on(self.reminders_on, "主动提醒")
        by = (by or "").strip()
        if not by:
            raise ValueError("confirm_reminder 需要具名人类（by 非空）")
        rec = self._reminders.get(rid)
        if rec is None:
            raise KeyError("no such reminder: %r" % rid)
        if rec["status"] != "pending":
            raise ValueError("reminder %s 状态为 %s，不能确认" % (rid, rec["status"]))
        rec["status"] = "confirmed"
        rec["confirmed_by"] = by
        rec["confirmed_at"] = _now_iso()
        self._save()
        self._audit("confirm_reminder", {"id": rid, "by": by})
        return copy.deepcopy(rec)

    def dismiss_reminder(self, rid: str, by: str, reason: str = "") -> dict:
        self._check_on(self.reminders_on, "主动提醒")
        by = (by or "").strip()
        if not by:
            raise ValueError("dismiss_reminder 需要具名人类（by 非空）")
        rec = self._reminders.get(rid)
        if rec is None:
            raise KeyError("no such reminder: %r" % rid)
        rec["status"] = "dismissed"
        rec["dismissed_by"] = by
        rec["dismiss_reason"] = reason
        self._save()
        self._audit("dismiss_reminder", {"id": rid, "by": by})
        return copy.deepcopy(rec)

    def list_reminders(self, status: str | None = None) -> list[dict]:
        rows = list(self._reminders.values())
        if status:
            rows = [r for r in rows if r["status"] == status]
        return [copy.deepcopy(r) for r in rows]

    # ---------- ② 跨会话任务接续 ----------
    def session_briefing(self, open_items: list[dict], last_summary: dict | None) -> dict:
        """纯函数：根据待办 + 上次小结，拼出"上次进展"简报。

        不读库、不写库、无副作用；调用方决定展示与否。
        """
        self._check_on(self.briefing_on, "跨会话任务接续")
        items = sorted(open_items or [], key=lambda i: str(i.get("updated_at") or ""), reverse=True)
        lines = []
        if last_summary:
            done = last_summary.get("done") or []
            nxt = last_summary.get("next") or []
            lines.append("上次（%s）：" % (last_summary.get("session_id") or "未知会话"))
            for d in done[:5]:
                lines.append("  已完成：%s" % d)
            for n in nxt[:5]:
                lines.append("  待继续：%s" % n)
        if items:
            lines.append("当前待办（%d 项，最近更新在前）：" % len(items))
            for it in items[:8]:
                lines.append(
                    "  - %s [%s]" % (it.get("title") or it.get("id"), it.get("status") or "open")
                )
        else:
            lines.append("当前没有待办。")
        briefing = {
            "generated_at": _now_iso(),
            "open_count": len(items),
            "text": "\n".join(lines),
        }
        self._audit(
            "session_briefing", {"open_count": len(items), "had_last_summary": bool(last_summary)}
        )
        return briefing

    # ---------- ③ 冲突消解 ----------
    def detect_conflicts(self, new_claim: dict, existing: list[dict]) -> list[dict]:
        """新旧记忆打架 -> 冲突报告（双方证据并列），status=pending。

        只报告、不裁决，裁决必须走 adjudicate（具名人类）。
        """
        self._check_on(self.conflicts_on, "冲突消解")
        out = []
        nkey = (_norm(new_claim.get("subject")), _norm(new_claim.get("predicate")))
        nval = _norm(new_claim.get("value"))
        for old in existing or []:
            if _norm(old.get("subject")) != nkey[0]:
                continue
            if _norm(old.get("predicate")) != nkey[1]:
                continue
            if old.get("status") not in (None, "active"):
                continue
            oval = _norm(old.get("value"))
            if oval and oval != nval:
                cid = self._new_id("con")
                rec = {
                    "id": cid,
                    "status": "pending",
                    "subject": str(new_claim.get("subject") or ""),
                    "predicate": str(new_claim.get("predicate") or ""),
                    "new": {
                        "ref": str(new_claim.get("claim_id") or new_claim.get("id") or ""),
                        "value": str(new_claim.get("value") or ""),
                        "evidence": str(new_claim.get("evidence") or ""),
                    },
                    "old": {
                        "ref": str(old.get("claim_id") or old.get("id") or ""),
                        "value": str(old.get("value") or ""),
                        "evidence": str(old.get("evidence") or ""),
                    },
                    "created_at": _now_iso(),
                }
                self._conflicts[cid] = rec
                out.append(copy.deepcopy(rec))
        self._save()
        self._audit("detect_conflicts", {"found": len(out)})
        return out

    def adjudicate(self, cid: str, decision: str, by: str) -> dict:
        """具名人类裁决。decision: keep_new | keep_old。

        只记录决定，不自己改记忆：调用方在自己的批准流程里执行
        （如给输家的 valid_to 打时间戳或走 quarantine）。返回裁决记录，
        内含 suggested_action 供调用方参考。
        """
        self._check_on(self.conflicts_on, "冲突消解")
        by = (by or "").strip()
        if not by:
            raise ValueError("adjudicate 需要具名人类（by 非空）")
        if decision not in ("keep_new", "keep_old"):
            raise ValueError("decision 只能是 keep_new / keep_old")
        rec = self._conflicts.get(cid)
        if rec is None:
            raise KeyError("no such conflict: %r" % cid)
        if rec["status"] != "pending":
            raise ValueError("conflict %s 状态为 %s，不能裁决" % (cid, rec["status"]))
        rec["status"] = "adjudicated"
        rec["decision"] = decision
        rec["adjudicated_by"] = by
        rec["adjudicated_at"] = _now_iso()
        loser = rec["old"] if decision == "keep_new" else rec["new"]
        rec["suggested_action"] = (
            "将输家（ref=%s）标记 valid_to=now 或走 quarantine；"
            "本模块不执行，由调用方在批准流程里做。" % (loser.get("ref") or "?")
        )
        self._save()
        self._audit("adjudicate", {"id": cid, "decision": decision, "by": by})
        return copy.deepcopy(rec)

    def list_conflicts(self, status: str | None = None) -> list[dict]:
        rows = list(self._conflicts.values())
        if status:
            rows = [r for r in rows if r["status"] == status]
        return [copy.deepcopy(r) for r in rows]
