"""Bridge: the only door between subject namespaces.

Default-deny. Nothing crosses a subject boundary unless:
1. it fits exactly one of three structured templates (no free text);
2. a human approves it after seeing the FULL rendered text;
3. it has not expired and has not been withdrawn.

The keyword blacklist is a NOISE FILTER ONLY, never the security
boundary. It can miss rephrased private content (proven in earlier
rounds: rephrased variants passed the keyword check). The real boundary
is human approval with the full text shown. A request that evades the
blacklist is still pending, still invisible to the target subject, and
still needs approval -- that is the test that matters.

TTL: BRIDGE_TTL_HOURS = 72 is a LOW-CONFIDENCE GUESS pending a month
of real use (handover wording, kept verbatim in spirit). Do not cite
72h as a measured optimum; it is a placeholder that real usage must
replace. Expiry is fail-closed: an expired request can never be
approved and never produces a copy.
"""

from __future__ import annotations

import itertools
import copy
import functools
import threading
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from .subjects import SUBJECTS, validate_subject
except Exception:  # pragma: no cover
    try:
        from subjects import SUBJECTS, validate_subject  # type: ignore
    except Exception:
        SUBJECTS = ("user", "persona", "viewer")

        def validate_subject(s):
            if s in SUBJECTS:
                return s
            raise ValueError(f"unknown subject namespace: {s!r}")


BRIDGE_TTL_HOURS = 72  # LOW-CONFIDENCE GUESS pending real use; see module docstring.
BRIDGE_TTL = timedelta(hours=BRIDGE_TTL_HOURS)

# Generic sensitive-category words, not personal data. This list is a
# noise filter: it flags obvious cases for the approver, it does not
# decide anything by itself.
BLACKLIST = (
    "身份证",
    "银行卡",
    "密码",
    "银行账号",
    "健康诊断",
    "医疗记录",
    "家庭住址",
    "电话号码",
)

# Three structured templates ONLY. Fields are the whole content; there
# is no free-text field to smuggle extra content through.
TEMPLATES = {
    "relationship": {
        "fields": ("a", "b", "c"),
        "kind": "relationship",
        "render": lambda f: f"{f['a']} 是 {f['b']} 的 {f['c']}",
    },
    "method": {
        "fields": ("task", "method"),
        "kind": "procedure",
        "render": lambda f: f"做 {f['task']} 用 {f['method']} 方式",
    },
    "result": {
        "fields": ("x", "result"),
        "kind": "fact",
        "render": lambda f: f"{f['x']} 的结果是 {f['result']}",
    },
}


def _now():
    return datetime.now(timezone.utc)


def _parse(dt):
    if isinstance(dt, datetime):
        return dt
    return datetime.fromisoformat(str(dt).replace("Z", "+00:00"))


def blacklist_hits(fields: dict) -> list[str]:
    blob = " ".join(str(v) for v in fields.values())
    return [w for w in BLACKLIST if w in blob]


# 原始来源模式：疑似"原文/原始日志混入摘要"的噪音信号。
# 与 BLACKLIST 同级 —— 只是给审批人的提示，不是安全边界。
# 真正的边界永远是：人工看完全文后批准（见 docs/adr/0004-summary-only-bridge.md）。
_RAW_PATTERNS = (
    # 系统日志行：[时间] [INFO] ... / [2026-11-03 20:31:02] ...
    (r"\[\d{2}:\d{2}:\d{2}\]\s*\[", "疑似系统日志行"),
    (r"\[\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}", "疑似带时间戳的原始行"),
    # 聊天 JSONL 原始行：{"type":"chat",...,"userId":...}
    (r'\{[^{}]*"userId"\s*:', "疑似聊天原始 JSON 行"),
    (r'\{[^{}]*"type"\s*:\s*"chat"', "疑似聊天原始 JSON 行"),
    # 礼物事件原始格式：昵称(id=...) -> 物品 xN
    (r"\(id=[^)]*\)\s*->", "疑似礼物事件原始行"),
)


def raw_source_hits(fields: dict) -> list[str]:
    """检查桥接字段里是否混入了原始日志/原文。

    返回命中的提示列表（去重）。这是噪音过滤器：命中只提醒审批人
    逐条核对，不自动拒绝；没命中也不代表干净 —— 原文改写后同样可能
    混入，人工看全文才是边界。
    """
    import re

    blob = " ".join(str(v) for v in fields.values())
    hits = []
    for pattern, label in _RAW_PATTERNS:
        if re.search(pattern, blob):
            if label not in hits:
                hits.append(label)
    return hits


def _serialized(method):
    """Serialize prototype operations and restore in-memory state on failure.

    Persistent audit failures propagate: a returned approval must never pretend
    it was audited. A partially appended disk audit remains an attempt record;
    this ephemeral planner is not a durable memory store.
    """

    @functools.wraps(method)
    def run(self, *args, **kwargs):
        with self._lock:
            before_requests = copy.deepcopy(self.requests)
            before_audit = copy.deepcopy(self.audit)
            try:
                return method(self, *args, **kwargs)
            except Exception:
                self.requests = before_requests
                self.audit = before_audit
                raise

    return run


class BridgeStore:
    """In-memory bridge requests with an audited decision log.

    This store holds requests, not memories. Approving a request returns
    a copy dict; the caller decides where (if anywhere) to store it.
    Nothing here writes to markdown claims or to any subject's records.
    """

    def __init__(self, audit_path=None, now_fn=None):
        self._lock = threading.RLock()
        self.audit_path = Path(audit_path) if audit_path else None
        self.now_fn = now_fn or _now
        self.requests: dict[str, dict] = {}
        self.audit: list[dict] = []
        self._counter = itertools.count(1)

    # -- creation ------------------------------------------------------
    @_serialized
    def create(
        self, source_subject, target_subject, template, fields, origin_id=None, created_at=None
    ) -> dict:
        source = validate_subject(source_subject)
        target = validate_subject(target_subject)
        if source == target:
            raise ValueError("bridge within the same subject is not a bridge; use a direct copy")
        if template not in TEMPLATES:
            raise ValueError(
                f"unknown bridge template: {template!r} (expected one of {sorted(TEMPLATES)})"
            )
        spec = TEMPLATES[template]
        clean = {}
        for name in spec["fields"]:
            value = fields.get(name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"template {template} requires non-empty field {name!r}")
            clean[name] = value.strip()
        extra = sorted(set(fields) - set(spec["fields"]))
        if extra:
            raise ValueError(f"template {template} does not accept fields: {extra}")
        created = _parse(created_at) if created_at is not None else self.now_fn()
        req_id = f"bridge-{next(self._counter):04d}"
        req = {
            "id": req_id,
            "source_subject": source,
            "target_subject": target,
            "template": template,
            "fields": clean,
            "full_text": spec["render"](clean),
            "kind": spec["kind"],
            "origin_id": origin_id,
            "status": "pending",
            "blacklist_flagged": bool(blacklist_hits(clean)),
            "blacklist_hits": blacklist_hits(clean),
            "created_at": created.isoformat(),
            "expires_at": (created + BRIDGE_TTL).isoformat(),
            "approver": None,
            "approved_at": None,
        }
        self.requests[req_id] = req
        self._audit("created", req_id, note="pending; default-deny")
        return copy.deepcopy(req)

    # -- expiry --------------------------------------------------------
    def _refresh(self, req: dict) -> dict:
        if req["status"] in ("pending", "approved") and self.now_fn() >= _parse(req["expires_at"]):
            req["status"] = "expired"
            self._audit("expired", req["id"], note="TTL elapsed; fail-closed")
        return req

    @_serialized
    def get(self, req_id: str) -> dict:
        req = self.requests.get(req_id)
        if req is None:
            raise KeyError(req_id)
        return copy.deepcopy(self._refresh(req))

    # -- approval ------------------------------------------------------
    @_serialized
    def approve(
        self,
        req_id: str,
        approver: str,
        confirmed_full_text: bool = False,
        acknowledge_flag: bool = False,
    ) -> dict:
        """Approve one request. Returns the bridge copy dict.

        confirmed_full_text=True means the approver has seen the full
        rendered text and takes responsibility for it. Without it, or
        without a non-empty approver, approval is refused. A blacklist-
        flagged request additionally requires acknowledge_flag=True.
        """
        req = self.requests.get(req_id)
        if req is None:
            raise KeyError(req_id)
        self._refresh(req)
        if req["status"] != "pending":
            raise ValueError(f"cannot approve request in status {req['status']!r}")
        if not approver or not str(approver).strip():
            raise ValueError("approver is required")
        if not confirmed_full_text:
            raise ValueError("approval requires confirmed_full_text=True (full text seen)")
        if req["blacklist_flagged"] and not acknowledge_flag:
            raise ValueError("blacklist-flagged request requires acknowledge_flag=True")
        # Re-check at approval time: template must still be valid.
        spec = TEMPLATES[req["template"]]
        for name in spec["fields"]:
            if not req["fields"].get(name):
                raise ValueError(f"re-check failed: field {name!r} is empty")
        req["status"] = "approved"
        req["approver"] = str(approver).strip()
        req["approved_at"] = self.now_fn().isoformat()
        self._audit("approved", req_id, approver=req["approver"])
        return self._copy(req)

    def _copy(self, req: dict) -> dict:
        return {
            "subject": req["target_subject"],
            "kind": req["kind"],
            "value": req["full_text"],
            "source": "bridge",
            "origin_id": req["origin_id"],
            "bridge_id": req["id"],
            "approver": req["approver"],
            "approved_at": req["approved_at"],
            "status": "active",
        }

    @_serialized
    def approved_copy(self, req_id: str):
        """Copy only if approved and not expired; otherwise None."""
        req = self.requests.get(req_id)
        if req is None:
            raise KeyError(req_id)
        self._refresh(req)
        if req["status"] != "approved":
            return None
        return self._copy(req)

    @_serialized
    def deny(self, req_id: str, approver: str, reason: str = "") -> dict:
        req = self.requests.get(req_id)
        if req is None:
            raise KeyError(req_id)
        self._refresh(req)
        if req["status"] != "pending":
            raise ValueError(f"cannot deny request in status {req['status']!r}")
        req["status"] = "denied"
        self._audit("denied", req_id, approver=approver, note=reason)
        return copy.deepcopy(req)

    @_serialized
    def withdraw(self, req_id: str, by: str = "") -> dict:
        """Single-item withdrawal: only pending requests can be withdrawn."""
        req = self.requests.get(req_id)
        if req is None:
            raise KeyError(req_id)
        self._refresh(req)
        if req["status"] != "pending":
            raise ValueError(f"cannot withdraw request in status {req['status']!r}")
        req["status"] = "withdrawn"
        self._audit("withdrawn", req_id, approver=by)
        return copy.deepcopy(req)

    @_serialized
    def approve_batch(
        self,
        req_ids,
        approver: str,
        confirmed_full_text: bool = False,
        acknowledge_flag: bool = False,
    ) -> list[dict]:
        """Batch approval: all-or-nothing (PATCH1 made this strictly true).

        Phase 1 re-checks EVERY unique item (pending, unexpired, template
        valid, full-text confirmed, flag acknowledged if needed) and keeps
        the requests untouched except for expiry marking. If ANY item
        fails, the whole batch aborts and no item is approved.
        Phase 2 only flips already-validated requests; it performs no
        refresh and takes the clock once, so it cannot fail halfway: there
        is no duplicate-id double approval and no cross-phase expiry window
        (the two ways the old two-call implementation could partially land).
        """
        unique_ids = []
        for req_id in req_ids:
            if req_id in unique_ids:
                raise ValueError(f"batch aborted: duplicate request id {req_id!r} in one batch")
            unique_ids.append(req_id)
        reqs = []
        for req_id in unique_ids:
            req = self.requests.get(req_id)
            if req is None:
                raise KeyError(req_id)
            self._refresh(req)
            if req["status"] != "pending":
                raise ValueError(f"batch aborted: {req_id} is {req['status']!r}, not pending")
            if not approver or not str(approver).strip():
                raise ValueError("batch aborted: approver is required")
            if not confirmed_full_text:
                raise ValueError("batch aborted: confirmed_full_text is required")
            if req["blacklist_flagged"] and not acknowledge_flag:
                raise ValueError(f"batch aborted: {req_id} is blacklist-flagged")
            spec = TEMPLATES[req["template"]]
            for name in spec["fields"]:
                if not req["fields"].get(name):
                    raise ValueError(f"batch aborted: {req_id} re-check failed on {name!r}")
            reqs.append(req)
        # Phase 2: validated only, single clock read. The landing is a
        # transaction: previous field values are saved and restored if
        # anything raises mid-way, so a failure cannot leave half a batch
        # approved (D6 补证: the failure path is tested, not just reasoned).
        approved_at = self.now_fn().isoformat()
        approver_name = str(approver).strip()
        before = [(req, req["status"], req.get("approver"), req.get("approved_at")) for req in reqs]
        try:
            copies = []
            for req in reqs:
                req["status"] = "approved"
                req["approver"] = approver_name
                req["approved_at"] = approved_at
                copies.append(self._copy(req))
                self._audit("approved", req["id"], approver=approver_name)
            return copies
        except Exception:
            for req, status, old_approver, old_approved_at in before:
                req["status"] = status
                req["approver"] = old_approver
                req["approved_at"] = old_approved_at
            raise

    @_serialized
    def list_pending(self) -> list[dict]:
        out = []
        for req in self.requests.values():
            refreshed = self._refresh(req)
            if refreshed["status"] == "pending":
                out.append(copy.deepcopy(refreshed))
        return out

    # -- audit ---------------------------------------------------------
    def _audit(self, action: str, req_id: str, approver=None, note: str = ""):
        entry = {
            "at": self.now_fn().isoformat(),
            "action": action,
            "request_id": req_id,
            "approver": approver,
            "note": note,
        }
        self.audit.append(entry)
        if self.audit_path:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
                fh.flush()
        return entry
