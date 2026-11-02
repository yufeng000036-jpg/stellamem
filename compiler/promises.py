"""Promises/tasks: a separate lightweight SQLite sidecar.

Promises are state machines (open -> fulfilled / expired), not
memories. They therefore live in their own SQLite table, NOT as
markdown claims: no markdown version to sync, no risk of a promise
edit rewriting a memory record, and expiry/fulfilment can be queried
without parsing prose.

Rules:
- create(): status "open", with optional expires_at.
- fulfil(): requires evidence; refused after expiry, but the refusal
  itself is audited and the promise remains auditable (it is marked
  expired, not deleted, not fulfilled).
- expire(): open + past-due -> expired, audited. Expired promises
  never appear in list_open().
- Nothing here deletes a promise. Auditing is append-only in spirit:
  every create/fulfil/expire/refusal writes an audit row.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS promises (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  subject TEXT NOT NULL DEFAULT 'user',
  description TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open',
  evidence TEXT,
  created_at TEXT NOT NULL,
  expires_at TEXT,
  fulfilled_at TEXT,
  expired_at TEXT
);
CREATE TABLE IF NOT EXISTS promise_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  promise_id TEXT NOT NULL,
  action TEXT NOT NULL,
  at TEXT NOT NULL,
  note TEXT DEFAULT ''
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PromiseStore:
    def __init__(self, path=None):
        self.path = str(path) if path is not None else ":memory:"
        self.con = sqlite3.connect(self.path)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)
        self.con.commit()

    def close(self):
        self.con.close()

    def _audit(self, promise_id: str, action: str, note: str = "") -> None:
        self.con.execute(
            "INSERT INTO promise_audit (promise_id, action, at, note) VALUES (?,?,?,?)",
            (promise_id, action, _now_iso(), note),
        )

    def create(self, title: str, description: str = "", subject: str = "user",
               expires_at: str | None = None) -> dict:
        if not title or not str(title).strip():
            raise ValueError("promise title is required")
        row = self.con.execute("SELECT COUNT(*) FROM promises").fetchone()[0]
        promise_id = f"promise-{row + 1:04d}"
        self.con.execute(
            "INSERT INTO promises (id, title, subject, description, status, created_at, expires_at)"
            " VALUES (?,?,?,?, 'open', ?, ?)",
            (promise_id, str(title).strip(), subject, description, _now_iso(), expires_at),
        )
        self._audit(promise_id, "created")
        self.con.commit()
        return self.get(promise_id)

    def get(self, promise_id: str) -> dict:
        row = self.con.execute("SELECT * FROM promises WHERE id = ?", (promise_id,)).fetchone()
        if row is None:
            raise KeyError(promise_id)
        return dict(row)

    def _is_expired(self, rec: dict, now: str | None = None) -> bool:
        expires = rec.get("expires_at")
        if not expires:
            return False
        return (now or _now_iso()) > expires

    def expire(self, promise_id: str) -> dict:
        rec = self.get(promise_id)
        if rec["status"] != "open":
            raise ValueError(f"cannot expire promise in status {rec['status']!r}")
        self.con.execute(
            "UPDATE promises SET status='expired', expired_at=? WHERE id = ?",
            (_now_iso(), promise_id),
        )
        self._audit(promise_id, "expired")
        self.con.commit()
        return self.get(promise_id)

    def expire_due(self, now: str | None = None) -> list[dict]:
        """Expire every open, past-due promise. Returns the expired ones."""
        out = []
        for rec in self.list_open():
            if self._is_expired(rec, now=now):
                self.con.execute(
                    "UPDATE promises SET status='expired', expired_at=? WHERE id = ?",
                    (_now_iso(), rec["id"]),
                )
                self._audit(rec["id"], "expired", note="past due")
                out.append(self.get(rec["id"]))
        self.con.commit()
        return out

    def fulfil(self, promise_id: str, evidence: str, now: str | None = None) -> dict:
        rec = self.get(promise_id)
        if rec["status"] != "open":
            self._audit(promise_id, "fulfil_refused", note=f"status is {rec['status']}")
            self.con.commit()
            raise ValueError(f"cannot fulfil promise in status {rec['status']!r}")
        if self._is_expired(rec, now=now):
            # Refused, but auditable: mark expired, do NOT mark fulfilled,
            # do NOT delete. The evidence offered is kept in the audit note.
            self.con.execute(
                "UPDATE promises SET status='expired', expired_at=? WHERE id = ?",
                (_now_iso(), promise_id),
            )
            self._audit(promise_id, "fulfil_refused_expired",
                        note=f"evidence offered but past due: {evidence}")
            self.con.commit()
            raise ValueError("cannot fulfil an expired promise")
        if not evidence or not str(evidence).strip():
            raise ValueError("fulfilment requires evidence")
        self.con.execute(
            "UPDATE promises SET status='fulfilled', evidence=?, fulfilled_at=? WHERE id = ?",
            (str(evidence).strip(), _now_iso(), promise_id),
        )
        self._audit(promise_id, "fulfilled", note=str(evidence).strip())
        self.con.commit()
        return self.get(promise_id)

    def list_open(self, subject: str | None = None) -> list[dict]:
        if subject is None:
            rows = self.con.execute(
                "SELECT * FROM promises WHERE status='open' ORDER BY created_at"
            ).fetchall()
        else:
            rows = self.con.execute(
                "SELECT * FROM promises WHERE status='open' AND subject=? ORDER BY created_at",
                (subject,),
            ).fetchall()
        return [dict(r) for r in rows]

    def audit(self, promise_id: str) -> list[dict]:
        rows = self.con.execute(
            "SELECT * FROM promise_audit WHERE promise_id=? ORDER BY id", (promise_id,)
        ).fetchall()
        return [dict(r) for r in rows]
