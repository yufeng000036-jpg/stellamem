"""Extract complete claims using the same pipeline as sleep_purify.

两种输入：
    extract.py daily/2026-09-15.md      # 旧路径：散文日记，speaker 多为 unknown
    extract.py --from-raw 2026-09-15    # 新路径：原始档案，speaker/时间/record_id 来自原文
"""

import argparse
import json
from pathlib import Path
from claim_normalizer import normalize_claim  # noqa: F401  (单一规范化实现的不变量锚点)
from pipeline import build_user, compile_messages  # noqa: F401  (不截断原文的不变量锚点)
from source_reader import read_daily
from cli_output import ensure_utf8_stdout


def main(argv=None):
    ensure_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("daily", nargs="?")
    parser.add_argument("--from-raw", metavar="YYYY-MM-DD")
    parser.add_argument("--roles", default="user")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--raw-root")
    parser.add_argument("--out")
    args = parser.parse_args(argv)

    if not args.daily and not args.from_raw:
        print("PROBLEM: give a daily file or --from-raw YYYY-MM-DD")
        return 2
    try:
        from config_loader import load_config

        config = load_config()
        if args.from_raw:
            from raw_source import messages_for_day

            roles = tuple(r.strip() for r in args.roles.split(",") if r.strip())
            root = args.raw_root or str(Path(config["memories_root"]) / "_raw")
            messages = messages_for_day(root, args.from_raw, roles)
            if args.limit:
                messages = messages[: args.limit]
            if not messages:
                print("PROBLEM: no archived messages for " + args.from_raw)
                return 3
        else:
            messages = read_daily(args.daily, config)

        claims = [c for chunk in compile_messages(messages, config) for c in chunk["claims"]]
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
