"""One authoritative claim representation. No configuration or I/O on import."""

import hashlib
import json
import math
import re
import unicodedata
from security import apply_policy

ROUTE = {
    "preference": "core/preferences.md",
    "decision": "long-term/decisions.md",
    "lesson": "long-term/lessons.md",
    "procedure": "long-term/lessons.md",
    "project": "long-term/projects.md",
    "fact": "semantic/profile.md",
    "relationship": "semantic/profile.md",
    "identity": "core/user-core.md",
    "user-core": "core/user-core.md",
}
THRESHOLDS = dict(
    project=0.85,
    decision=0.85,
    lesson=0.85,
    procedure=0.90,
    preference=0.95,
    fact=0.90,
    relationship=0.95,
    identity=1.01,
    **{"user-core": 1.01},
)
VALID_KINDS = set(ROUTE)
PROTECTED_KINDS = {"identity", "user-core"}


def number(value, default=0.0):
    try:
        n = float(value)
        return max(0.0, min(1.0, n)) if math.isfinite(n) else default
    except (TypeError, ValueError):
        return default


def canonical(value):
    return unicodedata.normalize("NFC", str(value or "")).strip()


def claim_slot(c):
    return tuple(canonical(c.get(k)).casefold() for k in ("kind", "subject", "scope", "predicate"))


def claim_key(c):
    # Values retain punctuation and case: C++, C#, paths and credentials differ.
    return claim_slot(c) + (canonical(c.get("value")),)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _id_part(value):
    return re.sub(r"[^\w-]+", "_", canonical(value).casefold()).strip("_") or "unknown"


def claim_id(c):
    # Hash avoids legacy lossy slug collisions.
    return "c-" + digest(claim_slot(c))[:24]


def claim_revision_id(c):
    return "r-" + digest(claim_key(c))[:24]


def normalize_claim(raw):
    if not isinstance(raw, dict):
        raise ValueError("claim must be an object")
    c = {k: canonical(raw.get(k)) for k in ("kind", "subject", "predicate", "value", "evidence")}
    c["scope"] = canonical(raw.get("scope")) or "global"
    c["source"] = canonical(raw.get("source", "unknown")).lower()
    c["confidence"] = number(raw.get("confidence"))
    c["importance"] = number(raw.get("importance"), 0.5)
    c["valid"] = c["kind"] in VALID_KINDS and all(c[k] for k in ("subject", "predicate", "value"))
    for key in ("suspicious", "temporary", "sarcasm", "outdated", "sensitive", "heavy_emotion"):
        value = raw.get(key, False)
        # Unknown flag encodings are conservative, never treated as false.
        c[key] = value if isinstance(value, bool) else value not in (None, 0, "", "false")
    c["sensitivity"] = str(raw.get("sensitivity", "")).upper()
    c["requested_write_policy"] = raw.get("requested_write_policy", raw.get("write_policy"))
    # Verified provenance is attached by pipeline after normalization, not the LLM.
    c["evidence_verified"] = False
    c["schema_version"] = 2
    return apply_policy(c)
