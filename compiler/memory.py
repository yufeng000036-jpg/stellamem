"""Inspect, explain and roll back local governed memory; no remote operations."""

import argparse
import json
from pathlib import Path
from config_loader import load_config
from storage import Transaction, writer_lock, load_records, safe_path
from store import FILES


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    for name in ("explain", "history"):
        sub.add_parser(name).add_argument("id")
    sub.add_parser("diff").add_argument("transaction")
    rollback = sub.add_parser("rollback")
    rollback.add_argument("transaction")
    rollback.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = load_config()
        root = Path(config["memories_root"])
        if args.command == "doctor":
            from importlib.util import spec_from_file_location, module_from_spec

            spec = spec_from_file_location(
                "stellamem_setup", Path(__file__).resolve().parents[1] / "setup.py"
            )
            module = module_from_spec(spec)
            spec.loader.exec_module(module)
            checks = module.check_report(offline=True)
            print(json.dumps(checks, indent=2))
            return 1 if any(c["status"] == "FAIL" for c in checks) else 0
        if args.command in ("diff", "rollback"):
            with writer_lock(root):
                tx = Transaction(root)
                result = tx.rollback(args.transaction, yes=args.command == "rollback" and args.yes)
                print(
                    json.dumps(
                        {"result": result, "applied": args.command == "rollback" and args.yes}
                    )
                )
            return 0
        events_path = safe_path(root, "system/version-chain.json")
        events = json.loads(events_path.read_text(encoding="utf-8")) if events_path.exists() else []
        result = [
            e for e in events if args.id in (e.get("id"), e.get("current"), e.get("event_id"))
        ]
        if args.command == "explain":
            result = [
                r
                for rel in FILES
                for r in load_records(safe_path(root, rel))[1]
                if args.id in (r.get("claim_id"), r.get("revision_id"), r.get("event_id"))
            ]
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result else 1
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        print("PROBLEM: memory operation failed (" + type(exc).__name__ + ")")
        print(
            "HOW TO FIX: verify config, record integrity, transaction ID and unchanged target files."
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
