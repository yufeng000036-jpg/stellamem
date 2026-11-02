"""Four separate metrics, because one number hid four different failures.

  1. extraction recall      — did the compiler find the memory at all
  2. extraction precision    — how much of what it emitted was real
  3. write accuracy          — of the claims that reached canonical memory,
                               how many match gold (wrong writes are worse than
                               missing ones)
  4. injection availability  — of the written claims, how many can the adapter
                               actually surface for a relevant prompt

Usage:
    python benchmark/report.py --pred-dir <dir> [--gold-dir <dir>] [--out file]
"""

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import match  # noqa: E402

BENCH = Path(os.environ.get("STELLAMEM_BENCH_DIR", "<USER_HOME>/.dsh/memories/benchmark"))


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def eligible(claim):
    """Mirror of the projection gate, kept intentionally strict."""
    return (
        claim.get("status") == "active"
        and claim.get("source") == "user"
        and claim.get("evidence_verified") is True
        and claim.get("write_policy") == "allow"
        and claim.get("injection_policy") in ("always", "recall")
        and claim.get("sensitivity") in ("PUBLIC", "PRIVATE")
        and claim.get("kind") not in ("identity", "user-core", "relationship")
        and not any(claim.get(k) for k in ("sensitive", "suspicious", "sarcasm", "temporary", "outdated"))
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pred-dir", required=True)
    parser.add_argument("--gold-dir", default=str(BENCH / "golden"))
    parser.add_argument("--samples", default=str(BENCH / "samples"))
    parser.add_argument("--out")
    args = parser.parse_args()

    pred_dir = Path(args.pred_dir)
    gold_dir = Path(args.gold_dir)
    totals = {"expected": 0, "predicted": 0, "matched": 0, "missed": 0, "extra": 0}
    written = {"count": 0, "matched": 0, "injectable": 0}
    per_sample = []

    for pred_path in sorted(pred_dir.glob("*.json")):
        name = pred_path.stem
        gold_path = gold_dir / f"{name}_confirmed.json"
        if not gold_path.exists():
            continue
        predictions = load_json(pred_path).get("claims", [])
        gold = load_json(gold_path).get("expected_claims", [])
        p = [{"text": str(c.get("value") or ""), "raw": c} for c in predictions]
        g = [{"text": str(c.get("value") or ""), "raw": c} for c in gold]
        pairs, missed, extra = match.match(
            p, g, use_embeddings=bool(os.environ.get("STELLAMEM_EMBED_URL"))
        )

        totals["expected"] += len(g)
        totals["predicted"] += len(p)
        totals["matched"] += len(pairs)
        totals["missed"] += len(missed)
        totals["extra"] += len(extra)

        matched_pred = {pair["predicted"] for pair in pairs}
        for i, item in enumerate(p):
            if item["raw"].get("status") != "active":
                continue
            written["count"] += 1
            if i in matched_pred:
                written["matched"] += 1
            if eligible(item["raw"]):
                written["injectable"] += 1

        per_sample.append({
            "sample": name,
            "expected": len(g),
            "predicted": len(p),
            "matched": len(pairs),
            "missed": len(missed),
            "extra": len(extra),
        })

    def ratio(a, b):
        return round(a / b, 4) if b else 0.0

    summary = {
        "samples": len(per_sample),
        "extraction": {
            "expected": totals["expected"],
            "predicted": totals["predicted"],
            "matched": totals["matched"],
            "recall": ratio(totals["matched"], totals["expected"]),
            "precision": ratio(totals["matched"], totals["predicted"]),
            "missed": totals["missed"],
            "extra": totals["extra"],
        },
        "write_accuracy": {
            "written": written["count"],
            "matched": written["matched"],
            "accuracy": ratio(written["matched"], written["count"]),
        },
        "injection_availability": {
            "written": written["count"],
            "injectable": written["injectable"],
            "rate": ratio(written["injectable"], written["count"]),
        },
        "per_sample": per_sample,
    }

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print("written to", args.out)


if __name__ == "__main__":
    main()
