"""Compile one known synthetic statement from the optional isolated host test."""

import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))
from openclaw_sqlite_ingest import grant_database, ingest_session
from openclaw_sqlite_source import resolve_pointer
from pipeline import compile_messages
from storage import writer_lock
from store import MemoryStore


def main(root):
    database = root / "state-origin/agents/main/agent/openclaw-agent.sqlite"
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        ((session_id,),) = connection.execute("SELECT session_id FROM session_windows").fetchall()
    allowlist = root / "fixture-allowlist.json"
    grant_database(
        allowlist, db_id="fixture", database=database, sessions=[session_id], actor="isolated-test"
    )
    result = ingest_session(
        allowlist, root / "fixture-pointers.json", db_id="fixture", session_id=session_id
    )
    user, assistant = result["messages"]
    assert user["speaker"] == "user" and assistant["speaker"] == "assistant"
    verified = resolve_pointer(database, user["provenance"])
    assert verified["text"] == "My favorite fictional beverage is fictional nebula tea."

    def model(system, payload):
        assert verified["text"] in payload
        return json.dumps(
            [
                dict(
                    kind="fact",
                    subject="user",
                    scope="global",
                    predicate="favorite fictional beverage",
                    value="fictional nebula tea",
                    evidence=verified["text"],
                    confidence=0.99,
                    source="user",
                    message_index=0,
                )
            ]
        )

    (claim,) = [
        c
        for batch in compile_messages([verified], {"chunk_chars": 512}, model)
        for c in batch["claims"]
    ]
    assert claim["evidence_verified"] and claim["source"] == "user"
    memory = root / "memory"
    with writer_lock(memory):
        store = MemoryStore(memory)
        store.ingest(claim)
        assert store.counts["active"] == 1
        store.commit()


if __name__ == "__main__":
    main(Path(sys.argv[1]))
