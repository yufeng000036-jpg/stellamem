"""Non-destructive v1 import into the v2 review inbox; default is dry-run."""

import argparse
import json
import re
from claim_normalizer import normalize_claim, digest
from config_loader import load_config
from security import contains_secret
from storage import Transaction, safe_path, writer_lock
from store import MemoryStore, FILES, INBOX
from cli_output import ensure_utf8_stdout

LEGACY = re.compile(r"(?m)^- \x60claim-id:([^\x60]+)\x60\s+\*\*(.+?)\*\*:\s*(.*?)\s*\(")
ALIASES = {"pref": "preference", "dec": "decision"}


def plan(root):
    claims, blocked, unparsed = [], 0, 0
    for rel in FILES:
        if rel == INBOX:
            continue
        path = safe_path(root, rel)
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8").split("<!-- stellamem:v2 -->")[0]
        found = list(LEGACY.finditer(text))
        unparsed += max(0, text.count("claim-id:") - len(found))
        for match in found:
            if contains_secret(match[0]):
                blocked += 1
                continue
            parts = match[1].split(".")
            if len(parts) not in (3, 4):
                unparsed += 1
                continue
            kind, subject = parts[:2]
            scope = parts[2] if len(parts) == 4 else "global"
            c = normalize_claim(
                {
                    "kind": ALIASES.get(kind, kind),
                    "subject": subject,
                    "scope": scope,
                    "predicate": match[2],
                    "value": match[3],
                    "source": "unknown",
                    "confidence": 0,
                }
            )
            c["provenance"] = {
                "provider": "legacy",
                "source_ref": rel,
                "precision": "legacy",
                "content_hash": digest(match[0]),
            }
            claims.append(c)
    return claims, blocked, unparsed


def main(argv=None):
    ensure_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--yes", action="store_true", help="Apply reviewed import, keeping legacy originals"
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        root = load_config()["memories_root"]
        claims, blocked, unparsed = plan(root)
        report = {
            "candidates": len(claims),
            "blocked_secrets": blocked,
            "unparsed": unparsed,
            "applied": False,
        }
        if args.yes and not args.dry_run:
            with writer_lock(root):
                Transaction(root).recover()
                store = MemoryStore(root)
                for c in claims:
                    store.ingest(c, mode="observe")
                report["transaction"] = store.commit()
                report["applied"] = True
        print(json.dumps(report, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print("PROBLEM: migration stopped (" + type(exc).__name__ + "); inspect locally")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
