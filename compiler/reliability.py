"""可靠性层：来源归因、去重、冲突、审批审计、快照恢复。

设计原则
- 归因是硬约束，不是模型意见：非 user 说话人永远不能产出 source=user。
- 去重与冲突判定只依赖可复现的规范化文本，不依赖模型调用。
- 所有写操作可回滚：审批、去重、红act 都落在 Transaction 里。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path

USER = "user"
# 除 USER 以外的来源一律不得写成用户事实。
NON_USER_SOURCES = ("assistant", "quoted", "unknown", "system", "tool", "third_party")
VALID_SOURCES = (USER,) + NON_USER_SOURCES

_PUNCT = re.compile(
    r"[\s\u3000`~!@#$%^&*()\-_=+\[\]{};:'\",.<>/?\\|！？。，、；：“”‘’（）【】《》…·]+"
)
_NUM = re.compile(r"\d+")
# 句末语气/标点不改变语义："完成了" 与 "完成" 视为同义。
_TRAIL = re.compile(r"(?:了|的|啦|呢|吧|啊|呀|哦|嘛|而已)+$")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# 来源归因
# --------------------------------------------------------------------------


def canonical_source(value: str | None) -> str:
    text = str(value or "").strip().lower()
    if text in VALID_SOURCES:
        return text
    if text in {"human", "owner", "self", "我", "本人"}:
        return USER
    return "unknown"


def enforce_attribution(claim: dict, speaker: str | None = None) -> dict:
    """把归因收敛到证据。

    - speaker 有真实角色时以 speaker 为准；
    - speaker 缺失/unknown 时，claim 自报的 source=user 不可信，降为 unknown；
    - 任何情况下 assistant/quoted/system 都不会变成 user。
    """
    claim = dict(claim)
    stated = canonical_source(claim.get("source"))
    actual = canonical_source(speaker) if speaker else None

    if actual and actual != "unknown":
        claim["source"] = actual
    elif stated == USER:
        # 没有说话人证据却自称用户事实：拒绝。
        claim["source"] = "unknown"
        claim["attribution_conflict"] = True
    else:
        claim["source"] = stated

    if claim["source"] != USER:
        # 非用户来源禁止进入用户画像，也禁止长期常驻注入。
        if claim.get("kind") == "identity":
            claim["kind"] = "fact"
        claim["injection_policy"] = "recall"
        claim["auto_write"] = False
        claim["evidence_verified"] = bool(claim.get("evidence_verified")) and claim["source"] == "quoted"
    return claim


def is_user_fact(claim: dict) -> bool:
    return canonical_source(claim.get("source")) == USER and bool(claim.get("evidence_verified"))


# --------------------------------------------------------------------------
# 规范化与指纹
# --------------------------------------------------------------------------


def normalize_text(text: str | None) -> str:
    raw = unicodedata.normalize("NFKC", str(text or "")).lower()
    return _PUNCT.sub("", raw)


def core_text(text: str | None) -> str:
    """规范化后再去掉句末语气词，用于同义判定。"""
    return _TRAIL.sub("", normalize_text(text))


def fingerprint(claim: dict) -> str:
    key = "\x1f".join(
        [
            str(claim.get("kind") or ""),
            core_text(claim.get("subject")),
            core_text(claim.get("predicate")),
            core_text(claim.get("value")),
        ]
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def _value_key(claim: dict) -> str:
    return _NUM.sub("#", normalize_text(claim.get("value")))


def similarity(a: str, b: str) -> float:
    """低成本近似相似度：字符集 Dice + 包含关系，避免引入 difflib 全量比较。"""
    ca, cb = core_text(a), core_text(b)
    ta = set(_NUM.sub("#", ca))
    tb = set(_NUM.sub("#", cb))
    if not ta or not tb:
        return 0.0
    dice = 2 * len(ta & tb) / (len(ta) + len(tb))
    short, long = sorted((ca, cb), key=len)
    if len(short) >= 4 and short in long and len(short) / len(long) >= 0.6:
        return max(dice, 0.96)
    return dice


# --------------------------------------------------------------------------
# 去重 / 冲突
# --------------------------------------------------------------------------


def is_live(claim: dict) -> bool:
    return claim.get("status") in {"active", "pending"}


def duplicate_groups(records: list[dict], threshold: float = 0.95) -> list[list[dict]]:
    """返回重复组。完全同指纹直接归组；同 kind+subject 且值相似度 ≥ 阈值也归组。"""
    exact: dict[str, list[dict]] = {}
    for rec in records:
        if not is_live(rec):
            continue
        exact.setdefault(fingerprint(rec), []).append(rec)

    groups = [g for g in exact.values() if len(g) > 1]
    solo = [g[0] for g in exact.values() if len(g) == 1]

    buckets: dict[tuple, list[dict]] = {}
    for rec in solo:
        key = (str(rec.get("kind")), normalize_text(rec.get("subject")))
        buckets.setdefault(key, []).append(rec)

    for items in buckets.values():
        if len(items) < 2:
            continue
        used: set[int] = set()
        for i, a in enumerate(items):
            if i in used:
                continue
            group = [a]
            for j in range(i + 1, len(items)):
                if j in used:
                    continue
                if similarity(a.get("value"), items[j].get("value")) >= threshold:
                    group.append(items[j])
                    used.add(j)
            if len(group) > 1:
                groups.append(group)
    return groups


def conflict_groups(records: list[dict]) -> list[list[dict]]:
    """同一 (kind, subject, predicate) 下并存的不同取值 → 冲突组。"""
    buckets: dict[tuple, list[dict]] = {}
    for rec in records:
        if rec.get("status") != "active":
            continue
        key = (
            str(rec.get("kind")),
            normalize_text(rec.get("subject")),
            normalize_text(rec.get("predicate")),
        )
        buckets.setdefault(key, []).append(rec)

    groups = []
    for items in buckets.values():
        if len({_value_key(rec) for rec in items}) > 1:
            groups.append(items)
    return groups


def plan_dedupe(records: list[dict], threshold: float = 0.95) -> list[dict]:
    """生成去重计划：每组保留最早/最可信的一条，其余标记 superseded。"""
    plan = []
    for group in duplicate_groups(records, threshold):
        ordered = sorted(
            group,
            key=lambda r: (
                0 if r.get("status") == "active" else 1,
                -float(r.get("confidence") or 0),
                str(r.get("observed_at") or ""),
            ),
        )
        keep = ordered[0]
        for dup in ordered[1:]:
            plan.append(
                {
                    "keep": keep.get("revision_id"),
                    "drop": dup.get("revision_id"),
                    "claim_id": dup.get("claim_id"),
                    "reason": "duplicate",
                    "value": str(dup.get("value") or "")[:80],
                }
            )
    return plan


def apply_dedupe(records: list[dict], plan: list[dict]) -> tuple[list[dict], list[str]]:
    """就地打标，返回 (records, 变更文件无关的 revision id 列表)。"""
    drop = {item["drop"] for item in plan}
    keep_map = {item["drop"]: item["keep"] for item in plan}
    touched = []
    for rec in records:
        rid = rec.get("revision_id")
        if rid in drop:
            rec["status"] = "superseded"
            rec["superseded_by"] = keep_map[rid]
            rec["dedupe_reason"] = "duplicate"
            touched.append(rid)
    return records, touched


# --------------------------------------------------------------------------
# 写入前置判定（供 API / pipeline 复用）
# --------------------------------------------------------------------------


def prepare_write(records: list[dict], claim: dict, threshold: float = 0.95) -> dict:
    """判定一条新记忆该怎么落库。

    返回 {"action": insert|duplicate|conflict, "target": revision_id|None}
    """
    cand = fingerprint(claim)
    for rec in records:
        if rec.get("status") != "active":
            continue
        if fingerprint(rec) == cand:
            return {"action": "duplicate", "target": rec.get("revision_id")}
    key = (
        str(claim.get("kind")),
        normalize_text(claim.get("subject")),
        normalize_text(claim.get("predicate")),
    )
    for rec in records:
        if rec.get("status") != "active":
            continue
        if (
            str(rec.get("kind")),
            normalize_text(rec.get("subject")),
            normalize_text(rec.get("predicate")),
        ) == key:
            if similarity(rec.get("value"), claim.get("value")) >= threshold:
                return {"action": "duplicate", "target": rec.get("revision_id")}
            return {"action": "conflict", "target": rec.get("revision_id")}
    return {"action": "insert", "target": None}


# --------------------------------------------------------------------------
# 审计日志与撤销
# --------------------------------------------------------------------------


class AuditLog:
    """追加式审计日志。每条记录带 txid，便于整体撤销。"""

    def __init__(self, root: Path):
        self.path = Path(root) / "system" / "audit-log.jsonl"

    def append(self, action: str, txid: str | None, **fields) -> dict:
        entry = {"at": now_iso(), "action": action, "txid": txid, **fields}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def read(self, limit: int = 200) -> list[dict]:
        if not self.path.exists():
            return []
        rows = [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return rows[-limit:]

    def last_tx(self) -> str | None:
        for row in reversed(self.read(500)):
            if row.get("txid"):
                return row["txid"]
        return None


def undo(root: Path, txid: str | None = None) -> str:
    """撤销一次写事务（默认撤销最近一次）。返回被恢复的事务 id。"""
    from storage import Transaction

    log = AuditLog(root)
    target = txid or log.last_tx()
    if not target:
        raise RuntimeError("no auditable transaction to undo")
    Transaction(root).rollback(target, yes=True)
    log.append("undo", target, note="rolled back")
    return target


# --------------------------------------------------------------------------
# 快照 / 恢复
# --------------------------------------------------------------------------

SNAPSHOT_SKIP = {"transactions", "__pycache__"}



def _check_duplicate_names(names):
    """Fail loudly on duplicate archive names instead of overwriting.

    zipfile.writestr() silently overwrites an earlier entry with the same
    name, so a snapshot that contains a duplicate name would restore
    only the last copy and lose the first one without any error. This
    check runs on the collected name list BEFORE any zip is written, so
    a duplicate can never leave a partial archive behind.
    Returns the names unchanged; raises ValueError listing duplicates.
    """
    seen = set()
    dups = []
    for name in names:
        if name in seen and name not in dups:
            dups.append(name)
        seen.add(name)
    if dups:
        raise ValueError(f"snapshot duplicate archive names: {dups}")
    return names


def snapshot(root: Path, dest: Path, include_raw: bool = False) -> dict:
    root = Path(root)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Collect first, write second: duplicates are detected here, before
    # any zip file exists, so a failure leaves no partial archive.
    collected = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if any(part in SNAPSHOT_SKIP for part in Path(rel).parts):
            continue
        if not include_raw and rel.startswith("_raw/"):
            continue
        collected.append((rel, path))
    _check_duplicate_names([rel for rel, _ in collected])
    entries = []
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, path in collected:
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            zf.writestr(rel, data)
            entries.append({"path": rel, "bytes": len(data), "sha256": digest})
    manifest = {
        "schema_version": 1,
        "created_at": now_iso(),
        "root": str(root),
        "files": entries,
        "digest": hashlib.sha256(
            "".join(e["sha256"] for e in entries).encode("utf-8")
        ).hexdigest(),
    }
    (dest.with_suffix(dest.suffix + ".json")).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return manifest


def verify_snapshot(archive: Path, manifest_path: Path | None = None) -> dict:
    archive = Path(archive)
    manifest_path = manifest_path or archive.with_suffix(archive.suffix + ".json")
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    expected = {e["path"]: e["sha256"] for e in manifest["files"]}
    ok, bad = 0, []
    with zipfile.ZipFile(archive) as zf:
        names = set(zf.namelist())
        for rel, digest in expected.items():
            if rel not in names:
                bad.append({"path": rel, "reason": "missing"})
                continue
            if hashlib.sha256(zf.read(rel)).hexdigest() != digest:
                bad.append({"path": rel, "reason": "sha256_mismatch"})
                continue
            ok += 1
        extra = names - set(expected)
    return {
        "ok": not bad and not extra,
        "verified": ok,
        "expected": len(expected),
        "failures": bad[:10],
        "unexpected": sorted(extra)[:10],
    }


def restore_snapshot(archive: Path, target: Path, overwrite: bool = False) -> dict:
    """恢复到空目录或显式允许覆盖的目录；先验签再落盘。"""
    archive = Path(archive)
    check = verify_snapshot(archive)
    if not check["ok"]:
        raise RuntimeError(f"snapshot failed verification: {check['failures'][:3]}")
    target = Path(target)
    if target.exists() and any(target.iterdir()) and not overwrite:
        raise RuntimeError("target not empty; pass overwrite=True")
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(target)
    return {"ok": True, "restored": check["verified"], "target": str(target)}


def clone_root(src: Path, dest: Path) -> Path:
    """完整复制记忆根（用于隔离演练）。"""
    src, dest = Path(src), Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    return dest


# --------------------------------------------------------------------------
# 外部事务：快照不落在记忆根内，避免 pre-image 把脱敏内容又写回去
# --------------------------------------------------------------------------


class ExternalTransaction:
    """与 storage.Transaction 语义一致，但快照与日志存放在记忆根之外。

    用途：脱敏 / 隔离类操作。storage.Transaction 会把 before 全文写进
    `system/transactions/`，等于把刚删掉的明文再存一份。
    """

    def __init__(self, root: Path, store: Path):
        self.root = Path(root).resolve()
        self.store = Path(store)
        self.store.mkdir(parents=True, exist_ok=True)
        (self.store / "tx").mkdir(exist_ok=True)
        self.journal = self.store / "journal.json"

    def _safe(self, rel: str) -> Path:
        from storage import safe_path

        return safe_path(self.root, rel)

    def recover(self) -> None:
        if not self.journal.exists():
            return
        entry = json.loads(self.journal.read_text(encoding="utf-8"))
        for rel, item in entry["files"].items():
            path = self._safe(rel)
            current = (
                hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
            )
            if current not in (item["before_hash"], item["after_hash"]):
                raise RuntimeError("transaction recovery conflicts with external edits")
        for rel, item in entry["files"].items():
            self._write(rel, item["after"])
        self.journal.unlink()

    def _write(self, rel: str, text: str) -> None:
        from storage import atomic_write

        path = self._safe(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, text)

    def commit(self, changes: dict) -> str | None:
        if self.journal.exists():
            raise RuntimeError("recover pending transaction first")
        import uuid

        txid = uuid.uuid4().hex
        files = {}
        for rel, text in changes.items():
            path = self._safe(rel)
            before = path.read_bytes() if path.exists() else None
            after = text.encode("utf-8")
            if before == after:
                continue
            files[rel] = {
                "before": before.decode("utf-8") if before is not None else None,
                "before_hash": hashlib.sha256(before).hexdigest() if before is not None else None,
                "after": text,
                "after_hash": hashlib.sha256(after).hexdigest(),
            }
        if not files:
            return None
        entry = {"schema_version": 2, "id": txid, "external": True, "files": files}
        data = json.dumps(entry, ensure_ascii=False, indent=2)
        (self.store / "tx" / f"{txid}.json").write_text(data, encoding="utf-8")
        self.journal.write_text(data, encoding="utf-8")
        self.recover()
        return txid

    def rollback(self, txid: str, yes: bool = False) -> str | None:
        if not re.fullmatch(r"[0-9a-f]{32}", txid):
            raise ValueError("invalid transaction ID")
        if self.journal.exists():
            raise RuntimeError("recover interrupted transaction first")
        entry = json.loads((self.store / "tx" / f"{txid}.json").read_text(encoding="utf-8"))
        changes = {}
        for rel, item in entry["files"].items():
            path = self._safe(rel)
            if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != item["after_hash"]:
                raise RuntimeError("rollback would overwrite later edits")
            changes[rel] = item["before"] if item["before"] is not None else ""
        if yes:
            return self.commit(changes)
        return list(changes)
