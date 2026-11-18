"""Deterministic policy; never interpret importance as write permission."""

import json
import re
from pathlib import Path

RULES = json.loads(
    (Path(__file__).resolve().parents[1] / "shared" / "policy-rules.json").read_text(
        encoding="utf-8"
    )
)


def matches(group, text):
    return any(re.search(pattern, str(text), re.I) for pattern in RULES[group])


def contains_secret(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return matches("secret", text)


def redact(text):
    result = str(text)
    for pattern in RULES["secret"]:
        result = re.sub(pattern, "[REDACTED]", result, flags=re.I)
    return result


def detect_injection(text):
    return matches("injection", text)


def detect_heavy_emotion(text):
    return matches("heavy", text)


def detect_temporary(text):
    return matches("temporary", text)


def detect_temporary_intent(text):
    return matches("temporary_intent", text)


def detect_past_anchor(text):
    return matches("past_anchor", text)


def correct_temporary_flag(claim, message_text=""):
    """Deterministically correct a model-supplied ``temporary`` flag.

    The extraction prompt lists ``temporary`` as an output field but defines
    it nowhere, so the model applies it to any time-bound content at all.
    Round-4 LongMemEval attribution (2026-11-18) decomposed the 1,115 gold
    claims dropped as ``ineligible:temporary-flag``: 1,112 carried the flag
    from the model itself, 2 from the source-message rule match, 0 from the
    claim-text rules — and 92% of all temporary-flagged active claims were
    gold. The system's own definition of temporary is the ``temporary``
    rule group (transient states of mind); a plan stated in the past and an
    event that already happened are not it, yet the model flags both.

    The correction is one-directional and fail-closed. It runs only in the
    extraction pipeline, where the claim's evidence has been located in a
    specific source message, and it clears the flag only when ALL hold:

    - neither the claim text nor that source message matches the system's
      own ``temporary`` rules;
    - the claim carries no plan / hypothesis / unsettled marker
      (``temporary_intent``) — plans and not-yet-happened content keep the
      flag even when a date is mentioned;
    - the claim carries a positive past anchor (``past_anchor``): a
      completed first-person report, an explicit past date, or a
      started-in-the-past perfect form.

    Anything ambiguous keeps the flag. A correction is recorded on the
    claim as ``temporary_correction`` and policy is recomputed, so the
    "temporary" review reason disappears with the flag. Returns
    ``(claim, corrected)``.
    """
    c = dict(claim)
    if not c.get("temporary"):
        return c, False
    text = " ".join(
        str(c.get(k, "")) for k in ("kind", "subject", "predicate", "value", "evidence")
    )
    if detect_temporary(text) or (message_text and detect_temporary(message_text)):
        return c, False
    if detect_temporary_intent(text):
        return c, False
    if not detect_past_anchor(text):
        return c, False
    c["temporary"] = False
    c["temporary_correction"] = "past-anchor-v1"
    return apply_policy(c), True


def detect_sarcasm(text):
    return matches("sarcasm", text)


def detect_outdated(text):
    return matches("outdated", text)


def apply_policy(claim):
    c = dict(claim)
    text = " ".join(
        str(c.get(k, "")) for k in ("kind", "subject", "predicate", "value", "evidence")
    )
    secret = contains_secret(c)
    c["suspicious"] = bool(c.get("suspicious")) or detect_injection(text)
    c["heavy_emotion"] = bool(c.get("heavy_emotion")) or detect_heavy_emotion(text)
    c["temporary"] = bool(c.get("temporary")) or detect_temporary(text)
    c["sarcasm"] = bool(c.get("sarcasm")) or detect_sarcasm(text)
    c["outdated"] = bool(c.get("outdated")) or detect_outdated(text)
    sensitive = (
        c.get("sensitivity") == "SENSITIVE"
        or bool(c.get("sensitive"))
        or c["heavy_emotion"]
        or matches("sensitive", text)
    )
    c["sensitivity"] = "SECRET" if secret else ("SENSITIVE" if sensitive else "PRIVATE")
    c["importance"] = max(c.get("importance", 0.5), 0.9 if c["heavy_emotion"] else 0)
    c["write_policy"] = "allow"
    c["injection_policy"] = "recall"
    reasons = []
    if c["kind"] in {"identity", "user-core"}:
        reasons.append("core identity requires exact-text confirmation")
    if sensitive:
        reasons.append("sensitive")
    for flag in ("suspicious", "temporary", "sarcasm", "outdated"):
        if c[flag]:
            reasons.append(flag)
    if matches("quoted", text):
        reasons.append("quoted statement")
    if c.get("source") != "user":
        # 有逐字核验证据的非 user 来源（assistant 本人提供过的内容、
        # 被引用的原话）不是「冒充用户发言」，而是待审的历史记录；
        # 没有核验证据的来源才按未核验处理。两者都只能人工审批
        # （write_policy=confirm 不变），区别只在理由要说真话。
        if c.get("evidence_verified") is True:
            reasons.append("non-user speaker requires review")
        else:
            reasons.append("unverified user speaker")
    if not c.get("evidence"):
        reasons.append("missing evidence")
    if c.get("evidence_verified") is not True:
        reasons.append("unverified evidence")
    if c.get("valid") is not True:
        reasons.append("invalid claim")
    # Model-provided policy may restrict but cannot grant permission.
    if c.get("requested_write_policy") in {"confirm", "never"}:
        reasons.append("restricted by source")
    if reasons:
        c["write_policy"] = "confirm"
        c["injection_policy"] = "confirm"
    if secret or c.get("requested_write_policy") == "never":
        c["write_policy"] = "never"
        c["injection_policy"] = "never"
        reasons = ["secret"] if secret else ["restricted by source"]
    c["requires_confirmation"] = c["write_policy"] == "confirm"
    c["auto_write"] = c["write_policy"] == "allow"
    c["policy_reasons"] = reasons
    return c


def check_security(claim):
    c = apply_policy(claim)
    return not c["auto_write"], "; ".join(c["policy_reasons"])
