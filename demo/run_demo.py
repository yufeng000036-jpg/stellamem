"""Run the real pipeline with fictional data, optionally an offline HTTP fixture."""

import argparse
import contextlib
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "compiler"))
from sleep_purify import run
from fixture_model import FixtureServer


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Synthetic model; tests engineering, not extraction quality",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", help="New empty sandbox directory; defaults to system temp")
    args = parser.parse_args(argv)
    if args.dry_run:
        print("Create isolated fictional diary sandbox, run actual pipeline, report exit status.")
        return 0
    root = Path(args.output) if args.output else Path(tempfile.mkdtemp(prefix="stellamem-demo-"))
    if root.exists() and any(root.iterdir()):
        print("PROBLEM: demo output must be empty")
        return 2
    root.mkdir(parents=True, exist_ok=True)
    (root / "daily").mkdir()
    for source in sorted((REPO / "demo/user").glob("*.md")):
        body = source.read_text(encoding="utf-8").split("\n", 1)[-1].strip()
        (root / "daily" / source.name).write_text(
            "[15:32:01] user: " + body + "\n", encoding="utf-8"
        )
    try:
        manager = FixtureServer() if args.offline else contextlib.nullcontext()
        with manager as model:
            if args.offline:
                config = {
                    "memories_root": str(root),
                    "server_url": model.url,
                    "model_id": "synthetic-fixture",
                }
                print("OFFLINE FIXTURE: no language-model accuracy claim")
            else:
                from config_loader import load_config

                config = dict(load_config(), memories_root=str(root))
            result = run(config)
        print(json.dumps(result, indent=2))
        print("Sandbox retained: " + str(root))
        return 0 if result["ok"] else 2
    except (OSError, ValueError, RuntimeError) as exc:
        print("PROBLEM: demo failed (" + type(exc).__name__ + "); sandbox retained")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
