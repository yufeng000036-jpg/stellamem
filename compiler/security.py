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
