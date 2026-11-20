#!/usr/bin/env python3
"""Independent bystander verification: source memory files vs the index.

Hermes'补法 (EigenFlux, 2026-11-20): a self-check written by the thing
it checks is not evidence. This script is the external bystander for
the Stellamem index. It does NOT use the Index, search, or API code:

- Source side: it parses the memory files itself (only the file-format
  parser, storage.load_records, is shared -- the record format is the
  contract) and recomputes each record's id and content hash from the
  source bytes' meaning.
- Index side: it opens the SQLite index with plain sqlite3, read-only.
- It then compares the two sides: records missing from the index,
  phantom rows with no source record, content-hash mismatches (silent
  divergence), and per-status counts. A records-bearing store whose
  vectors table is empty is a hard failure, not a warning -- the
  2026-11-21 production incident was exactly that shape (records 707,
  vectors 0) and every check on duty still reported ok.

Assumes the index was built with default options (pending included).

Usage:
    python3 tools/independent_verify.py MEMORY_ROOT INDEX_DB
Exit code 0 when the two sides agree, 1 on any mismatch, 2 on usage
or unreadable inputs. The JSON report goes to stdout.
"""

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))
from storage import load_records  # noqa: E402  (file-format parser only)

# The bystander's own expectation of where records live. Deliberately
# a separate copy from indexer.MANAGED: if the two ever drift apart,
# the count comparison below is exactly what surfaces it.
MANAGED_FILES = (
    "core/preferences.md",
    "core/user-core.md",
    "long-term/projects.md",
    "long-term/decisions.md",
    "long-term/lessons.md",
    "semantic/facts.md",
    "semantic/profile.md",
    "system/memory-inbox.md",
)


def source_records(root: Path) -> dict:
    """id -> {claim_id, status, value, redacted, content_hash} from files."""
    out = {}
    duplicates = []
    for rel in MANAGED_FILES:
        path = root / rel
        if not path.exists():
            continue
        _, records = load_records(path)
        for rec in records:
            rid = f"{rec.get('claim_id')}:{rec.get('revision_id')}"
            if rid in out:
                duplicates.append(rid)
            out[rid] = {
                "claim_id": rec.get("claim_id"),
                "status": rec.get("status"),
                "value": rec.get("value") or "",
                "redacted": bool(rec.get("redacted") or not rec.get("value")),
                "content_hash": hashlib.sha256(
                    json.dumps(rec, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest(),
            }
    return out, duplicates


def index_records(db_path: Path) -> dict:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT id, claim_id, status, value, redacted, content_hash FROM records"
        ).fetchall()
        vectors = con.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
    finally:
        con.close()
    out = {
        rid: {
            "claim_id": claim_id,
            "status": status,
            "value": value or "",
            "redacted": bool(redacted),
            "content_hash": content_hash,
        }
        for rid, claim_id, status, value, redacted, content_hash in rows
    }
    return out, vectors


def verify(root: Path, db_path: Path) -> dict:
    source, duplicates = source_records(root)
    index, vectors = index_records(db_path)

    missing = sorted(set(source) - set(index))
    phantom = sorted(set(index) - set(source))
    hash_mismatch, status_mismatch, value_mismatch = [], [], []
    for rid in sorted(set(source) & set(index)):
        s, i = source[rid], index[rid]
        if s["content_hash"] != i["content_hash"]:
            hash_mismatch.append(rid)
        if s["status"] != i["status"]:
            status_mismatch.append(rid)
        if not s["redacted"] and s["value"] != i["value"]:
            value_mismatch.append(rid)

    def counts(records):
        out = {}
        for r in records.values():
            out[r["status"]] = out.get(r["status"], 0) + 1
        return out

    warnings = []
    if duplicates:
        warnings.append(f"duplicate record ids in source files: {duplicates}")

    # Semantic-index liveness is a hard failure, not a warning.
    # Criterion: source records exist but the vectors table is empty.
    # There is no per-store "semantic disabled" declaration to gate
    # on: the only off-switch (STELLAMEM_SEMANTIC) is a search-time
    # runtime env var, not a property of the store being verified,
    # and the index builder always attempts a vector sync -- so a
    # records-bearing store with zero vectors is the incident shape
    # itself, not a legitimate configuration. An empty store (no
    # source records) is exempt: nothing was ever meant to be embedded.
    semantic_index_empty = bool(source) and vectors == 0
    failures = []
    if semantic_index_empty:
        failures.append(f"记录 {len(source)} 条、向量 0 条：语义索引未建或已失效")

    ok = (
        not (missing or phantom or hash_mismatch or status_mismatch or value_mismatch)
        and not semantic_index_empty
    )
    return {
        "ok": ok,
        "source_total": len(source),
        "index_total": len(index),
        "source_status_counts": counts(source),
        "index_status_counts": counts(index),
        "vectors": vectors,
        "semantic_index_empty": semantic_index_empty,
        "failures": failures,
        "missing_in_index": missing,
        "phantom_in_index": phantom,
        "content_hash_mismatch": hash_mismatch,
        "status_mismatch": status_mismatch,
        "value_mismatch": value_mismatch,
        "warnings": warnings,
    }


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    root, db_path = Path(argv[0]), Path(argv[1])
    if not root.is_dir() or not db_path.is_file():
        print(f"unreadable inputs: root={root} db={db_path}", file=sys.stderr)
        return 2
    report = verify(root, db_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
