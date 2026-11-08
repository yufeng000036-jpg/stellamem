"""Autonomy levels L1–L4 + permission-boundary triple (LAAP, minimal).

Adapted from 太极's LAAP description on EigenFlux (2026-11-07):
autonomy is graded L1–L4; the permission boundary is a triple
(capability set + resource budget + rollback/revocation); a level-up
passes positive AND negative fault-injection acceptance, recorded in
the receipt ledger. (太极's concrete locations — actions.jsonl,
desire_arbitration.py, receipt ledger as rule cards + append-only
actions chain, smoke.py L4 injection tests — were shared as file
paths; the arbitration core and L4 test snippets were requested and
are still awaited. This module does NOT claim to have read that code:
it adapts the described pattern to Stellamem's own guardrail receipt.)

Levels (what the memory system — and its host agent — may do without
a human in the loop):

- L1 read-only perception: recall, search, read records, trace a
  deletion chain, generate the tracking table, run the nightly
  settlement report. Nothing is written.
- L2 suggest / draft: propose writes a human approves — ingest to the
  pending inbox, create a bridge request, mark decay candidates,
  trace a deletion, draft a demotion list. The proposal is the whole
  action; the write itself is someone else's decision.
- L3 conditional autonomous execution: execute a pre-approved, bounded
  action automatically — auto-approve after the 72h grace (PR #80),
  demote 90-day-no-hit records (PR #87), write the nightly settlement
  log + baseline. Each L3 action has explicit conditions (its
  eligibility rules) AND a rollback (withdraw, restore status, logs
  are append-only so nothing is silently undone).
- L4 entrusted autonomy: full delegation within a written charter —
  e.g. the user's standing delegation of the Bilibili / Douyin
  accounts, or a brief the user ordered executed. Stellamem never
  grants L4 to itself; the USER grants it, in writing, with a scope
  and a revocation path. The module records the grant; it does not
  invent one.

The permission boundary is a triple: the capability set (which
actions this level may run), the resource budget (how much it may
spend — records per run, cost), and rollback/revocation (how an
action is undone, how the grant is revoked). A level without all
three is not a level, it is a wish.

Minimal implementation, deliberately: this module classifies actions,
checks them against a level + boundary, and records level-ups. It
does NOT enforce at call sites — wiring ``check()`` into the MCP
server / CLI is a follow-up, not this change (no full refactor).
What it does enforce: a level-up is refused unless BOTH fault-
injection acceptances are supplied (a positive case that must pass
and a negative case that must fail). The acceptances themselves are
tests (``tests/test_autonomy.py``); the ledger they are recorded in
is the guardrail receipt (PR #86).
"""

from __future__ import annotations

from dataclasses import dataclass, field

try:
    from .guardrail_receipt import make_receipt
except Exception:  # pragma: no cover
    try:
        from guardrail_receipt import make_receipt  # type: ignore
    except Exception:
        make_receipt = None

GUARDRAIL_ID = "autonomy"
RULE_VERSION = 1

LEVELS = {
    1: {
        "name": "read-only perception",
        "description": "Read, recall, trace, report. Nothing is written.",
    },
    2: {
        "name": "suggest / draft",
        "description": "Propose writes a human approves. The proposal is the action.",
    },
    3: {
        "name": "conditional autonomous execution",
        "description": "Execute pre-approved, bounded actions automatically, with rollback.",
    },
    4: {
        "name": "entrusted autonomy",
        "description": "Full delegation within a written charter granted by the user.",
    },
}

# Concrete Stellamem actions and the lowest level that may run them.
# A higher level may run everything a lower level may.
ACTION_LEVELS = {
    # L1
    "recall": 1,
    "search": 1,
    "read_record": 1,
    "trace_deletion": 1,
    "tracking_table": 1,
    "nightly_settlement_report": 1,
    # L2
    "propose_ingest": 2,
    "propose_bridge": 2,
    "mark_decayed": 2,
    "draft_demotion": 2,
    # L3
    "auto_approve": 3,
    "demote": 3,
    "nightly_settlement_write": 3,
    "forget_record": 3,
    # L4 (charter only; see level_up / grant)
    "operate_account": 4,
    "execute_brief": 4,
}


@dataclass(frozen=True)
class Boundary:
    """The permission-boundary triple for one level grant.

    capabilities: the actions this grant may run (a subset of
        ACTION_LEVELS; anything not listed is denied).
    budget: resource limits, e.g. {"max_records_per_run": 100,
        "max_cost_usd": 2.0}. Empty means "no stated budget" — which
        is only acceptable at L1/L2 (reads and proposals cost little);
        L3/L4 grants should always state one.
    rollback: how an executed action is undone, in words
        (e.g. "inbox withdraw", "status back to active").
    revocation: how the grant itself is revoked, in words
        (e.g. "config pending_auto_approve_enabled=false",
        "user says stop").
    """

    capabilities: frozenset = frozenset()
    budget: dict = field(default_factory=dict)
    rollback: str = ""
    revocation: str = ""


def level_name(level: int) -> str:
    if level not in LEVELS:
        raise ValueError(f"unknown autonomy level: {level!r} (expected 1-4)")
    return LEVELS[level]["name"]


def required_level(action: str) -> int:
    if action not in ACTION_LEVELS:
        raise ValueError(f"unknown action: {action!r} (not in ACTION_LEVELS)")
    return ACTION_LEVELS[action]


def _receipt(decision: str, summary: str, detail: dict) -> dict | None:
    if make_receipt is None:
        return None
    return make_receipt(
        GUARDRAIL_ID,
        decision,
        summary,
        {"rule_version": RULE_VERSION},
        detail=detail,
    )


def check(level: int, action: str, boundary: Boundary | None = None) -> dict:
    """Check whether `action` may run at `level` under `boundary`.

    Returns {"allowed": bool, "reason": str, "receipt": dict|None}.
    Deny reasons, in order: unknown level / unknown action / level too
    low / action outside the boundary's capability set / L3-L4 grant
    without rollback or revocation stated. The receipt records the
    check itself (guardrail_id "autonomy").
    """
    try:
        need = required_level(action)
        name = level_name(level)
    except ValueError as exc:
        return {
            "allowed": False,
            "reason": str(exc),
            "receipt": _receipt(
                "block",
                "autonomy check: invalid level or action",
                {"level": level, "action": action},
            ),
        }
    if level < need:
        return {
            "allowed": False,
            "reason": f"{action} requires L{need} ({LEVELS[need]['name']}), not L{level}",
            "receipt": _receipt(
                "block",
                f"autonomy L{level} check: level too low",
                {"level": level, "action": action, "required": need},
            ),
        }
    if boundary is not None:
        if action not in boundary.capabilities:
            return {
                "allowed": False,
                "reason": f"{action} is outside this grant's capability set",
                "receipt": _receipt(
                    "block",
                    "autonomy check: outside capability set",
                    {"level": level, "action": action},
                ),
            }
        if level >= 3 and (not boundary.rollback or not boundary.revocation):
            return {
                "allowed": False,
                "reason": "L3/L4 grants must state rollback and revocation",
                "receipt": _receipt(
                    "block",
                    "autonomy check: boundary triple incomplete",
                    {"level": level, "action": action},
                ),
            }
    return {
        "allowed": True,
        "reason": f"L{level} ({name}) may run {action}",
        "receipt": _receipt(
            "allow",
            f"autonomy L{level} check: allowed",
            {"level": level, "action": action, "required": need},
        ),
    }


def level_up(
    from_level: int, to_level: int, *, positive_passed: bool, negative_passed: bool
) -> dict:
    """Record a level-up decision. Refused unless BOTH acceptances pass.

    ``positive_passed``: the fault-injection positive case ran and the
    action behaved (e.g. an in-conditions L3 action executed).
    ``negative_passed``: the fault-injection negative case ran and the
    action was refused (e.g. the same action out of conditions, or an
    L2 actor attempting it, was denied). One without the other is not
    an acceptance: a positive alone proves nothing about the boundary,
    a negative alone proves nothing about the capability.
    Returns {"granted": bool, "reason": str, "receipt": dict|None}.
    """
    level_name(from_level)
    level_name(to_level)
    if to_level <= from_level:
        return {
            "granted": False,
            "reason": f"level-up requires to_level > from_level, got L{from_level} -> L{to_level}",
            "receipt": _receipt(
                "block", "autonomy level-up: not an upgrade", {"from": from_level, "to": to_level}
            ),
        }
    if not positive_passed or not negative_passed:
        missing = [
            name
            for name, passed in (("positive", positive_passed), ("negative", negative_passed))
            if not passed
        ]
        return {
            "granted": False,
            "reason": f"level-up refused: fault-injection acceptance missing: {', '.join(missing)}",
            "receipt": _receipt(
                "block",
                "autonomy level-up: acceptance incomplete",
                {
                    "from": from_level,
                    "to": to_level,
                    "positive_passed": positive_passed,
                    "negative_passed": negative_passed,
                },
            ),
        }
    return {
        "granted": True,
        "reason": f"L{from_level} -> L{to_level} granted (both acceptances passed)",
        "receipt": _receipt(
            "allow",
            "autonomy level-up: granted",
            {"from": from_level, "to": to_level, "positive_passed": True, "negative_passed": True},
        ),
    }
