"""Minimal human approval loop for pending memories (no GUI).

Pending claims land in ``system/memory-inbox.md``. This CLI lets a person review
them and decide:

    inbox list              show pending candidates
    inbox show <id>         full record for one candidate
    inbox approve <id>      accept as-is, with provenance, into canonical memory
    inbox approve <id> --value ... --predicate ...   edit then accept
    inbox reject <id>       discard; never enters canonical memory
    inbox edit <id> --value ...    record an edit WITHOUT approving (kept pending)
    inbox auto-approve [--dry-run]
                            approve clean user-subject candidates whose grace
                            period has passed with no human action
    inbox withdraw <id>     return an auto-approved record to pending

Manual review stays available at any time: ``approve`` / ``reject`` / ``edit``
keep working on pending items, before and after any auto-approval run. The
time-based auto-approval is deliberately narrow: only ``user``-subject records
that are clean (verified evidence, PRIVATE, relation NEW, no secret, no
protected target, no occupied slot) and were stamped with ``pending_since`` at
ingest can auto-approve, and only after ``pending_auto_approve_hours`` (default
72) have passed with no human action. Sensitive, identity, contradictory,
unverified and pre-feature backlog records never auto-approve; they stay
manual. Every auto-approval is written to the version chain and review log
with ``review_action: "auto-approve"`` and can be undone with ``withdraw``,
which returns the record to pending and clears its auto-approval clock, so a
withdrawn record never auto-approves again.
"""

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from claim_normalizer import ROUTE, claim_id, claim_revision_id, digest
from config_loader import load_config
from security import contains_secret
from storage import PROTECTED, Transaction, load_records, render_records, safe_path, writer_lock
from store import INBOX, MemoryStore
from cli_output import ensure_utf8_stdout
from subjects import USER, subject_of, validate_subject

EDITABLE = ("kind", "subject", "namespace", "scope", "predicate", "value")

# Kinds that never auto-approve. The protected-target check below covers the
# same ground via ROUTE; this is the explicit fail-closed list.
AUTO_APPROVE_BLOCKED_KINDS = frozenset({"identity", "user-core"})

DEFAULT_AUTO_APPROVE_HOURS = 72


def auto_approve_settings(config):
    """(enabled, grace_hours) for pending auto-approval from a loaded config.

    Validation lives in ``load_config``; this only applies the defaults so
    callers that already hold a config dict do not re-read the file.
    """
    if not isinstance(config, dict):
        return True, DEFAULT_AUTO_APPROVE_HOURS
    return (
        config.get("pending_auto_approve_enabled", True),
        config.get("pending_auto_approve_hours", DEFAULT_AUTO_APPROVE_HOURS),
    )


def _parse_pending_since(value):
    """Parse a ``pending_since`` stamp to an aware datetime, or None.

    Anything unparsable -- or naive, with no timezone -- fails closed: the
    record is treated as having no usable stamp and never auto-approves.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def auto_approve_eligible(record, store, grace_hours, now, duplicate_keys=None):
    """Decide whether one pending record may auto-approve.

    Returns ``(True, "")`` or ``(False, reason)``. Fail closed: any doubt --
    missing stamp, wrong subject, sensitive content, unverified evidence,
    non-NEW relation, occupied slot -- stays pending for a human.
    ``duplicate_keys`` is the set of (claim_id, revision_id) pairs that occur
    more than once in the inbox: approving one of two identical rows would
    trip the append-only guard (the surviving twin changes the count), so
    those rows stay manual, like a manual approval of one twin would fail.
    """
    if record.get("status") != "pending":
        return False, "not pending"
    since = _parse_pending_since(record.get("pending_since"))
    if since is None:
        return False, "no pending_since: landed before auto-approval existed, manual only"
    if now - since < timedelta(hours=grace_hours):
        return False, "grace period not elapsed"
    if (
        duplicate_keys is not None
        and (record.get("claim_id"), record.get("revision_id")) in duplicate_keys
    ):
        return False, "duplicate inbox rows share this id; resolve manually"
    if subject_of(record) != USER:
        return False, "not a user-subject record"
    kind = record.get("kind")
    if kind not in ROUTE:
        return False, "unknown kind"
    if kind in AUTO_APPROVE_BLOCKED_KINDS:
        return False, "core identity never auto-approves"
    if record.get("sensitivity") != "PRIVATE":
        return False, "sensitivity is not PRIVATE"
    if str(record.get("source") or "") != "user":
        return False, "source is not the user"
    if not record.get("evidence") or record.get("evidence_verified") is not True:
        return False, "evidence not verified"
    # Literal evidence matching proves provenance, not semantic entailment.
    # Only exact quotations may pass unattended; paraphrases need human review.
    value = record.get("value")
    evidence = record.get("evidence")
    if (
        not isinstance(value, str)
        or not isinstance(evidence, str)
        or value.strip() != evidence.strip()
    ):
        return False, "value is not an exact evidence quote; manual review required"
    if record.get("relation") != "NEW":
        return False, "relation is not NEW"
    if record.get("write_policy") != "allow":
        return False, "write_policy is not allow"
    if contains_secret(record):
        return False, "contains a secret"
    if ROUTE[kind].replace("\\", "/") in PROTECTED:
        return False, "targets a protected core file"
    if store is not None and store.active_by_id.get(record.get("claim_id")):
        return False, "active claim already occupies this slot"
    return True, ""


def _duplicate_keys(records):
    """(claim_id, revision_id) pairs occurring more than once in ``records``."""
    from collections import Counter

    counts = Counter((r.get("claim_id"), r.get("revision_id")) for r in records)
    return {key for key, count in counts.items() if count > 1}


def _auto_approve_status(record, store, enabled, grace_hours, now, duplicate_keys=None):
    """One-line eligibility summary for ``inbox list``.

    Returns ``(eligible, text)`` where text is ``eligible``, ``in <n>h`` while
    the grace period is still running, or ``never: <reason>``.
    """
    if not enabled:
        return False, "never: auto-approval disabled in config"
    eligible, reason = auto_approve_eligible(record, store, grace_hours, now, duplicate_keys)
    if eligible:
        return True, "eligible"
    since = _parse_pending_since(record.get("pending_since"))
    if since is not None and now - since < timedelta(hours=grace_hours):
        remaining = timedelta(hours=grace_hours) - (now - since)
        hours = int(remaining.total_seconds() // 3600) + 1
        return False, "in {n}h".format(n=hours)
    return False, "never: " + reason


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
    config = getattr(args, "config", None)
    enabled, hours = auto_approve_settings(config)
    path, _, records = _load_inbox(root)
    pending = _pending(records)
    store = MemoryStore(root)
    now = datetime.now(timezone.utc)
    duplicate_keys = _duplicate_keys(pending)
    if args.json:
        out = []
        for r in pending:
            item = dict(r)
            eligible, status = _auto_approve_status(r, store, enabled, hours, now, duplicate_keys)
            since = _parse_pending_since(r.get("pending_since"))
            item["auto_approve"] = {
                "eligible": eligible,
                "status": status,
                "eligible_at": (since + timedelta(hours=hours)).isoformat()
                if since is not None
                else None,
            }
            out.append(item)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    if not pending:
        print("No pending memories.")
        return 0
    for r in pending:
        reasons = ", ".join(r.get("policy_reasons", [])) or "-"
        _, status = _auto_approve_status(r, store, enabled, hours, now, duplicate_keys)
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
        print(
            "    pending_since={ps} auto_approve={st}".format(
                ps=r.get("pending_since") or "-", st=status
            )
        )
    print(
        "\n"
        + str(len(pending))
        + " pending. Use: inbox approve|reject|edit <id> (or auto-approve to sweep "
        "grace-expired clean claims)"
    )
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
    # A human can move a pending topic into an already occupied namespace/slot.
    # Recheck after edits so projection suppresses the unresolved old value.
    # This only records uncertainty; it never chooses or overwrites a version.
    active = MemoryStore(root).active_by_id.get(updated["claim_id"], [])
    if active:
        updated["relation_before_edit"] = record.get("relation")
        updated["relation"] = "UNCERTAIN"
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


def _approve_record(store, records, record, edited, *, source, approved_via, hours, now):
    """Shared approval mechanics for manual and auto approval.

    Moves one pending record into its canonical route file and records the
    decision in the version chain / review log. ``record`` is the member of
    ``records`` to remove; ``edited`` is the record to write (identical to
    ``record`` for auto-approval, possibly a human-edited copy for manual
    approval). Raises ``ValueError`` on the same guards manual approval has
    always enforced (secret, protected core target, occupied slot). Mutates
    ``store`` and ``records``; the caller commits. ``approved_via`` is None
    for a human approval (no new fields on the record, behaviour unchanged)
    and ``"auto"`` for an auto-approval (marks the record
    ``approved_via: "auto"`` so withdrawal and auditing can tell it apart).
    ``hours`` is the grace period that authorised an auto approval and is
    recorded in the event; it is ignored for manual approval.
    """
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
    approved = dict(edited)
    approved["write_policy"] = "allow"
    approved["injection_policy"] = "confirm" if protected else "recall"
    approved["status"] = "active"
    # Attribution is a human decision, not a model guess. The extraction stage
    # cannot see speaker tags in prose diaries, so it tags nearly everything
    # "unknown"/"assistant"; without this line no approved claim can ever pass
    # the projection gate (which requires source == "user"). Callers reviewing
    # quoted or AI-authored content should pass --source to override.
    approved["source"] = source
    if approved_via is not None:
        approved["approved_via"] = approved_via
        approved["auto_approved_at"] = now.isoformat()
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
    decided["approved_at"] = now.isoformat()
    decided_record = decided
    review_action = "auto-approve" if approved_via == "auto" else "approve"
    event = {
        "event_id": digest(
            {
                "inbox": decided_record.get("claim_id"),
                "action": review_action,
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
        "review_action": review_action,
        "edited": False,
        "namespace": approved.get("namespace"),
        "namespace_before_review": record.get("namespace"),
    }
    if approved_via == "auto":
        event["approved_via"] = "auto"
        event["rule"] = "pending_auto_approve_hours={h}".format(h=hours)
        event["pending_since"] = record.get("pending_since")
    store.dirty.add(INBOX)
    store.records[INBOX] = records
    # Record the approval in the version chain and review log exactly like reject.
    store.new_events.append(event)
    store.seen_events.add(event["event_id"])
    return approved, decided_record, event


def cmd_approve(root, args):
    path, prefix, records = _load_inbox(root)
    record = _find(records, args.id)
    if record.get("status") != "pending":
        raise ValueError("candidate is not pending")
    edited, edit = _apply_edits(record, args, root)
    store = MemoryStore(root)
    now = datetime.now(timezone.utc)
    approved, decided_record, event = _approve_record(
        store,
        records,
        record,
        edited,
        source=args.source,
        approved_via=None,
        hours=None,
        now=now,
    )
    if edit is not None:
        # _apply_edits produced a new revision; the event records that the
        # approval carried an inline edit, as it always has.
        event["edited"] = True
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


def cmd_auto_approve(root, args):
    """Sweep grace-expired clean pending claims into canonical memory.

    One transaction for the whole sweep; a second sweep changes nothing
    (approved records are gone from the inbox, skipped ones are still
    ineligible). ``--dry-run`` reports what would happen and writes nothing.
    When auto-approval is disabled in config this is a no-op that reports so.
    """
    config = getattr(args, "config", None) or load_config()
    enabled, hours = auto_approve_settings(config)
    now = datetime.now(timezone.utc)
    path, prefix, records = _load_inbox(root)
    pending = _pending(records)
    store = MemoryStore(root)
    duplicate_keys = _duplicate_keys(pending)
    approved_ids = []
    skipped = []
    if not enabled:
        print(
            json.dumps(
                {
                    "enabled": False,
                    "grace_hours": hours,
                    "checked": len(pending),
                    "approved": [],
                    "skipped": [],
                    "dry_run": bool(args.dry_run),
                    "transaction": None,
                },
                ensure_ascii=False,
            )
        )
        return 0
    for record in list(pending):
        eligible, reason = auto_approve_eligible(record, store, hours, now, duplicate_keys)
        if not eligible:
            skipped.append({"id": record.get("claim_id"), "reason": reason})
            continue
        if args.dry_run:
            approved_ids.append(record.get("claim_id"))
            continue
        try:
            _approve_record(
                store,
                records,
                record,
                record,
                source="user",
                approved_via="auto",
                hours=hours,
                now=now,
            )
        except ValueError as exc:
            skipped.append({"id": record.get("claim_id"), "reason": str(exc)})
            continue
        approved_ids.append(record.get("claim_id"))
    txid = None
    if approved_ids and not args.dry_run:
        changes = {INBOX: render_records(prefix, records)}
        txid = store.commit(changes)
    print(
        json.dumps(
            {
                "enabled": True,
                "grace_hours": hours,
                "checked": len(pending),
                "approved": approved_ids,
                "skipped": skipped,
                "dry_run": bool(args.dry_run),
                "transaction": txid,
            },
            ensure_ascii=False,
        )
    )
    return 0


def cmd_withdraw(root, args):
    """Return an auto-approved record to pending.

    Only records marked ``approved_via: "auto"`` can be withdrawn: a human
    approval is a deliberate decision, not something a generic command should
    silently undo. Withdrawing clears ``pending_since``, so the record never
    auto-approves again -- the withdrawal is a veto, and only a human can
    approve the record afterwards. The move is a new state transition in the
    append-only log (new version-chain event, ``review_action: "withdraw"``),
    never an in-place rewrite.
    """
    store = MemoryStore(root)
    now = datetime.now(timezone.utc).isoformat()
    matches = []
    for rel, recs in store.records.items():
        if rel == INBOX:
            continue
        for r in recs:
            if args.id in (r.get("claim_id"), r.get("revision_id"), r.get("event_id")):
                matches.append((rel, r))
    if not matches:
        raise ValueError("no active record matches that id")
    if len(matches) > 1:
        raise ValueError("id matches multiple records; use the full id")
    target_rel, target = matches[0]
    if target.get("status") != "active" or target.get("approved_via") != "auto":
        raise ValueError(
            "only auto-approved records can be withdrawn; "
            "manually approved records need explicit conflict resolution"
        )
    pending = dict(target)
    pending["status"] = "pending"
    pending.pop("approved_via", None)
    pending.pop("auto_approved_at", None)
    # Veto: without a stamp the record can never auto-approve again.
    pending.pop("pending_since", None)
    pending["withdrawn_at"] = now
    store.records[target_rel].remove(target)
    store.dirty.add(target_rel)
    store.records[INBOX].append(pending)
    store.dirty.add(INBOX)
    event = {
        "event_id": digest({"inbox": pending.get("claim_id"), "action": "withdraw", "at": now}),
        "id": pending.get("claim_id"),
        "current": pending.get("revision_id"),
        "relation": "WITHDRAWN",
        "status": "pending",
        "date": now,
        "provenance": pending.get("provenance", {}),
        "evidence": pending.get("evidence", ""),
        "review_action": "withdraw",
        "approved_via": "auto",
        "namespace": pending.get("namespace"),
    }
    store.new_events.append(event)
    store.seen_events.add(event["event_id"])
    txid = store.commit()
    print(
        json.dumps(
            {
                "withdrawn": pending.get("claim_id"),
                "transaction": txid,
                "back_to_pending": True,
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
    auto = sub.add_parser(
        "auto-approve",
        help=(
            "approve clean user-subject pending claims whose grace period "
            "(pending_auto_approve_hours, default 72) has passed"
        ),
    )
    auto.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be approved without writing anything",
    )
    withdraw = sub.add_parser(
        "withdraw",
        help="return an auto-approved record to pending (never auto-approves again)",
    )
    withdraw.add_argument("id")
    reject = sub.add_parser("reject")
    reject.add_argument("id")
    reject.add_argument("--reason")
    edit = sub.add_parser("edit")
    edit.add_argument("id")
    add_edit_flags(edit)
    args = parser.parse_args(argv)
    try:
        config = load_config()
        args.config = config
        root = Path(config["memories_root"])
        with writer_lock(root):
            Transaction(root).recover()
            return {
                "list": cmd_list,
                "show": cmd_show,
                "approve": cmd_approve,
                "auto-approve": cmd_auto_approve,
                "withdraw": cmd_withdraw,
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
