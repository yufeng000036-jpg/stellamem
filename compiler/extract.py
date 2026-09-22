"""Extract complete claims using the same pipeline as sleep_purify."""

import argparse
import json
from pathlib import Path
from claim_normalizer import normalize_claim
from model_client import call_llm, parse_json
from pipeline import SYSTEM, ChunkFailure, build_user, compile_messages
from source_reader import read_daily


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("daily")
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    try:
        from config_loader import load_config

        config = load_config()
        claims = [
            c
            for chunk in compile_messages(read_daily(args.daily, config), config)
            for c in chunk["claims"]
        ]
        result = json.dumps(claims, ensure_ascii=False, indent=2)
        if args.out:
            from storage import atomic_write

            atomic_write(Path(args.out), result)
        else:
            print(result)
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print("PROBLEM: extraction failed (" + type(exc).__name__ + ")")
        print("HOW TO FIX: verify source/config/model; incomplete output was not written.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
