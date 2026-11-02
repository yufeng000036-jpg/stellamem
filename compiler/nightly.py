"""Nightly consolidation (Step 7, part 2): deterministic, no model calls.

The nightly job turns a day's scattered events into stable facts and
a forgetting list. It deliberately does NOT call any model and does
NOT send anything to a cloud service: scoring is a documented,
deterministic formula over the event fields, so the result is
auditable and reproducible.

Scoring (weights are documented conventions, not measured optima):
- base importance: event.get("importance", 0) in 0..10;
- time decay: score loses 0.1 per day of age (age_days, floor at 0);
- emotion weight: event.get("emotion_weight", 0) in 0..2;
- consequence weight: event.get("consequence_weight", 0) in 0..3.
  total = importance - 0.1*age_days + emotion + consequence.
- keep threshold: total >= keep_threshold (default 5.0) -> consolidated;
  below -> forgotten (listed, not deleted by this module).

Grouping uses (subject, scope, predicate). Conflicting values are never
selected by score: all candidate events go to a needs_review conflict list.
The complete deep-copied source_events retain timestamps, evidence, versions,
and low-score events. Forgotten means an inactive suggestion, never deletion.
Text output includes only complete lines that fit; it is not canonical storage.
This is an experimental pure planner with no production caller.
Inputs are not mutated and nothing is written; the caller decides
what to do with the output under its own approval.
"""

from __future__ import annotations

import copy
import math
import json

DEFAULT_KEEP_THRESHOLD = 5.0


def score_event(event: dict) -> float:
    importance = float(event.get("importance") or 0.0)
    age_days = float(event.get("age_days") or 0.0)
    emotion = float(event.get("emotion_weight") or 0.0)
    consequence = float(event.get("consequence_weight") or 0.0)
    if not all(math.isfinite(v) for v in (importance, age_days, emotion, consequence)):
        raise ValueError("event scores must be finite")
    return importance - 0.1 * max(0, age_days) + emotion + consequence


def consolidate(
    events, keep_threshold: float = DEFAULT_KEEP_THRESHOLD, budget_chars: int = 2000
) -> dict:
    """Group events, score them, split into kept and forgotten lists."""
    if budget_chars is not None and budget_chars < 0:
        raise ValueError("budget_chars must be >= 0")
    if not math.isfinite(keep_threshold):
        raise ValueError("keep_threshold must be finite")
    source_events = copy.deepcopy(list(events or []))
    groups: dict[tuple, list] = {}
    for event in source_events:
        key = (
            str(event.get("subject") or ""),
            str(event.get("scope") or ""),
            str(event.get("predicate") or ""),
        )
        groups.setdefault(key, []).append(event)
    kept = []
    forgotten = []
    conflicts = []
    for (subject, scope, predicate), members in sorted(groups.items()):
        scored = sorted(members, key=lambda e: (-score_event(e), str(e.get("event_id") or "")))
        best = scored[0]
        total = score_event(best)
        entry = {
            "subject": subject,
            "scope": scope,
            "predicate": predicate,
            "value": best.get("value"),
            "score": total,
            "evidence_event_ids": [str(e.get("event_id") or "") for e in scored],
            "member_count": len(scored),
        }
        values = {json.dumps(e.get("value"), sort_keys=True, ensure_ascii=False) for e in scored}
        if len(values) > 1:
            conflicts.append(
                {
                    "subject": subject,
                    "scope": scope,
                    "predicate": predicate,
                    "status": "needs_review",
                    "candidates": copy.deepcopy(scored),
                }
            )
            continue
        if total >= keep_threshold:
            kept.append(entry)
        else:
            forgotten.append(entry)
    kept.sort(key=lambda e: (-e["score"], e["subject"], e["scope"], e["predicate"]))
    forgotten.sort(key=lambda e: (-e["score"], e["subject"], e["scope"], e["predicate"]))
    text = "\n".join(
        (
            f"- {k['subject']} [{k['scope']}] {k['predicate']}: {k['value']}"
            if k["scope"]
            else f"- {k['subject']} {k['predicate']}: {k['value']}"
        )
        for k in kept
    )
    if budget_chars is not None and len(text) > budget_chars:
        complete = []
        for line in text.splitlines():
            if len("\n".join(complete + [line])) > budget_chars:
                break
            complete.append(line)
        text = "\n".join(complete)
    return {
        "source_events": source_events,
        "conflicts": conflicts,
        "conflict_count": len(conflicts),
        "experimental": True,
        "kept": kept,
        "forgotten": forgotten,
        "kept_count": len(kept),
        "forgotten_count": len(forgotten),
        "text": text,
        "chars": len(text),
        "budget_chars": budget_chars,
        "keep_threshold": keep_threshold,
        "changed_anything": False,
        "wrote_anything": False,
        "model_called": False,
        "cloud_called": False,
    }
