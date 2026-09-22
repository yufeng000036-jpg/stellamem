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
        self.events = json.loads(self.events_path.read_text(encoding="utf-8")) if self.events_path.exists() else []
        if not isinstance(self.events, list):
            raise ValueError("invalid version chain")
        self.dirty = set()
        self.new_events = []
        self.conflicts = []
        self.counts = dict(active=0, pending=0, blocked=0, duplicate=0)

    def ingest(self, claim, mode="review", llm=None, config=None):
        if contains_secret(claim):
            self.counts["blocked"] += 1
            return
        c = apply_policy(claim)
        if c["write_policy"] == "never":
            self.counts["blocked"] += 1
            return
        cid, rev = claim_id(c), claim_revision_id(c)
        event_id = digest({"revision": rev, "provenance": c.get("provenance"),
                           "evidence": c["evidence"], "mode": mode})
        if any(e.get("event_id") == event_id for e in self.events + self.new_events):
            self.counts["duplicate"] += 1
            return
        active = [(rel, record) for rel in FILES if rel != INBOX
                  for record in self.records[rel]
                  if record.get("claim_id") == cid and record.get("status") == "active"]
        allowed = c["auto_write"] and c["confidence"] >= THRESHOLDS.get(c["kind"], 1.01) and mode == "review"
        relation = "NEW"
        old = None
        if active:
            # More than one active value is ambiguous; never silently pick one.
            if len(active) != 1:
                relation = "UNCERTAIN"
            else:
                old = active[0][1]
                relation = judge_relation(old, c, llm=llm, config=config) if allowed else "UNCERTAIN"
        if relation in {"UNCERTAIN", "CONTRADICTS", "COMPATIBLE"}:
            allowed = False
        if relation in {"DUPLICATE", "REINFORCES"} and allowed:
            self.counts["duplicate"] += 1
        else:
            if allowed and old and relation in {"SUPERSEDES", "REFINES"}:
                old["status"] = "superseded"
                old["superseded_by"] = rev
                self.dirty.add(active[0][0])
            c.update(claim_id=cid, revision_id=rev, event_id=event_id,
                     status="active" if allowed else "pending", relation=relation)
            rel = ROUTE[c["kind"]] if allowed else INBOX
            self.records[rel].append(c)
            self.dirty.add(rel)
            self.counts["active" if allowed else "pending"] += 1
        event = {"event_id": event_id, "id": cid, "current": rev,
                 "supersedes": old.get("revision_id") if old else None,
                 "relation": relation, "status": "active" if allowed else "pending",
                 "date": datetime.now(timezone.utc).isoformat(),
                 "provenance": c.get("provenance", {}),
                 "policy_reasons": c.get("policy_reasons", [])}
        self.new_events.append(event)
        if relation == "CONTRADICTS":
            self.conflicts.append(event)

    def commit(self, extra=None):
        changes = {rel: render_records(self.prefixes[rel], self.records[rel]) for rel in self.dirty}
        if self.new_events:
            changes["system/version-chain.json"] = json.dumps(self.events + self.new_events, ensure_ascii=False, indent=2)
            review = safe_path(self.root, "system/review-log.md")
            before = review.read_text(encoding="utf-8") if review.exists() else "# Review log\n"
            changes["system/review-log.md"] = before + "".join(
                "\n" + json.dumps(e, ensure_ascii=False) for e in self.new_events)
        if self.conflicts:
            path = safe_path(self.root, "system/conflict-log.md")
            before = path.read_text(encoding="utf-8") if path.exists() else "# Conflict log\n"
            changes["system/conflict-log.md"] = before + "".join(
                "\n## conflict\n" + json.dumps(e, ensure_ascii=False) + "\n" for e in self.conflicts)
        changes.update(extra or {})
        return Transaction(self.root).commit(changes)
