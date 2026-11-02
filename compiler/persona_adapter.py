"""Persona adapter: the thin interface for the livestream persona.

This adapter is the ONLY way the livestream side touches memory in
this round, and it is deliberately narrow:
- record_event(): an event starts as pending_confirmation. It is NOT
  retrievable as persona memory until it is confirmed spoken.
- retrieve_pack(): persona subject only, char-budget capped, speaker
  card first, then confirmed events only. Pending events never appear.
- confirm_spoken(): upgrades pending -> confirmed ONLY on an explicit
  spoken confirmation. There is no auto-confirm, no timeout-confirm,
  and no path from here to user-subject records.

No direct writes to user-subject records exist in this module at all:
record_event() always creates persona-subject events, and passing a
different subject is refused, not silently remapped.

PATCH2: an optional ``store`` (persona_store.PersonaStore, SQLite
sidecar) makes events survive restarts. Without a store the adapter stays
in-memory exactly as before. With a store:
- events are appended to the sidecar (duplicate event ids refused there);
- ``review_spoken()`` is the human-review gate;
- sync/export is still NOT done here: the store's ``export_pack()``
  returns data for a separate, human-approved job, and this module never
  writes the user library. There is no automatic sync.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _view(row: dict, speaker: str | None = None) -> dict:
    """Map a persona_store row onto the adapter's event shape."""
    return {
        "id": row["event_id"],
        "subject": row["subject"],
        "speaker": row["source"],
        "text": row["text"],
        "status": row["status"],
        "created_at": row["created_at"],
        "spoken_at": row.get("spoken_at"),
        "source": row["source"],
        "reviewed_by": row.get("reviewed_by"),
        "reviewed_at": row.get("reviewed_at"),
    }


class PersonaAdapter:
    def __init__(self, store=None):
        self.store = store
        self.events: dict[str, dict] = {}
        self.cards: dict[str, str] = {}
        self._counter = itertools.count(1)

    def set_card(self, speaker: str, card: str) -> None:
        """One-line speaker card, e.g. a regular-viewer summary."""
        if not speaker or not str(speaker).strip():
            raise ValueError("speaker is required")
        self.cards[str(speaker).strip()] = str(card or "").strip()

    def record_event(self, speaker: str, text: str, subject: str = "persona",
                     source: str | None = None) -> dict:
        if subject != "persona":
            raise ValueError(
                "persona_adapter only records persona-subject events; "
                f"refused subject {subject!r} (no writes to user records from here)"
            )
        if not speaker or not str(speaker).strip():
            raise ValueError("speaker is required")
        if not text or not str(text).strip():
            raise ValueError("event text is required")
        if self.store is not None:
            row = self.store.record(text=text, source=source or speaker, subject=subject)
            event = _view(row)
            self.events[event["id"]] = event
            return dict(event)
        event_id = f"evt-{next(self._counter):04d}"
        event = {
            "id": event_id,
            "subject": "persona",
            "speaker": str(speaker).strip(),
            "text": str(text).strip(),
            "status": "pending_confirmation",
            "created_at": _now(),
            "spoken_at": None,
            "source": source or str(speaker).strip(),
        }
        self.events[event_id] = event
        return dict(event)

    def confirm_spoken(self, event_id: str, confirmed: bool = False,
                       spoken_evidence: str = "") -> dict:
        """Explicit spoken confirmation only. confirmed=False changes nothing."""
        if self.store is not None:
            if not confirmed:
                return _view(self.store.get(event_id))
            row = self.store.confirm_spoken(event_id, spoken_evidence=spoken_evidence)
            event = _view(row)
            self.events[event["id"]] = event
            return dict(event)
        event = self.events.get(event_id)
        if event is None:
            raise KeyError(event_id)
        if not confirmed:
            return dict(event)
        if event["status"] == "confirmed":
            return dict(event)
        event["status"] = "confirmed"
        event["spoken_at"] = _now()
        if spoken_evidence:
            event["spoken_evidence"] = str(spoken_evidence)
        return dict(event)

    def review_spoken(self, event_id: str, reviewer: str) -> dict:
        """Human-review gate (requires the persistent store).

        Only reviewed events are eligible for a later, approved sync; this
        module still never writes the user library.
        """
        if self.store is None:
            raise RuntimeError(
                "review_spoken requires a persistent store; the in-memory "
                "adapter has no review/sync step by design"
            )
        event = _view(self.store.review(event_id, reviewer=reviewer))
        self.events[event["id"]] = event
        return dict(event)

    def export_reviewed(self) -> list[dict]:
        """Reviewed-event data for an approved sync job (no writes here)."""
        if self.store is None:
            return []
        return [dict(e) for e in self.store.export_pack()]

    def list_pending(self) -> list[dict]:
        if self.store is not None:
            return [_view(e) for e in self.store.list_pending()]
        return [dict(e) for e in self.events.values() if e["status"] == "pending_confirmation"]

    def list_confirmed(self) -> list[dict]:
        if self.store is not None:
            return [_view(e) for e in self.store.list_confirmed()]
        return [dict(e) for e in self.events.values() if e["status"] == "confirmed"]

    def retrieve_pack(self, speaker: str | None = None, budget_chars: int = 800) -> dict:
        """Persona-only pack, char-budget capped, speaker card first.

        Only confirmed events are included. Pending events (not yet
        spoken) are invisible here, by design. With a persistent store,
        reviewed events are included too (they were confirmed first).
        The returned text never exceeds budget_chars; lines are dropped
        from the end, never by silently merging or reordering the card.
        """
        if budget_chars is not None and budget_chars < 0:
            raise ValueError("budget_chars must be >= 0")
        lines: list[str] = []
        if speaker and self.cards.get(str(speaker).strip()):
            lines.append(f"[{speaker}] {self.cards[str(speaker).strip()]}")
        if self.store is not None:
            visible = [e for e in self.store.pack(speaker=speaker, budget_chars=None)["items"]]
            for event in visible:
                lines.append(f"- {event['text']}")
        else:
            for event in self.events.values():
                if event["status"] != "confirmed":
                    continue
                if speaker and event["speaker"] != str(speaker).strip():
                    continue
                lines.append(f"- {event['text']}")
        full = "\n".join(lines)
        text = full
        if budget_chars is not None and len(text) > budget_chars:
            text = text[:budget_chars]
        used = len(text)
        return {
            "subject": "persona",
            "speaker": speaker,
            "items": lines,
            "text": text,
            "chars": used,
            "budget": budget_chars,
            "truncated": len(full) > used if budget_chars is not None else False,
        }
