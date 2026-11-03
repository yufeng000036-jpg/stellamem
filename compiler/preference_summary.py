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

写入：
    - create / update 的 status 是 pending，具名人类 approve 之后才是 active；
    - 每个版本的 text/evidence/status 追加进历史，不覆盖旧版本；
    - rollback(version, by) 再追加一条 pending，正文来自那个旧版本。

本模块不做：
    - 不做语义忠实度自动判定（不可靠，不硬上）；
    - 不接 recall/注入（召回侧另见 search.py；这里只产卡片对象）；
    - 不碰生产、不写用户库。给出 audit_path 时，版本历史是该路径上的
      只增 JSONL：每条写入后 fsync，启动时从磁盘重建全部版本。
"""

from __future__ import annotations

import copy
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("stellamem.preference_summary")

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
            f"evidence[{index}].evidence 超过 {MAX_EVIDENCE_CHARS} 字：引用，不是原文搬运"
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
        raise PreferenceEvidenceError(f"text 超过 {MAX_TEXT_CHARS} 字：偏好摘要要短，不是小作文")
    evidence = card.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise PreferenceEvidenceError("evidence 必须是非空列表：每条陈述都要有证据")
    items = [validate_evidence_item(it, i) for i, it in enumerate(evidence)]
    version = card.get("version", 1)
    if not isinstance(version, int) or version < 1:
        raise PreferenceEvidenceError("version 必须是 >= 1 的整数")
    # 未写 status 时默认 pending：新建/改写都要等人审，不能直接 active。
    status = card.get("status", "pending")
    if status not in ("pending", "active", "withdrawn"):
        raise PreferenceEvidenceError("status 只能是 pending/active/withdrawn")
    return {
        "kind": PREFERENCE_SUMMARY_KIND,
        "subject": subject,
        "text": text,
        "evidence": items,
        "version": version,
        "status": status,
        "updated_at": card.get("updated_at") or _now_iso(),
    }


_STATE_ACTIONS = {"created", "updated", "approved", "withdrawn", "rolled_back"}


class PreferenceCardStore:
    """偏好卡的只增版本历史。

    新建和更新都进入 pending，须具名人类 approve 后才变 active。
    每个版本的正文单独保存，只追加、不改旧版本；rollback 追加一条新的
    pending 版本，内容来自指定旧版本。audit_path 是这份历史的唯一账本：
    追加 JSONL，写完 fsync，从不覆盖已写字节。新实例从同一路径重建
    全部版本、状态和批准。拒绝写入同样记审计（action=rejected）。
    不管用户库、不碰生产。
    """

    def __init__(self, audit_path=None, now_fn=None):
        self.audit_path = Path(audit_path) if audit_path else None
        self.now_fn = now_fn or _now_iso
        self.cards: dict[str, dict] = {}
        self._history: dict[str, list[dict]] = {}
        self._replaying = False
        if self.audit_path:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            if self.audit_path.exists():
                self._load()

    # -- durable log ---------------------------------------------------
    def _fsync_dir(self, path: Path) -> None:
        try:
            fd = os.open(path, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)

    def _append_line(self, entry: dict) -> None:
        """Append one JSON line and fsync it. Never rewrites earlier bytes."""
        if not self.audit_path or self._replaying:
            return
        payload = (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        prefix = b""
        if self.audit_path.exists() and self.audit_path.stat().st_size:
            with self.audit_path.open("rb") as fh:
                fh.seek(-1, os.SEEK_END)
                if fh.read(1) != b"\n":
                    # A crashed writer may have left a torn tail. Terminate it
                    # with a newline so the next record cannot be glued onto it.
                    prefix = b"\n"
        fd = os.open(self.audit_path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
        try:
            os.write(fd, prefix + payload)
            os.fsync(fd)
        finally:
            os.close(fd)
        self._fsync_dir(self.audit_path.parent)

    def _load(self) -> None:
        raw = self.audit_path.read_bytes()
        if not raw:
            return
        skipped = 0
        if not raw.endswith(b"\n"):
            cut = raw.rfind(b"\n")
            raw = raw[: cut + 1] if cut >= 0 else b""
            skipped += 1
        entries = []
        if raw:
            for line in raw.decode("utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    skipped += 1
        self._replaying = True
        try:
            for entry in entries:
                self._apply(entry)
        finally:
            self._replaying = False
        if skipped:
            logger.warning(
                "preference history fail-closed: skipped %d partial or unreadable "
                "record(s) in %s; earlier complete versions were kept",
                skipped,
                self.audit_path,
            )

    def _apply(self, entry: dict) -> None:
        if entry.get("action") not in _STATE_ACTIONS:
            return
        snap = entry.get("snapshot")
        if not isinstance(snap, dict):
            content = entry.get("content") if isinstance(entry.get("content"), dict) else {}
            snap = {
                "kind": entry.get("kind") or PREFERENCE_SUMMARY_KIND,
                "subject": entry.get("subject"),
                "text": content.get("text"),
                "evidence": copy.deepcopy(content.get("evidence") or []),
                "version": entry.get("version"),
                "status": content.get("status") or "pending",
                "updated_at": content.get("updated_at") or entry.get("ts"),
            }
            if content.get("approved_by"):
                snap["approved_by"] = content["approved_by"]
                snap["approved_at"] = content.get("approved_at")
        subject = snap.get("subject")
        if not subject:
            return
        self._history.setdefault(subject, []).append(copy.deepcopy(snap))
        self.cards[subject] = copy.deepcopy(snap)

    def _audit(
        self,
        action: str,
        subject: str,
        version=None,
        note: str = "",
        content=None,
        snapshot=None,
    ) -> None:
        entry = {
            "ts": self.now_fn(),
            "action": action,
            "kind": PREFERENCE_SUMMARY_KIND,
            "subject": subject,
            "version": version,
            "note": note,
        }
        if content is not None:
            entry["content"] = copy.deepcopy(content)
        if snapshot is not None:
            entry["snapshot"] = copy.deepcopy(snapshot)
        self._append_line(entry)

    def _content(self, card: dict) -> dict:
        content = {
            "text": card.get("text"),
            "evidence": copy.deepcopy(card.get("evidence")),
            "status": card.get("status"),
            "updated_at": card.get("updated_at"),
        }
        if card.get("approved_by"):
            content["approved_by"] = card["approved_by"]
            content["approved_at"] = card.get("approved_at")
        return content

    def _commit(self, card: dict, action: str, note: str = "") -> dict:
        """追加不可变快照。不修改已有版本，不覆盖已落盘的行。"""
        snap = copy.deepcopy(card)
        self._history.setdefault(snap["subject"], []).append(snap)
        self.cards[snap["subject"]] = copy.deepcopy(snap)
        self._audit(
            action,
            snap["subject"],
            version=snap["version"],
            note=note,
            content=self._content(snap),
            snapshot=snap,
        )
        return copy.deepcopy(snap)

    def _named(self, by: str, action: str) -> str:
        name = (by or "").strip()
        if not name:
            raise ValueError(f"{action} 需要具名人类（by 非空）")
        return name

    # -- write ---------------------------------------------------------
    def create(self, subject: str, text: str, evidence: list[dict]) -> dict:
        """新建偏好卡（version=1, status=pending）。证据缺失即拒绝 + 记审计。"""
        if subject in self.cards:
            raise KeyError(f"card already exists: {subject!r}")
        try:
            card = validate_card(
                {
                    "kind": PREFERENCE_SUMMARY_KIND,
                    "subject": subject,
                    "text": text,
                    "evidence": evidence,
                    "version": 1,
                    "status": "pending",
                    "updated_at": self.now_fn(),
                }
            )
        except PreferenceEvidenceError as e:
            self._audit("rejected", str(subject), version=1, note=f"create: {e}")
            raise
        return self._commit(card, "created")

    def update(
        self, subject: str, text: str | None = None, evidence: list[dict] | None = None
    ) -> dict:
        """更新偏好卡：version+1，status 回到 pending，旧版本保留。"""
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
            "status": "pending",
            "updated_at": self.now_fn(),
        }
        try:
            card = validate_card(merged)
        except PreferenceEvidenceError as e:
            self._audit(
                "rejected",
                subject,
                version=merged["version"],
                note=f"update: {e}",
                content=self._content(old),
            )
            raise
        return self._commit(card, "updated")

    def approve(self, subject: str, by: str) -> dict:
        """具名人类批准：pending -> active。正文不变，版本 +1，历史只追加。"""
        name = self._named(by, "approve")
        old = self.cards.get(subject)
        if old is None:
            raise KeyError(f"no such card: {subject!r}")
        if old["status"] != "pending":
            raise PreferenceEvidenceError(f"status {old['status']!r} 不能批准，只有 pending 可以")
        card = validate_card(
            {
                "kind": PREFERENCE_SUMMARY_KIND,
                "subject": subject,
                "text": old["text"],
                "evidence": old["evidence"],
                "version": old["version"] + 1,
                "status": "active",
                "updated_at": self.now_fn(),
            }
        )
        card["approved_by"] = name
        card["approved_at"] = self.now_fn()
        return self._commit(card, "approved", note=f"by={name}")

    def withdraw(self, subject: str, reason: str = "") -> dict:
        """撤回偏好卡：追加 status=withdrawn 的新版本，旧版本保留。"""
        old = self.cards.get(subject)
        if old is None:
            raise KeyError(f"no such card: {subject!r}")
        card = validate_card(
            {
                "kind": PREFERENCE_SUMMARY_KIND,
                "subject": subject,
                "text": old["text"],
                "evidence": old["evidence"],
                "version": old["version"] + 1,
                "status": "withdrawn",
                "updated_at": self.now_fn(),
            }
        )
        return self._commit(card, "withdrawn", note=reason)

    def rollback(self, subject: str, version: int, by: str) -> dict:
        """追加一条 pending 新版本，正文来自指定历史版本。不改旧记录。"""
        name = self._named(by, "rollback")
        old = self.cards.get(subject)
        if old is None:
            raise KeyError(f"no such card: {subject!r}")
        prior = None
        for snap in self._history.get(subject, []):
            if snap.get("version") == version:
                prior = snap
                break
        if prior is None:
            raise KeyError(f"no version {version} for {subject!r}")
        card = validate_card(
            {
                "kind": PREFERENCE_SUMMARY_KIND,
                "subject": subject,
                "text": prior["text"],
                "evidence": prior["evidence"],
                "version": old["version"] + 1,
                "status": "pending",
                "updated_at": self.now_fn(),
            }
        )
        return self._commit(card, "rolled_back", note=f"by={name} to_version={version}")

    # -- read ----------------------------------------------------------
    def get(self, subject: str) -> dict | None:
        card = self.cards.get(subject)
        return copy.deepcopy(card) if card else None

    def history(self, subject: str) -> list[dict]:
        """按时间顺序返回该主体的全部版本（深拷贝）。"""
        return [copy.deepcopy(snap) for snap in self._history.get(subject, [])]

    def list_active(self) -> list[dict]:
        return [copy.deepcopy(c) for c in self.cards.values() if c["status"] == "active"]
