"""Guardrail receipts: every guardrail decision leaves a checkable ticket.

皮皮虾's suggestion (EigenFlux, 2026-11-07): split irreversible actions
from everyday reads/writes, let low-risk daily traffic through, and put
a guardrail on the risky openings. The extra step is that every
guardrail decision must produce a receipt with three parts:

1. ``guardrail_id`` — which guardrail decided (stable id, e.g.
   ``"injection-gate"``);
2. ``decision`` — the decision class, one of ``allow`` / ``block`` /
   ``partial`` / ``defer`` (a small closed vocabulary, not free text);
3. ``rule_digest`` — a digest of the rule summary, so the receipt can be
   checked against the rule that produced it. The digest binds the
   guardrail id, the rule (version, summary, inputs) AND the decision:
   relabelling a ``block`` as an ``allow`` breaks it just as surely as
   editing the rule does.

The digest is machine-verifiable by construction, which answers the open
question from that conversation (is the digest verifiable, is there a
replay check): ``verify_receipt`` recomputes the digest from the
receipt's own fields — no external input needed — and a replay is just
re-running the decision with the same inputs and comparing receipts.
The receipt also records the rule inputs (thresholds, rule version)
transparently, so a digest can never hide behind an undocumented rule:
bump ``rule_version`` when the rule changes and the digest changes with
it, so a receipt from an old rule cannot pass as a new-rule decision.

A receipt is a record of a decision, not a permission: producing one
never authorizes anything, and verifying one never replays the action.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

DECISION_CLASSES = ("allow", "block", "partial", "defer")


def _canonical(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(payload: dict) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def make_receipt(
    guardrail_id: str,
    decision: str,
    rule_summary: str,
    rule_inputs: dict,
    detail: dict | None = None,
    at: str | None = None,
) -> dict:
    """Build a receipt for one guardrail decision.

    ``rule_inputs`` are the rule's identifying parameters (thresholds,
    rule version, flags) — recorded transparently in the receipt.
    ``detail`` is free-form per-guardrail evidence (counts, ids).
    """
    if not guardrail_id or not str(guardrail_id).strip():
        raise ValueError("guardrail_id is required")
    if decision not in DECISION_CLASSES:
        raise ValueError(
            f"unknown decision class: {decision!r} (expected one of {DECISION_CLASSES})"
        )
    if not rule_summary or not str(rule_summary).strip():
        raise ValueError("rule_summary is required")
    rule_version = rule_inputs.get("rule_version", 1)
    inputs = dict(rule_inputs)
    inputs["rule_version"] = rule_version
    stamp = at or datetime.now(timezone.utc).isoformat(timespec="microseconds")
    digest_payload = {
        "guardrail_id": guardrail_id,
        "rule_version": rule_version,
        "rule_summary": str(rule_summary).strip(),
        "rule_inputs": inputs,
        "decision": decision,
    }
    rule_digest = _digest(digest_payload)
    receipt = {
        "receipt_id": "gr-" + _digest({**digest_payload, "at": stamp, "decision": decision})[:16],
        "at": stamp,
        "guardrail_id": str(guardrail_id).strip(),
        "decision": decision,
        "rule_version": rule_version,
        "rule_summary": str(rule_summary).strip(),
        "rule_inputs": inputs,
        "rule_digest": rule_digest,
        "detail": dict(detail or {}),
    }
    return receipt


def verify_receipt(receipt: dict) -> bool:
    """True when the receipt's digest matches its own recorded rule.

    Recomputes the digest from the receipt's guardrail id, rule version,
    rule summary and rule inputs, and checks the decision class and the
    required fields. A tampered decision, summary, input or digest fails.
    This proves the receipt belongs to the rule it cites; it does not
    prove the rule's inputs are the ones actually in force — that is
    what a replay (re-running the decision and comparing receipts) is
    for.
    """
    try:
        if not isinstance(receipt, dict):
            return False
        if receipt.get("decision") not in DECISION_CLASSES:
            return False
        for key in ("guardrail_id", "rule_summary", "rule_inputs", "rule_digest", "receipt_id"):
            if not receipt.get(key):
                return False
        if receipt.get("rule_version") != receipt["rule_inputs"].get("rule_version"):
            return False
        digest_payload = {
            "guardrail_id": receipt["guardrail_id"],
            "rule_version": receipt["rule_version"],
            "rule_summary": receipt["rule_summary"],
            "rule_inputs": receipt["rule_inputs"],
            "decision": receipt["decision"],
        }
        return _digest(digest_payload) == receipt["rule_digest"]
    except (TypeError, AttributeError, KeyError):
        return False
