"""Token 看板 / 预算治理（F2，只读）。

记忆管家做法：dashboard 看板、tiktoken 真实计量、三色预算告警、
超预算自动压实、throttle 一键压预算。

Stellamem 适配（刻意不同）：
- 只读：统计 + 建议，不改任何数据；
- 不做"自动压实/throttle"：记忆的删除与压实必须走人工复核，
  工具只输出"超预算时建议优先复核的大户清单"；
- 计量优先 tiktoken，没有则用 CJK 启发式估算，并明确标注"估算"。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import subjects as SU

DEFAULT_BUDGET_TOKENS = 30000

# 三色告警阈值（占预算比例）
GREEN_BELOW = 0.70
YELLOW_BELOW = 1.00


def _tiktoken_counter():
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        return lambda s: len(enc.encode(s or "")), False
    except Exception:
        return None, True


def estimate_tokens(text: str) -> tuple[int, bool]:
    """返回 (token数, 是否为估算)。优先 tiktoken，否则 CJK 启发式。"""
    counter, is_estimate = _tiktoken_counter()
    text = text or ""
    if counter is not None:
        return counter(text), False
    # 启发式：CJK 字符按 1.5 token，ASCII 按 4 字符 1 token
    cjk = sum(
        1
        for ch in text
        if "\u4e00" <= ch <= "\u9fff"
        or "\u3400" <= ch <= "\u4dbf"
        or "\uf900" <= ch <= "\ufaff"
        or "\u3040" <= ch <= "\u30ff"
        or "\uac00" <= ch <= "\ud7af"
    )
    ascii_chars = max(0, len(text) - cjk)
    return int(cjk * 1.5 + ascii_chars / 4), True


def _record_text(rec: dict) -> str:
    parts = [rec.get("value") or "", rec.get("subject") or "", rec.get("kind") or ""]
    return "\n".join(p for p in parts if p)


def _canonical_by_claim(con: sqlite3.Connection) -> dict[str, dict]:
    """Prefer the memory-root record when meta.root is set.

    The index may also store namespace. Canonical markdown still wins so a
    hand-built database without that column keeps the same labels.
    """
    try:
        row = con.execute("SELECT value FROM meta WHERE key='root'").fetchone()
    except sqlite3.Error:
        return {}
    if not row or not row[0]:
        return {}
    root = Path(str(row[0]))
    if not root.is_dir():
        return {}
    try:
        import indexer
        from storage import load_records
    except ImportError:
        return {}
    paths = [root / rel for rel, _layer in indexer.MANAGED]
    try:
        extra = [
            item[0]
            for item in con.execute("SELECT DISTINCT file FROM records WHERE file IS NOT NULL")
        ]
    except sqlite3.Error:
        extra = []
    paths.extend(root / rel for rel in extra)
    found: dict[str, dict] = {}
    seen = set()
    for path in paths:
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        try:
            _, records = load_records(path)
        except (OSError, ValueError):
            continue
        for rec in records:
            claim_id = rec.get("claim_id")
            if claim_id:
                found[str(claim_id)] = rec
    return found


def _namespace_label(row: dict, canonical: dict[str, dict]) -> str:
    claim_id = row.get("claim_id")
    source = canonical.get(str(claim_id)) if claim_id else None
    if source is None:
        source = row
    return SU.subject_of(source) or "(unclassified)"


def dashboard(
    db_path: Path | str, budget_tokens: int = DEFAULT_BUDGET_TOKENS, top_n: int = 10
) -> dict:
    """只读统计。返回看板数据；不写库、不改文件。"""
    db_path = Path(db_path)
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in con.execute("SELECT * FROM records")]
        canonical = _canonical_by_claim(con)
    finally:
        con.close()

    total = len(rows)
    by_namespace: dict[str, int] = {}
    by_status: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    token_rows = []
    estimated_any = False
    for rec in rows:
        ns = _namespace_label(rec, canonical)
        by_namespace[ns] = by_namespace.get(ns, 0) + 1
        st = rec.get("status") or "(none)"
        by_status[st] = by_status.get(st, 0) + 1
        kd = rec.get("kind") or "(none)"
        by_kind[kd] = by_kind.get(kd, 0) + 1
        toks, is_est = estimate_tokens(_record_text(rec))
        estimated_any = estimated_any or is_est
        token_rows.append(
            {
                "id": rec.get("claim_id") or rec.get("id"),
                "kind": rec.get("kind"),
                "namespace": ns,
                "tokens": toks,
            }
        )
    total_tokens = sum(r["tokens"] for r in token_rows)
    token_rows.sort(key=lambda r: r["tokens"], reverse=True)

    ratio = (total_tokens / budget_tokens) if budget_tokens > 0 else 0.0
    if ratio < GREEN_BELOW:
        level = "green"
    elif ratio < YELLOW_BELOW:
        level = "yellow"
    else:
        level = "red"

    return {
        "records": total,
        "by_namespace": by_namespace,
        "by_status": by_status,
        "by_kind": by_kind,
        "tokens": {
            "total": total_tokens,
            "estimated": estimated_any,  # True = 启发式估算，非精确计量
            "budget": budget_tokens,
            "usage_ratio": round(ratio, 4),
            "alert": level,
        },
        "top_consumers": token_rows[:top_n],
        # 超预算时的只读建议：优先复核这些大户（不自动删、不自动压）
        "review_suggestions": ([r["id"] for r in token_rows[:top_n]] if level == "red" else []),
        "note": "只读看板；压实/删除必须走人工复核，本工具不执行。",
    }


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="Stellamem token dashboard (read-only)")
    parser.add_argument("--db", required=True, help="index.db 路径")
    parser.add_argument("--budget", type=int, default=DEFAULT_BUDGET_TOKENS)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    data = dashboard(args.db, budget_tokens=args.budget, top_n=args.top)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=1))
        return
    t = data["tokens"]
    est = "（估算）" if t["estimated"] else "（tiktoken 精确）"
    print(f"记录总数: {data['records']}")
    print(f"按 namespace: {json.dumps(data['by_namespace'], ensure_ascii=False)}")
    print(f"按 status:    {json.dumps(data['by_status'], ensure_ascii=False)}")
    print(
        f"Token 总量: {t['total']}{est} / 预算 {t['budget']}"
        f"（{t['usage_ratio'] * 100:.1f}%）[{t['alert']}]"
    )
    print("Top 消耗:")
    for r in data["top_consumers"][:5]:
        print(f"  {r['tokens']:>6}  {r['id']}  [{r['namespace']}/{r['kind']}]")
    if data["review_suggestions"]:
        print("超预算：建议人工优先复核以上大户（本工具不自动删除/压实）。")
    print(data["note"])


if __name__ == "__main__":
    main()
