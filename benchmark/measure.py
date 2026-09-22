"""Synthetic canonical parse/lookup scaling; no extraction-quality claims."""

import argparse
import json
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))
from storage import load_records, render_records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", nargs="+", type=int, default=[10, 100, 1000, 10000, 100000])
    parser.add_argument("--out")
    args = parser.parse_args()
    results = []
    for n in args.sizes:
        with tempfile.TemporaryDirectory(prefix="stellamem-benchmark-") as temp:
            path = Path(temp) / "synthetic.md"
            path.write_text(
                render_records(
                    "",
                    [
                        {
                            "schema_version": 2,
                            "claim_id": "c-" + str(i),
                            "status": "active",
                            "predicate": "synthetic",
                            "value": "fictional " + str(i),
                        }
                        for i in range(n)
                    ],
                ),
                encoding="utf-8",
            )
            tracemalloc.start()
            start = time.perf_counter()
            _, records = load_records(path)
            parse_ms = (time.perf_counter() - start) * 1000
            start = time.perf_counter()
            index = {r["claim_id"]: r for r in records}
            index_ms = (time.perf_counter() - start) * 1000
            start = time.perf_counter()
            for _ in range(1000):
                index.get("c-" + str(n // 2))
            lookup_ms = (time.perf_counter() - start) * 1000 / 1000
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            results.append(
                {
                    "memories": n,
                    "parse_ms": round(parse_ms, 3),
                    "index_ms": round(index_ms, 3),
                    "lookup_ms": round(lookup_ms, 5),
                    "peak_mib": round(peak / 1024 / 1024, 3),
                    "file_bytes": path.stat().st_size,
                }
            )
    data = json.dumps({"kind": "synthetic-storage-only", "results": results}, indent=2)
    if args.out:
        Path(args.out).write_text(data + "\n", encoding="utf-8")
    print(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
