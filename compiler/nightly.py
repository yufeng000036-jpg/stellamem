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

Grouping: events with the same (subject, predicate) are consolidated
into one fact whose value is the highest-scoring event's value and
whose evidence is the list of grouped event ids. Inputs are not
mutated and nothing is written; the caller decides what to do with
the output under its own approval.
"""

from __future__ import annotations

DEFAULT_KEEP_THRESHOLD = 5.0


def score_event(event: dict) -> float:
    importance = float(event.get("importance") or 0.0)
    age_days = float(event.get("age_days") or 0.0)
    emotion = float(event.get("emotion_weight") or 0.0)
    consequence = float(event.get("consequence_weight") or 0.0)
    return importance - 0.1 * age_days + emotion + consequence


def consolidate(events, keep_threshold: float = DEFAULT_KEEP_THRESHOLD,
                budget_chars: int = 2000) -> dict:
    """Group events, score them, split into kept and forgotten lists."""
    if budget_chars is not None and budget_chars < 0:
        raise ValueError("budget_chars must be >= 0")
    groups: dict[tuple, list] = {}
    for event in events or []:
        key = (str(event.get("subject") or ""), str(event.get("predicate") or ""))
        groups.setdefault(key, []).append(event)
    kept = []
    forgotten = []
    for (subject, predicate), members in sorted(groups.items()):
        scored = sorted(members, key=lambda e: (-score_event(e), str(e.get("event_id") or "")))
        best = scored[0]
        total = score_event(best)
        entry = {
            "subject": subject,
            "predicate": predicate,
            "value": best.get("value"),
            "score": total,
            "evidence_event_ids": [str(e.get("event_id") or "") for e in scored],
            "member_count": len(scored),
        }
        if total >= keep_threshold:
            kept.append(entry)
        else:
            forgotten.append(entry)
    kept.sort(key=lambda e: (-e["score"], e["subject"], e["predicate"]))
    forgotten.sort(key=lambda e: (-e["score"], e["subject"], e["predicate"]))
    text = "\n".join(f"- {k['subject']} {k['predicate']}: {k['value']}" for k in kept)
    if budget_chars is not None and len(text) > budget_chars:
        text = text[:budget_chars]
    return {
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
