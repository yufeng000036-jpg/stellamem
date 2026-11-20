#!/usr/bin/env python3
"""Denominator fingerprint for LongMemEval result files.

The frozen runner (evaluate.py) is byte-locked by freeze.py and must
not change; this module is the post-hoc companion. Given a result JSON
produced by any run, it computes the fingerprint of the scored
denominator -- sha256 over the canonical JSON of the sorted per-case
id hashes -- so two runs can prove mechanically that they scored the
same set (a 482/500 run and a 500/500 run cannot be silently compared
as equals). It can also stamp the digest into a copy of the result.

Usage:
    python3 denominator.py RESULTS.json            # print digest + n
    python3 denominator.py RESULTS.json --stamp    # write RESULTS.denominator.json
"""

import hashlib
import json
import sys
from pathlib import Path


def _canon(value):
    """Canonical JSON: dict keys sorted recursively, arrays keep order,
    nulls preserved."""
    if isinstance(value, dict):
        return {k: _canon(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        return [_canon(v) for v in value]
    return value


def denominator_digest(rows):
    """rows: result['cases'] (each carries case_id_sha256)."""
    ids = sorted(r["case_id_sha256"] for r in rows)
    payload = json.dumps(_canon(ids), ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def stamp(result: dict) -> dict:
    out = dict(result)
    cases = result.get("cases") or []
    out["denominator_n"] = len(cases)
    out["denominator_digest"] = denominator_digest(cases)
    return out


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    do_stamp = "--stamp" in argv
    argv = [a for a in argv if a != "--stamp"]
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    path = Path(argv[0])
    result = json.loads(path.read_text(encoding="utf-8"))
    stamped = stamp(result)
    print(
        json.dumps(
            {
                "denominator_n": stamped["denominator_n"],
                "denominator_digest": stamped["denominator_digest"],
            },
            ensure_ascii=False,
        )
    )
    if do_stamp:
        out_path = path.with_suffix(".denominator.json")
        out_path.write_text(
            json.dumps(stamped, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"stamped copy: {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
