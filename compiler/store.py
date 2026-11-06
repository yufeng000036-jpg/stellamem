"""Route validated claims and persist their revision/decision history."""

import json
from datetime import datetime, timezone
from claim_normalizer import ROUTE, THRESHOLDS, claim_id, claim_revision_id, digest
from judge import judge_relation
from security import apply_policy, contains_secret
from storage import Transaction, load_records, render_records, safe_path

INBOX = "system/memory-inbox.md"
FILES = sorted(set(ROUTE.values()) - {"core/user-core.md"}) + [INBOX]


class MemoryStore:
    def __init__(self, root):
        self.root = root
        self.prefixes = {}
        self.records = {}
        for rel in FILES:
            self.prefixes[rel], self.records[rel] = load_records(safe_path(root, rel))
        self.events_path = safe_path(root, "system/version-chain.json")
        self.events = (
            json.loads(self.events_path.read_text(encoding="utf-8"))
            if self.events_path.exists()
            else []
        )
        if not isinstance(self.events, list):
            raise ValueError("invalid version chain")
        self.active_by_id = {}
        for rel, records in self.records.items():
            if rel != INBOX:
                for record in records:
                    if record.get("status") == "active":
                        self.active_by_id.setdefault(record.get("claim_id"), []).append(
                            (rel, record)
                        )
        # append-only 用的原始快照：commit 前拿它和当前状态比对
        self._snapshot = {rel: [dict(r) for r in recs] for rel, recs in self.records.items()}
        self.seen_events = {e.get("event_id") for e in self.events}
        self.dirty = set()
        self.new_events = []
        self.conflicts = []
        self.counts = dict(active=0, pending=0, blocked=0, duplicate=0)

    def ingest(self, claim, mode="review", llm=None, config=None):
        # 验收 P3：未 normalize 的 dict（缺 schema_version）写进去后，
        # load_records 读不了整份文件，索引还静默跳过。入口直接拒绝，
        # 不写坏块。官方链路（pipeline / migrate / sleep_purify）都走
        # normalize_claim，不受影响。
        if not isinstance(claim, dict) or claim.get("schema_version") != 2:
            raise ValueError(
                "ingest requires a normalized claim (schema_version == 2); "
                "run claim_normalizer.normalize_claim first"
            )
        if contains_secret(claim):
            self.counts["blocked"] += 1
            return
        c = apply_policy(claim)
        if c["write_policy"] == "never":
            self.counts["blocked"] += 1
            return
        cid, rev = claim_id(c), claim_revision_id(c)
        event_id = digest(
            {
                "revision": rev,
                "provenance": c.get("provenance"),
                "evidence": c["evidence"],
                "mode": mode,
            }
        )
        if event_id in self.seen_events:
            self.counts["duplicate"] += 1
            return
        active = self.active_by_id.get(cid, [])
        allowed = (
            c["auto_write"]
            and c["confidence"] >= THRESHOLDS.get(c["kind"], 1.01)
            and mode == "review"
            # 只有用户本人原话可以自动成为正式记忆；assistant / quoted / unknown 一律待审。
            and str(c.get("source") or "") == "user"
        )
        relation = "NEW"
        old = None
        if active:
            # More than one active value is ambiguous; never silently pick one.
            if len(active) != 1:
                relation = "UNCERTAIN"
            else:
                old = active[0][1]
                relation = (
                    judge_relation(old, c, llm=llm, config=config) if allowed else "UNCERTAIN"
                )
        if relation in {"UNCERTAIN", "CONTRADICTS", "COMPATIBLE"}:
            allowed = False
        if relation in {"DUPLICATE", "REINFORCES"} and allowed:
            self.counts["duplicate"] += 1
        else:
            if allowed and old and relation in {"SUPERSEDES", "REFINES"}:
                old["status"] = "superseded"
                old["superseded_by"] = rev
                # 双时间戳：记录"它在什么时候失效"。expired_at 是系统发现它失效的时刻，
                # valid_to 使用已核验的较新来源时间；expired_at 保留系统发现时间。
                # 更精确的历史事实生效时间若来源未给出，不由系统猜测。
                _now = (
                    __import__("datetime")
                    .datetime.now(__import__("datetime").timezone.utc)
                    .isoformat(timespec="seconds")
                )
                # Relation engine has verified that this source is newer.
                # Keep source event time separate from system discovery time.
                old["valid_to"] = c["provenance"]["timestamp"]
                old["expired_at"] = _now
                self.dirty.add(active[0][0])
            _now = datetime.now(timezone.utc).isoformat()
            c.update(
                claim_id=cid,
                revision_id=rev,
                event_id=event_id,
                status="active" if allowed else "pending",
                relation=relation,
            )
            if not allowed:
                # Time-based second chance (pending auto-approval): stamp the
                # moment this claim entered the inbox. Only records stamped
                # here can ever auto-approve. Anything that landed before
                # this field existed has no stamp and stays manual forever.
                # Never derive this from provenance.timestamp: a new claim
                # may quote an old message.
                c["pending_since"] = _now
            rel = ROUTE[c["kind"]] if allowed else INBOX
            self.records[rel].append(c)
            if allowed:
                self.active_by_id[cid] = [(rel, c)]
            self.dirty.add(rel)
            self.counts["active" if allowed else "pending"] += 1
        event = {
            "event_id": event_id,
            "id": cid,
            "current": rev,
            "supersedes": old.get("revision_id") if old else None,
            "relation": relation,
            "status": "active" if allowed else "pending",
            "date": datetime.now(timezone.utc).isoformat(),
            "provenance": c.get("provenance", {}),
            "evidence": c.get("evidence", ""),
            "policy_reasons": c.get("policy_reasons", []),
        }
        self.new_events.append(event)
        self.seen_events.add(event_id)
        if relation == "CONTRADICTS":
            self.conflicts.append(event)

    def commit(self, extra=None):
        from guards import diff_violations, state_changes

        violations = diff_violations(self._snapshot, self.records)
        if violations:
            raise RuntimeError(
                "append-only violation：这些记录的内容被改写了 → "
                + str(violations[:3])
                + "。只能新增 revision 让旧的 superseded，不能原地改。"
            )

        changes = {rel: render_records(self.prefixes[rel], self.records[rel]) for rel in self.dirty}

        # 状态流转留痕（谁、什么时候、为什么由调用方在 extra 里补充）
        moves = state_changes(self._snapshot, self.records)
        if moves:
            audit_path = safe_path(self.root, "system/state-audit.md")
            head = (
                audit_path.read_text(encoding="utf-8")
                if audit_path.exists()
                else "# 状态变更审计\n\n"
            )
            if not head.endswith("\n"):
                head += "\n"
            now = (
                __import__("datetime")
                .datetime.now(__import__("datetime").timezone.utc)
                .isoformat(timespec="seconds")
            )
            lines = "".join(
                f"- {now}  {m['file']}  {m['claim_id']}  {m['change']}: "
                f"{m['from']!r} → {m['to']!r}\n"
                for m in moves
            )
            changes["system/state-audit.md"] = head + lines
        if self.new_events:
            changes["system/version-chain.json"] = json.dumps(
                self.events + self.new_events, ensure_ascii=False, indent=2
            )
            review = safe_path(self.root, "system/review-log.md")
            before = review.read_text(encoding="utf-8") if review.exists() else "# Review log\n"
            changes["system/review-log.md"] = before + "".join(
                "\n" + json.dumps(e, ensure_ascii=False) for e in self.new_events
            )
        if self.conflicts:
            path = safe_path(self.root, "system/conflict-log.md")
            before = path.read_text(encoding="utf-8") if path.exists() else "# Conflict log\n"
            changes["system/conflict-log.md"] = before + "".join(
                "\n## conflict\n" + json.dumps(e, ensure_ascii=False) + "\n" for e in self.conflicts
            )
        changes.update(extra or {})
        txid = Transaction(self.root).commit(changes)
        self._snapshot = {rel: [dict(r) for r in recs] for rel, recs in self.records.items()}
        return txid
