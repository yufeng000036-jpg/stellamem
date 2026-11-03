"""preference_summary: 一等记忆类型 —— 可读偏好摘要。

设计来源：Ant International《Text as a universal interface for
transferable personalisation》(EMNLP 2026) 的"可读记忆"主张 ——
记忆应该是纯文本、可读、可审计、可改；跨系统传递时只传提炼后的摘要，
不搬原始行为数据。Stellamem 本就走这条路线，本模块把"偏好摘要"
做成一等记忆类型（kind），给常客/用户画像用。

铁律（用户 2026-11-03 亲定，写进校验规则）：
    偏好卡里的每条陈述必须有证据支撑，禁止想当然式推断。
    证据缺失 = 拒绝写入 + 记审计，不存在"先写上以后再补"。

真实反例（教训，写进文档与评审清单）：
    - "送了 235 个小心心" → 不能写成"喜欢小心心特效"。
      小心心是最便宜的礼物，送小心心是省钱的支持方式，不是偏好特效。
    - "晚上来" → 不能写成"偏好晚上"。
      直播只在晚上开，晚上来是必然的，不等于偏好晚上。

本模块强制的是结构：evidence 非空。证据是否真正支撑陈述
（忠实度）是人工评审的判断 —— 模块管"有没有"，人管"对不对"，
与桥接"AI 起草、人拍板"的治理哲学一致。

本模块不做：
    - 不做语义忠实度自动判定（不可靠，不硬上）；
    - 不接 recall/注入（后续可做，见 docs/READABLE-MEMORY.md）；
    - 不碰生产、不写用户库，只做内存对象 + 可选的 JSONL 审计。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

PREFERENCE_SUMMARY_KIND = "preference_summary"

# "短"的上限：plain-language 摘要，不是小作文。
MAX_TEXT_CHARS = 500
# 单条 evidence 引用的上限：引用，不是原文搬运。
MAX_EVIDENCE_CHARS = 300


class PreferenceEvidenceError(ValueError):
    """证据缺失或陈述不合法：拒绝写入。"""


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _nonempty_str(value, field):
    if not isinstance(value, str) or not value.strip():
        raise PreferenceEvidenceError(f"{field} 必须是非空字符串")
    return value.strip()


def validate_evidence_item(item: dict, index: int) -> dict:
    """校验单条 {statement, evidence}。任一缺失/为空即拒绝。"""
    if not isinstance(item, dict):
        raise PreferenceEvidenceError(f"evidence[{index}] 必须是对象")
    statement = _nonempty_str(item.get("statement"), f"evidence[{index}].statement")
    evidence = _nonempty_str(item.get("evidence"), f"evidence[{index}].evidence")
    if len(evidence) > MAX_EVIDENCE_CHARS:
        raise PreferenceEvidenceError(
            f"evidence[{index}].evidence 超过 {MAX_EVIDENCE_CHARS} 字："
            "引用，不是原文搬运"
        )
    return {"statement": statement, "evidence": evidence}


def validate_card(card: dict) -> dict:
    """校验整张偏好卡。合法返回规范化后的副本，不合法抛错。"""
    if not isinstance(card, dict):
        raise PreferenceEvidenceError("card 必须是对象")
    if card.get("kind") != PREFERENCE_SUMMARY_KIND:
        raise PreferenceEvidenceError(
            f"kind 必须是 {PREFERENCE_SUMMARY_KIND!r}，实际 {card.get('kind')!r}"
        )
    subject = _nonempty_str(card.get("subject"), "subject")
    text = _nonempty_str(card.get("text"), "text")
    if len(text) > MAX_TEXT_CHARS:
        raise PreferenceEvidenceError(
            f"text 超过 {MAX_TEXT_CHARS} 字：偏好摘要要短，不是小作文"
        )
    evidence = card.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise PreferenceEvidenceError("evidence 必须是非空列表：每条陈述都要有证据")
    items = [validate_evidence_item(it, i) for i, it in enumerate(evidence)]
    version = card.get("version", 1)
    if not isinstance(version, int) or version < 1:
        raise PreferenceEvidenceError("version 必须是 >= 1 的整数")
    status = card.get("status", "active")
    if status not in ("active", "withdrawn"):
        raise PreferenceEvidenceError("status 只能是 active/withdrawn")
    return {
        "kind": PREFERENCE_SUMMARY_KIND,
        "subject": subject,
        "text": text,
        "evidence": items,
        "version": version,
        "status": status,
        "updated_at": card.get("updated_at") or _now_iso(),
    }


class PreferenceCardStore:
    """偏好卡的内存存储 + 只增审计。

    只管卡片对象本身，不写用户库、不碰生产。审计日志是 JSONL 只增文件，
    拒绝写入同样记审计（action=rejected），可回查"这条为什么没写进去"。
    """

    def __init__(self, audit_path=None, now_fn=None):
        self.audit_path = Path(audit_path) if audit_path else None
        self.now_fn = now_fn or _now_iso
        self.cards: dict[str, dict] = {}
        if self.audit_path:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)

    # -- audit ---------------------------------------------------------
    def _audit(self, action: str, subject: str, version=None, note: str = "") -> None:
        entry = {
            "ts": self.now_fn(),
            "action": action,
            "kind": PREFERENCE_SUMMARY_KIND,
            "subject": subject,
            "version": version,
            "note": note,
        }
        if self.audit_path:
            with open(self.audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # -- write ---------------------------------------------------------
    def create(self, subject: str, text: str, evidence: list[dict]) -> dict:
        """新建偏好卡（version=1）。证据缺失即拒绝 + 记审计。"""
        try:
            card = validate_card(
                {
                    "kind": PREFERENCE_SUMMARY_KIND,
                    "subject": subject,
                    "text": text,
                    "evidence": evidence,
                    "version": 1,
                    "status": "active",
                    "updated_at": self.now_fn(),
                }
            )
        except PreferenceEvidenceError as e:
            self._audit("rejected", str(subject), version=1, note=f"create: {e}")
            raise
        self.cards[card["subject"]] = card
        self._audit("created", card["subject"], version=1)
        return dict(card)

    def update(self, subject: str, text: str | None = None,
               evidence: list[dict] | None = None) -> dict:
        """更新偏好卡：version+1，updated_at 刷新。证据规则同样强制。"""
        old = self.cards.get(subject)
        if old is None:
            raise KeyError(f"no such card: {subject!r}")
        if old["status"] == "withdrawn":
            raise PreferenceEvidenceError("已撤回的卡不能更新，先恢复再改")
        merged = {
            "kind": PREFERENCE_SUMMARY_KIND,
            "subject": subject,
            "text": text if text is not None else old["text"],
            "evidence": evidence if evidence is not None else old["evidence"],
            "version": old["version"] + 1,
            "status": "active",
            "updated_at": self.now_fn(),
        }
        try:
            card = validate_card(merged)
        except PreferenceEvidenceError as e:
            self._audit("rejected", subject, version=merged["version"],
                        note=f"update: {e}")
            raise
        self.cards[subject] = card
        self._audit("updated", subject, version=card["version"])
        return dict(card)

    def withdraw(self, subject: str, reason: str = "") -> dict:
        """撤回偏好卡：status=withdrawn，历史保留，可回查。"""
        old = self.cards.get(subject)
        if old is None:
            raise KeyError(f"no such card: {subject!r}")
        card = dict(old)
        card["status"] = "withdrawn"
        card["updated_at"] = self.now_fn()
        self.cards[subject] = card
        self._audit("withdrawn", subject, version=card["version"], note=reason)
        return dict(card)

    # -- read ----------------------------------------------------------
    def get(self, subject: str) -> dict | None:
        card = self.cards.get(subject)
        return dict(card) if card else None

    def list_active(self) -> list[dict]:
        return [dict(c) for c in self.cards.values() if c["status"] == "active"]
