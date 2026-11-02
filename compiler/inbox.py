"""Minimal human approval loop for pending memories (no GUI).

Pending claims land in ``system/memory-inbox.md``. This CLI lets a person review
them and decide:

    inbox list              show pending candidates
    inbox show <id>         full record for one candidate
    inbox approve <id>      accept as-is, with provenance, into canonical memory
    inbox approve <id> --value ... --predicate ...   edit then accept
    inbox reject <id>       discard; never enters canonical memory
    inbox edit <id> --value ...    record an edit WITHOUT approving (kept pending)

Sensitive and identity candidates are never auto-approved by ingestion; approval
here is an explicit human action and is recorded in the version chain and review
log. Rejected candidates are retained in the inbox as ``rejected`` for audit.
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from claim_normalizer import ROUTE, claim_id, claim_revision_id, digest
from config_loader import load_config
from security import contains_secret
from storage import PROTECTED, Transaction, load_records, render_records, safe_path, writer_lock
from store import INBOX, MemoryStore
from cli_output import ensure_utf8_stdout
from subjects import validate_subject

EDITABLE = ("kind", "subject", "namespace", "scope", "predicate", "value")


def _load_inbox(root):
    path = safe_path(root, INBOX)
    prefix, records = load_records(path)
    return path, prefix, records


def _pending(records):
    return [r for r in records if r.get("status") == "pending"]


def _find(records, identifier):
    matches = [
        r
        for r in records
        if identifier in (r.get("claim_id"), r.get("revision_id"), r.get("event_id"))
    ]
    if not matches:
        raise ValueError("no pending candidate matches that id")
    if len(matches) > 1:
        raise ValueError("id matches multiple candidates; use the full id")
    return matches[0]


def cmd_list(root, args):
    path, _, records = _load_inbox(root)
    pending = _pending(records)
    if args.json:
        print(json.dumps(pending, ensure_ascii=False, indent=2))
        return 0
    if not pending:
        print("No pending memories.")
        return 0
    for r in pending:
        reasons = ", ".join(r.get("policy_reasons", [])) or "-"
        print(
            "{id}  [{kind}] {subject}.{predicate} = {value}".format(
                id=r.get("claim_id"),
                kind=r.get("kind"),
                subject=r.get("subject"),
                predicate=r.get("predicate"),
                value=str(r.get("value"))[:60],
            )
        )
        print("    sensitivity={s} reasons={r}".format(s=r.get("sensitivity"), r=reasons))
    print("\n" + str(len(pending)) + " pending. Use: inbox approve|reject|edit <id>")
    return 0


def cmd_show(root, args):
    _, _, records = _load_inbox(root)
    print(json.dumps(_find(records, args.id), ensure_ascii=False, indent=2))
    return 0


def _apply_edits(record, args, root):
    edits = {}
    for field in EDITABLE:
        value = getattr(args, field, None)
        if value is not None:
            edits[field] = validate_subject(value) if field == "namespace" else value
    if not edits:
        return record, None
    updated = dict(record)
    updated.update(edits)
    updated["claim_id"] = claim_id(updated)
    updated["revision_id"] = claim_revision_id(updated)
    history = list(record.get("edit_history", []))
    history.append(
        {
            "at": datetime.now(timezone.utc).isoformat(),
            "original": {k: record.get(k) for k in edits},
            "edited": edits,
            "original_claim_id": record.get("claim_id"),
            "original_revision_id": record.get("revision_id"),
        }
    )
    updated["edit_history"] = history
    return updated, history[-1]


def _commit_changes(root, changes, events):
    if events:
        chain_path = safe_path(root, "system/version-chain.json")
        chain = json.loads(chain_path.read_text(encoding="utf-8")) if chain_path.exists() else []
        changes["system/version-chain.json"] = json.dumps(
            chain + events, ensure_ascii=False, indent=2
        )
        review = safe_path(root, "system/review-log.md")
        before = review.read_text(encoding="utf-8") if review.exists() else "# Review log\n"
        changes["system/review-log.md"] = before + "".join(
            "\n" + json.dumps(e, ensure_ascii=False) for e in events
        )
    Transaction(root).commit(changes)


def cmd_approve(root, args):
    path, prefix, records = _load_inbox(root)
    record = _find(records, args.id)
    if record.get("status") != "pending":
        raise ValueError("candidate is not pending")
    edited, edit = _apply_edits(record, args, root)
    if contains_secret(edited):
        raise ValueError("candidate contains a secret and cannot be approved")
    # Core identity files are hard-protected by the transaction layer; they can
    # never be written automatically. Refuse loudly instead of consuming the
    # candidate and silently dropping the memory.
    target = ROUTE.get(edited.get("kind"), "")
    if target.replace("\\", "/") in PROTECTED:
        raise ValueError(
            """this candidate targets a protected core file (""" + target + """); """
            "approve it by editing that file manually, or reject the candidate"
        )
    # Sensitive/identity content stays require-confirmation even when approved, so
    # it never becomes injectable just because a human clicked approve.
    protected = edited.get("sensitivity") == "SENSITIVE" or edited.get("kind") in {
        "identity",
        "user-core",
    }
    store = MemoryStore(root)
    approved = dict(edited)
    approved["write_policy"] = "allow"
    approved["injection_policy"] = "confirm" if protected else "recall"
    approved["status"] = "active"
    # Attribution is a human decision, not a model guess. The extraction stage
    # cannot see speaker tags in prose diaries, so it tags nearly everything
    # "unknown"/"assistant"; without this line no approved claim can ever pass
    # the projection gate (which requires source == "user"). Callers reviewing
    # quoted or AI-authored content should pass --source to override.
    approved["source"] = args.source
    # Approval is a status transition of an existing candidate, not a new claim.
    # Writing directly avoids the pipeline's event-dedup guard, which correctly
    # rejects a second ingest of the identical event_id. Provenance, ids and the
    # route are preserved from the reviewed candidate.
    approved["claim_id"] = claim_id(approved)
    approved["revision_id"] = claim_revision_id(approved)
    approved["relation"] = approved.get("relation", "NEW")
    if store.active_by_id.get(approved["claim_id"]):
        # Approving a pending candidate is not a conflict-resolution command.
        # Preserve both versions for deliberate review; never create a second
        # active value or silently supersede a prior fact with this generic CLI.
        raise ValueError(
            "active claim already occupies this slot; explicit conflict resolution required"
        )
    target = ROUTE.get(approved.get("kind"), INBOX)
    if target.replace("\\", "/") in PROTECTED:
        raise ValueError("refusing to write a protected core file")
    store.records[target].append(approved)
    store.active_by_id.setdefault(approved["claim_id"], []).append((target, approved))
    store.dirty.add(target)
    store.counts["active"] += 1
    # Remove the candidate from the pending inbox and record the decision.
    records.remove(record)
    decided = dict(edited)
    decided["status"] = "approved"
    decided["approved_at"] = datetime.now(timezone.utc).isoformat()
    decided_record = decided
    events = [
        {
            "event_id": digest(
                {
                    "inbox": decided_record.get("claim_id"),
                    "action": "approve",
                    "at": decided_record["approved_at"],
                }
            ),
            "id": decided_record.get("claim_id"),
            "current": decided_record.get("revision_id"),
            "relation": "APPROVED",
            "status": "active",
            "date": decided_record["approved_at"],
            "provenance": decided_record.get("provenance", {}),
            "evidence": decided_record.get("evidence", ""),
            "review_action": "approve",
            "edited": bool(edit),
            "namespace": approved.get("namespace"),
            "namespace_before_review": record.get("namespace"),
        }
    ]
    store.dirty.add(INBOX)
    store.records[INBOX] = records
    # Record the approval in the version chain and review log exactly like reject.
    store.new_events.extend(events)
    store.seen_events.update(e["event_id"] for e in events)
    changes = {INBOX: render_records(prefix, records)}
    txid = store.commit(changes)
    print(
        json.dumps(
            {
                "approved": decided_record.get("claim_id"),
                "transaction": txid,
                "edited": bool(edit),
                "source": approved.get("source"),
            }
        )
    )
    return 0


def cmd_reject(root, args):
    path, prefix, records = _load_inbox(root)
    record = _find(records, args.id)
    if record.get("status") != "pending":
        raise ValueError("candidate is not pending")
    record["status"] = "rejected"
    record["rejected_at"] = datetime.now(timezone.utc).isoformat()
    if getattr(args, "reason", None):
        record["reject_reason"] = args.reason
    changes = {INBOX: render_records(prefix, records)}
    events = [
        {
            "event_id": digest(
                {"inbox": record.get("claim_id"), "action": "reject", "at": record["rejected_at"]}
            ),
            "id": record.get("claim_id"),
            "current": record.get("revision_id"),
            "relation": "REJECTED",
            "status": "rejected",
            "date": record["rejected_at"],
            "provenance": record.get("provenance", {}),
            "evidence": record.get("evidence", ""),
            "review_action": "reject",
            "reason": record.get("reject_reason", ""),
        }
    ]
    _commit_changes(root, changes, events)
    print(json.dumps({"rejected": record.get("claim_id")}))
    return 0


def cmd_edit(root, args):
    path, prefix, records = _load_inbox(root)
    record = _find(records, args.id)
    if record.get("status") != "pending":
        raise ValueError("candidate is not pending")
    updated, edit = _apply_edits(record, args, root)
    if edit is None:
        raise ValueError(
            "no fields to edit; pass --value/--predicate/--subject/--namespace/--kind/--scope"
        )
    if contains_secret(updated):
        raise ValueError("edit introduces a secret; refused")
    records[records.index(record)] = updated
    _commit_changes(root, {INBOX: render_records(prefix, records)}, [])
    print(json.dumps({"edited": updated.get("claim_id"), "still_pending": True}))
    return 0


def add_edit_flags(parser):
    parser.add_argument("--kind")
    parser.add_argument("--subject")
    parser.add_argument(
        "--namespace",
        choices=("user", "persona", "viewer"),
        help="explicit human classification; keeps the topic/person subject unchanged",
    )
    parser.add_argument("--scope")
    parser.add_argument("--predicate")
    parser.add_argument("--value")


def main(argv=None):
    ensure_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    lst = sub.add_parser("list")
    lst.add_argument("--json", action="store_true")
    show = sub.add_parser("show")
    show.add_argument("id")
    approve = sub.add_parser("approve")
    approve.add_argument("id")
    approve.add_argument(
        "--source",
        choices=("user", "assistant", "quoted"),
        default="user",
        help=(
            "who this memory is attributed to; human approval defaults to the "
            "user, override for AI-authored or quoted content"
        ),
    )
    add_edit_flags(approve)
    reject = sub.add_parser("reject")
    reject.add_argument("id")
    reject.add_argument("--reason")
    edit = sub.add_parser("edit")
    edit.add_argument("id")
    add_edit_flags(edit)
    args = parser.parse_args(argv)
    try:
        root = Path(load_config()["memories_root"])
        with writer_lock(root):
            Transaction(root).recover()
            return {
                "list": cmd_list,
                "show": cmd_show,
                "approve": cmd_approve,
                "reject": cmd_reject,
                "edit": cmd_edit,
            }[args.command](root, args)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        print("PROBLEM: inbox operation failed (" + type(exc).__name__ + ")")
        print(
            "CAUSE: " + str(exc)
            if isinstance(exc, ValueError)
            else "CAUSE: local file or config error"
        )
        print("HOW TO FIX: run `inbox list` and verify the candidate id and config.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
