"""One million actual persistent records through the production storage path."""

import json
import time
import statistics
import random
import pathlib
import sys
import sqlite3
import concurrent.futures

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from store import Store, Principal

ROOT = pathlib.Path(__file__).resolve().parents[1]
DB = ROOT / "work/million.sqlite3"
s = Store(DB)
p = Principal(tenant="capacity-test")
n = 1000000
existing = s.db.execute("SELECT count(*) FROM memories").fetchone()[0]
started = time.monotonic()
for start in range(existing, n, 1000):
    with s.transaction():
        for i in range(start, min(start + 1000, n)):
            data = {
                "title": "Research project %07d" % i,
                "content": ("key%07d " % i)
                + "Long term research decision. Preserve source evidence, participant identity and historical corrections. Local durable storage supports future project continuity. "
                + "\u957f\u671f\u8bb0\u5fc6\u9879\u76ee\u51b3\u7b56\uff0c\u4fdd\u7559\u6765\u6e90\u548c\u65f6\u95f4\u3002",
                "kind": "project",
                "scope": "public" if i % 10 == 0 else "owner",
                "source": {"reference": "synthetic:" + str(i), "evidence_verified": False},
                "occurred_at": "2024-01-01T00:00:00Z",
            }
            s._insert(p, data, ident="bench-%07d" % i, status="active")
    if (start + 1000) % 50000 == 0:
        print("stored", start + 1000, "seconds", round(time.monotonic() - started, 1), flush=True)
insert_s = time.monotonic() - started
s.db.execute("INSERT INTO memory_fts(memory_fts) VALUES('optimize')")
s.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
count = s.stats(p)["total"]
assert count == n
s.close()
s = Store(DB)
assert s.stats(p)["total"] == n
random.seed(13)
lat = []
hits = 0
for i in random.sample(range(n), 100):
    t = time.perf_counter()
    rows = s.search(p, "key%07d" % i, limit=10)["items"]
    lat.append((time.perf_counter() - t) * 1000)
    hits += bool(rows and rows[0]["id"] == "bench-%07d" % i)
broad = []
for _ in range(20):
    t = time.perf_counter()
    r = s.search(p, "research", limit=30)
    broad.append((time.perf_counter() - t) * 1000)
    assert len(r["items"]) == 30
pub = Principal(tenant="capacity-test", role="public")
no_leak = True
for i in range(1, 20):
    r = s.search(pub, "key%07d" % i)["items"]
    no_leak &= all(x["scope"] == "public" for x in r)


def query(i):
    t = time.perf_counter()
    s.search(p, "key%07d" % (i * 997), limit=10)
    return (time.perf_counter() - t) * 1000


with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
    concurrent = list(ex.map(query, range(80)))
t = time.perf_counter()
backup = s.backup(p, ROOT / "work/capacity-backup")
backup_s = time.perf_counter() - t
check = sqlite3.connect(backup["path"])
assert check.execute("SELECT count(*) FROM memories").fetchone()[0] == n
assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
check.close()


def quant(a):
    return {
        "p50_ms": round(statistics.median(a), 3),
        "p95_ms": round(sorted(a)[int((len(a) - 1) * 0.95)], 3),
        "max_ms": round(max(a), 3),
    }


report = {
    "records": count,
    "model_required": False,
    "synthetic": True,
    "resumed_from": existing,
    "insert_seconds_this_run": round(insert_s, 2),
    "database_bytes": DB.stat().st_size,
    "exact_query_top1": hits / 100,
    "exact_query": quant(lat),
    "common_query": quant(broad),
    "concurrent_8": quant(concurrent),
    "public_scope_no_leak": no_leak,
    "restart_count_verified": True,
    "backup_restore_count_and_integrity": True,
    "backup_seconds": round(backup_s, 2),
    "limitations": "Synthetic bounded-length memories; not a semantic accuracy benchmark or real multi-platform test.",
}
(ROOT / "reports/capacity-million.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report), flush=True)
s.close()
