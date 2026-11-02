"""第二阶段基准：旧关键词扫描 vs 新混合检索。

指标：recall@1 / recall@5 / MRR / p50 / p95 / 单次注入字符。
样本：从生产记忆根随机抽 30 条，取正文中段 6 字作为查询（针）。
旧方案：把 markdown 全量读进内存，按子串命中 + 时间倒序取前 5（复刻旧 projection 的口径）。
"""

import json
import random
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(r"<USER_HOME>/.dsh/memories")
DB = Path(r"./stellamem-data/index.db")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

from indexer import Index  # noqa: E402
import search as S  # noqa: E402
from storage import load_records  # noqa: E402

MANAGED = [
    ("core/preferences.md", "core"),
    ("core/user-core.md", "core"),
    ("long-term/projects.md", "long-term"),
    ("long-term/decisions.md", "long-term"),
    ("long-term/lessons.md", "long-term"),
    ("semantic/facts.md", "semantic"),
    ("semantic/profile.md", "semantic"),
    ("system/memory-inbox.md", "inbox"),
]


def load_all():
    rows = []
    for rel, layer in MANAGED:
        path = ROOT / rel
        if not path.exists():
            continue
        _, records = load_records(path)
        for rec in records:
            if rec.get("redacted") or not rec.get("value"):
                continue
            prov = rec.get("provenance") or {}
            rows.append(
                {
                    "id": f"{rec.get('claim_id')}:{rec.get('revision_id')}",
                    "kind": rec.get("kind"),
                    "value": rec.get("value") or "",
                    "evidence": rec.get("evidence") or "",
                    "occurred_at": prov.get("timestamp") or rec.get("observed_at") or "",
                    "layer": layer,
                }
            )
    return rows


def baseline(rows, query, limit=5):
    """旧口径：子串命中 + 时间倒序，不看类型也不看语义。"""
    hits = [r for r in rows if query in r["value"] or query in r["evidence"]]
    hits.sort(key=lambda r: r["occurred_at"], reverse=True)
    return hits[:limit]


def normalize(text):
    import re

    return re.sub(r"\s+", "", text or "")[:60]


def evaluate(pick, rows, queries, label):
    # 查询是正文片段：任何包含该片段的记录都算正确答案。
    accept = {}
    for rec in rows:
        accept.setdefault(normalize(rec["value"]), set()).add(rec["id"])
    contains = {}
    for query, target, value in queries:
        contains[query] = {r["id"] for r in rows if query in r["value"] or query in r["evidence"]}
    r1 = r5 = 0
    rr = 0.0
    lat = []
    chars = []
    for query, target, target_value in queries:
        t = time.perf_counter()
        got = pick(query)
        lat.append((time.perf_counter() - t) * 1000)
        ids = [g.get("id") for g in got]
        valid = contains.get(query) or accept.get(normalize(target_value), set())
        ok = set(ids) & valid
        if ids and ids[0] in valid:
            r1 += 1
        if ok:
            r5 += 1
            first = next(i for i, rid in enumerate(ids) if rid in ok)
            rr += 1.0 / (first + 1)
        chars.append(sum(len(str(g.get("value") or "")) for g in got))
    n = len(queries)
    lat_sorted = sorted(lat)
    return {
        "label": label,
        "n": n,
        "recall@1": round(r1 / n, 3),
        "recall@5": round(r5 / n, 3),
        "mrr": round(rr / n, 3),
        "p50_ms": round(statistics.median(lat_sorted), 2),
        "p95_ms": round(lat_sorted[max(0, int(n * 0.95) - 1)], 2),
        "avg_chars": round(sum(chars) / n, 1),
    }


def main():
    rows = load_all()
    idx = Index(DB)
    meta = idx.build(ROOT)
    print("index:", {k: meta[k] for k in ("records", "docs")})

    random.seed(20261101)
    pool = [r for r in rows if len(r["value"]) >= 14 and len(set(r["value"])) >= 8]
    picked = random.sample(pool, 30)
    queries = []
    for rec in picked:
        v = rec["value"]
        mid = max(0, (len(v) - 6) // 2)
        queries.append((v[mid : mid + 6], rec["id"], v))

    old = evaluate(lambda q: baseline(rows, q), rows, queries, "baseline_keyword")
    new = evaluate(
        lambda q: S.search(idx.con, q, limit=5, status=("active", "pending")), rows, queries, "hybrid_index"
    )

    # 第二组：打散词序（用户提问不会逐字复述原文）
    import re as _re

    frag = []
    for rec in picked:
        tokens = []
        for run in _re.findall(r"[\u4e00-\u9fff]{4,}", rec["value"]):
            step = max(2, len(run) // 4)
            tokens.extend(run[i : i + 2] for i in range(0, len(run) - 1, step))
        if len(tokens) >= 3:
            frag.append((" ".join(tokens[:3]), rec["id"], rec["value"]))
    old_f = evaluate(lambda q: baseline(rows, q), rows, frag, "baseline_keyword")
    new_f = evaluate(
        lambda q: S.search(idx.con, q, limit=5, status=("active", "pending")), rows, frag, "hybrid_index"
    )

    out = {
        "exact_match_queries": len(queries),
        "exact": {"old": old, "new": new},
        "fragmented_queries": len(frag),
        "fragmented": {"old": old_f, "new": new_f},
        "built_at": meta["built_at"],
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))
    Path("./stellamem-data/bench_phase2.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return out


if __name__ == "__main__":
    main()
