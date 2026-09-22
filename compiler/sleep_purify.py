"""Compile changed daily sources through a single governed pipeline."""

import argparse
import json
from pathlib import Path
from claim_normalizer import ROUTE, THRESHOLDS, VALID_KINDS, PROTECTED_KINDS
from claim_normalizer import claim_slot, claim_key, claim_id, claim_revision_id, normalize_claim
from pipeline import ChunkFailure, compile_messages
from source_reader import content_hash, read_daily
from security import check_security, contains_secret
from storage import Transaction, atomic_write, safe_path, writer_lock
from store import MemoryStore

CURSOR = "system/extraction-cursor.json"


def run(config, mode="review", llm=None):
    root = Path(config["memories_root"])
    with writer_lock(root):
        tx = Transaction(root)
        tx.recover()
        cursor_path = safe_path(root, CURSOR if mode == "review" else "system/observe-cursor.json")
        if cursor_path.exists():
            cursor = json.loads(cursor_path.read_text(encoding="utf-8"))
            if cursor.get("schema_version") != 2 or not isinstance(cursor.get("sources"), dict):
                raise ValueError("invalid cursor; use backup/review to recover")
        else:
            # Legacy cursor not trusted as proof of complete extraction.
            cursor = {"schema_version": 2, "sources": {}}
        total = dict(sources=0, chunks=0, active=0, pending=0, blocked=0, duplicate=0)
        daily = safe_path(root, "daily")
        if not daily.is_dir():
            raise ValueError("daily directory missing; run installer")
        for source in sorted(daily.glob("*.md")):
            safe_path(root, "daily/" + source.name)
            manifest = source.with_suffix(".sources.json")
            payload = (
                manifest.read_text(encoding="utf-8")
                if manifest.exists()
                else source.read_text(encoding="utf-8")
            )
            fingerprint = content_hash(payload)
            if cursor["sources"].get(source.name) == fingerprint:
                continue
            store = MemoryStore(root)
            try:
                messages = read_daily(source, config)
                store.counts["blocked"] += sum(contains_secret(m["text"]) for m in messages)
                chunks = 0
                for chunk in compile_messages(messages, config, llm):
                    chunks += 1
                    store.counts["blocked"] += chunk["secret_messages"]
                    for claim in chunk["claims"]:
                        store.ingest(claim, mode, llm, config)
            except (OSError, ValueError, RuntimeError) as exc:
                status = {
                    "status": "failed",
                    "source_hash": fingerprint,
                    "error": type(exc).__name__,
                    "failed_chunk": getattr(exc, "index", None),
                    "chunk_id": getattr(exc, "chunk_id", None),
                }
                atomic_write(
                    safe_path(root, "system/compile-status.json"), json.dumps(status, indent=2)
                )
                return {"ok": False, **total, **status}
            # Commit source only when every chunk is complete; failures do not
            # consume any chunk or advance the source cursor. Retrying is safe.
            cursor["sources"][source.name] = fingerprint
            status = {
                "status": "success",
                "source_hash": fingerprint,
                "chunks": chunks,
                "blocked": store.counts["blocked"],
            }
            cursor_rel = CURSOR if mode == "review" else "system/observe-cursor.json"
            store.commit(
                {
                    cursor_rel: json.dumps(cursor, indent=2),
                    "system/compile-status.json": json.dumps(status, indent=2),
                }
            )
            total["sources"] += 1
            total["chunks"] += chunks
            for key, count in store.counts.items():
                total[key] += count
        return {"ok": True, **total}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["review", "observe"], default="review")
    parser.add_argument(
        "--force", action="store_true", help="Legacy alias; sources are content-addressed"
    )
    args = parser.parse_args(argv)
    try:
        from config_loader import load_config

        result = run(load_config(), args.mode)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["ok"] else 2
    except (OSError, ValueError, RuntimeError) as exc:
        print("PROBLEM: compile failed (" + type(exc).__name__ + ")")
        print("HOW TO FIX: check config, permissions, source integrity and pending transaction.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
