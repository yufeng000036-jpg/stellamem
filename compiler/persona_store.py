"""Persona event persistence (PATCH2): a SQLite sidecar for livestream events.

Why a sidecar: persona events are *not* memory claims. Keeping them in their
own SQLite file means the persona adapter can persist across restarts without
writing into the user's memory markdown, without touching the index, and
without any risk of a livestream event being auto-promoted into the user's
long-term memory.

Design (PATCH2):
- append-only event log: every record/confirm/review/sync-export appends a row
  to ``persona_event_log``; nothing is deleted. The current state lives in
  ``persona_events`` (status transitions only, all logged).
- required fields: event_id, subject, text, created_at, status, spoken_at,
  source. ``source`` is who/what produced the event (speaker / platform / "人工").
- state machine: pending_confirmation -> confirmed -> reviewed.
  * pending events are invisible to retrieval;
  * confirmed (explicitly spoken) events are visible to the persona pack;
  * ``review`` is the human-review gate;
  * ``export_pack`` only exports reviewed events, and returns them as data:
    this module NEVER writes to the user library (no user-subject records, no
    markdown, no index). Sync is somebody else's explicitly approved job.
- duplicate event_id is rejected (ValueError), not silently overwritten.
- restart: opening a new store on the same path sees all events.

Subjects: every event is persona-subject. Passing anything else (notably
"user") is refused, not remapped.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

PERSONA = "persona"
SUBJECTS = ("persona",)

STATUSES = ("pending_confirmation", "confirmed", "reviewed")

SCHEMA = """
CREATE TABLE IF NOT EXISTS persona_events (
  event_id TEXT PRIMARY KEY,
  subject TEXT NOT NULL,
  text TEXT NOT NULL,
  created_at TEXT NOT NULL,
  status TEXT NOT NULL,
  spoken_at TEXT,
  source TEXT NOT NULL,
  reviewed_by TEXT,
  reviewed_at TEXT
);
CREATE TABLE IF NOT EXISTS persona_event_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT NOT NULL,
  action TEXT NOT NULL,
  at TEXT NOT NULL,
  actor TEXT DEFAULT '',
  note TEXT DEFAULT ''
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PersonaStore:
    def __init__(self, path=None):
        self.path = str(path) if path is not None else ":memory:"
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.path)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)
        self.con.commit()

    def close(self):
        self.con.close()

    # -- audit (append-only) ------------------------------------------
    def _log(self, event_id: str, action: str, actor: str = "", note: str = "") -> None:
        self.con.execute(
            "INSERT INTO persona_event_log (event_id, action, at, actor, note) VALUES (?,?,?,?,?)",
            (event_id, action, _now_iso(), actor, note),
        )

    def log(self, event_id: str | None = None) -> list[dict]:
        if event_id is None:
            rows = self.con.execute("SELECT * FROM persona_event_log ORDER BY id").fetchall()
        else:
            rows = self.con.execute(
                "SELECT * FROM persona_event_log WHERE event_id=? ORDER BY id", (event_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    # -- read ----------------------------------------------------------
    def get(self, event_id: str) -> dict:
        row = self.con.execute(
            "SELECT * FROM persona_events WHERE event_id=?", (event_id,)
        ).fetchone()
        if row is None:
            raise KeyError(event_id)
        return dict(row)

    def exists(self, event_id: str) -> bool:
        return (
            self.con.execute(
                "SELECT 1 FROM persona_events WHERE event_id=?", (event_id,)
            ).fetchone()
            is not None
        )

    def list_status(self, status: str) -> list[dict]:
        if status not in STATUSES:
            raise ValueError(f"unknown status: {status!r}")
        rows = self.con.execute(
            "SELECT * FROM persona_events WHERE status=? ORDER BY created_at, event_id", (status,)
        ).fetchall()
        return [dict(r) for r in rows]

    def list_pending(self) -> list[dict]:
        return self.list_status("pending_confirmation")

    def list_confirmed(self) -> list[dict]:
        return self.list_status("confirmed")

    def list_reviewed(self) -> list[dict]:
        return self.list_status("reviewed")

    # -- write ---------------------------------------------------------
    def record(
        self,
        text: str,
        source: str,
        event_id: str | None = None,
        subject: str = PERSONA,
        created_at: str | None = None,
    ) -> dict:
        """Append one event in pending_confirmation. Duplicate id is refused."""
        if subject != PERSONA:
            raise ValueError(
                "persona store only records persona events; "
                f"refused subject {subject!r} (no user-library writes from here)"
            )
        if not text or not str(text).strip():
            raise ValueError("event text is required")
        if not source or not str(source).strip():
            raise ValueError("event source is required")
        if event_id is None:
            row = self.con.execute("SELECT COUNT(*) FROM persona_events").fetchone()[0]
            event_id = f"pevent-{row + 1:04d}"
        event_id = str(event_id).strip()
        if not event_id:
            raise ValueError("event id must not be empty")
        if self.exists(event_id):
            raise ValueError(f"duplicate event_id: {event_id!r} (append-only log, not overwritten)")
        created = created_at or _now_iso()
        self.con.execute(
            "INSERT INTO persona_events (event_id, subject, text, created_at, status, spoken_at, source)"
            " VALUES (?,?,?,?, 'pending_confirmation', NULL, ?)",
            (event_id, PERSONA, str(text).strip(), created, str(source).strip()),
        )
        self._log(event_id, "recorded", actor=str(source).strip())
        self.con.commit()
        return self.get(event_id)

    def confirm_spoken(
        self, event_id: str, spoken_evidence: str = "", spoken_at: str | None = None
    ) -> dict:
        """pending_confirmation -> confirmed. Explicit confirmation only."""
        rec = self.get(event_id)
        if rec["status"] == "confirmed":
            return rec
        if rec["status"] != "pending_confirmation":
            raise ValueError(
                f"cannot confirm event in status {rec['status']!r} (only pending events)"
            )
        spoken = spoken_at or _now_iso()
        self.con.execute(
            "UPDATE persona_events SET status='confirmed', spoken_at=? WHERE event_id=?",
            (spoken, event_id),
        )
        self._log(event_id, "confirmed_spoken", note=spoken_evidence or "")
        self.con.commit()
        return self.get(event_id)

    def review(self, event_id: str, reviewer: str) -> dict:
        """confirmed -> reviewed. The human-review gate before any sync."""
        if not reviewer or not str(reviewer).strip():
            raise ValueError("reviewer is required")
        rec = self.get(event_id)
        if rec["status"] == "reviewed":
            return rec
        if rec["status"] != "confirmed":
            raise ValueError(
                f"cannot review event in status {rec['status']!r} (only confirmed events)"
            )
        self.con.execute(
            "UPDATE persona_events SET status='reviewed', reviewed_by=?, reviewed_at=? WHERE event_id=?",
            (str(reviewer).strip(), _now_iso(), event_id),
        )
        self._log(event_id, "reviewed", actor=str(reviewer).strip())
        self.con.commit()
        return self.get(event_id)

    # -- read for the livestream side -----------------------------------
    def pack(self, speaker: str | None = None, budget_chars: int = 800) -> dict:
        """Visible pack: confirmed + reviewed events only (pending invisible).

        Nothing here writes anywhere; the pack is for prompt context.
        """
        if budget_chars is not None and budget_chars < 0:
            raise ValueError("budget_chars must be >= 0")
        rows = self.con.execute(
            "SELECT * FROM persona_events WHERE status IN ('confirmed','reviewed') "
            "ORDER BY created_at, event_id"
        ).fetchall()
        items = []
        for row in rows:
            ev = dict(row)
            if speaker and ev["source"] != str(speaker).strip():
                continue
            items.append(ev)
        lines = [f"- {ev['text']}" for ev in items]
        text = "\n".join(lines)
        if budget_chars is not None and len(text) > budget_chars:
            text = text[:budget_chars]
        return {
            "subject": PERSONA,
            "speaker": speaker,
            "items": items,
            "text": text,
            "chars": len(text),
            "budget": budget_chars,
            "pending_visible": False,
        }

    def export_pack(self) -> list[dict]:
        """Data for an approved sync job: reviewed events only.

        This is deliberately a return value, not a write: PATCH2 does NOT
        sync into the user library automatically. A human-approved job may
        consume this list; that job is out of scope here.
        """
        events = self.list_reviewed()
        self._log("*", "export_pack", note=f"reviewed_events={len(events)}")
        self.con.commit()
        return events
