"""selector-audit（F4，只读）：诊断"这条记忆为什么没被召回"。

逐阶段输出目标记录在召回链路中的状态：
1. 索引中存在？（id / claim_id / revision_id）
2. namespace 主体隔离是否放行？
3. status 是否为 active？
4. search() 是否召回？（score / overlap / semantic 分项）
5. min_score 过滤？
6. 去重折叠？（同 claim_id / 同值）
7. top-k 截断位置？
8. budget 字符截断？
9. decay 时效系数？

只读：不写库、不改任何文件。输出 JSON，便于程序化处理。
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import rank as rank_mod
import search as S
import subjects as SU


def _find_record(con: sqlite3.Connection, ident: str) -> dict | None:
    row = con.execute(
        "SELECT * FROM records WHERE id = ? OR claim_id = ? OR revision_id = ? LIMIT 1",
        (ident, ident, ident),
    ).fetchone()
    return dict(row) if row else None


def audit(
    db_path: Path | str,
    ident: str,
    prompt: str,
    budget_chars: int = 1200,
    max_items: int = 6,
    min_score: float = 0.18,
    decay: bool = True,
    namespace: str | None = None,
) -> dict:
    """诊断单条记录在 recall 链路各阶段的命运。"""
    db_path = Path(db_path)
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        stages: list[dict] = []

        # 阶段 1：存在性
        rec = _find_record(con, ident)
        if rec is None:
            return {
                "id": ident,
                "prompt": prompt,
                "stages": [
                    {
                        "stage": "exists",
                        "pass": False,
                        "detail": "索引中无此 id/claim_id/revision_id",
                    }
                ],
                "verdict": "not_in_index",
            }
        target_id = rec["id"]
        stages.append(
            {
                "stage": "exists",
                "pass": True,
                "detail": f"claim_id={rec.get('claim_id')}, kind={rec.get('kind')}",
            }
        )

        # 阶段 2：namespace 主体隔离。subject_of 返回 None 是未分类，必须失败关闭。
        ns = SU.subject_of(rec)
        requester = namespace.strip() if isinstance(namespace, str) else namespace
        if requester == "":
            requester = None
        if ns is None:
            stages.append(
                {
                    "stage": "namespace",
                    "pass": False,
                    "namespace": "unknown",
                    "detail": "subject_of returned None; unclassified records are blocked",
                }
            )
            return {
                "id": ident,
                "prompt": prompt,
                "stages": stages,
                "verdict": "blocked_by_namespace",
            }
        if requester is not None:
            try:
                requester_ns = SU.validate_subject(requester)
            except ValueError as exc:
                stages.append(
                    {
                        "stage": "namespace",
                        "pass": False,
                        "namespace": ns,
                        "detail": str(exc),
                    }
                )
                return {
                    "id": ident,
                    "prompt": prompt,
                    "stages": stages,
                    "verdict": "blocked_by_namespace",
                }
            if ns != requester_ns:
                stages.append(
                    {
                        "stage": "namespace",
                        "pass": False,
                        "namespace": ns,
                        "detail": f"requester {requester_ns} does not match record namespace {ns}",
                    }
                )
                return {
                    "id": ident,
                    "prompt": prompt,
                    "stages": stages,
                    "verdict": "blocked_by_namespace",
                }
        stages.append({"stage": "namespace", "pass": True, "namespace": ns})

        # 阶段 3：status
        status = rec.get("status")
        if status != "active":
            stages.append(
                {
                    "stage": "status",
                    "pass": False,
                    "detail": f"status={status!r}，recall 只取 active",
                }
            )
            return {"id": ident, "prompt": prompt, "stages": stages, "verdict": "blocked_by_status"}
        stages.append({"stage": "status", "pass": True, "detail": "active"})

        # 阶段 4：search() 召回
        hits = S.search(
            con,
            prompt,
            limit=max_items * 3,
            status=("active",),
            namespace=ns if requester is None else requester_ns,
        )
        hit = next((h for h in hits if h["id"] == target_id), None)
        if hit is None:
            stages.append(
                {"stage": "search", "pass": False, "detail": f"未进候选池（共 {len(hits)} 条命中）"}
            )
            return {
                "id": ident,
                "prompt": prompt,
                "stages": stages,
                "verdict": "not_recalled_by_search",
            }
        stages.append(
            {
                "stage": "search",
                "pass": True,
                "score": round(hit["score"], 4),
                "overlap": round(hit.get("overlap", 0), 4),
                "semantic": round(hit.get("semantic", 0), 4),
                "rank_in_hits": [h["id"] for h in hits].index(target_id) + 1,
                "hits_total": len(hits),
            }
        )

        # 阶段 5：decay 重排（信息性）
        ordered = hits
        decay_info = None
        if decay:
            ranked = rank_mod.rank([(r, r["score"]) for r in hits])
            ordered = [r for r, _b, _a in ranked]
            decay_info = rank_mod.explain(rec)
            stages.append({"stage": "decay", "pass": True, **decay_info})

        # 阶段 6/7/8：复刻 recall 的去重 + min_score + top-k + budget
        seen_claims, seen_values, picked = set(), set(), []
        fate = None
        for h in ordered:
            if h["score"] < min_score:
                if h["id"] == target_id:
                    fate = "filtered_by_min_score"
                continue
            key = h.get("claim_id")
            value_key = re.sub(r"\s+", "", (h.get("value") or ""))[:40]
            if key in seen_claims or value_key in seen_values:
                if h["id"] == target_id:
                    fate = "folded_by_dedup"
                continue
            seen_claims.add(key)
            seen_values.add(value_key)
            picked.append(h)
            if len(picked) >= max_items:
                # 目标还没进来就被截断
                if not any(p["id"] == target_id for p in picked):
                    fate = "cut_by_top_k"
                break

        if fate is None:
            if not any(p["id"] == target_id for p in picked):
                fate = "cut_by_top_k"
        stages.append(
            {
                "stage": "dedup_topk",
                "pass": fate is None,
                "detail": fate or f"进入 top-{len(picked)}",
                "position": next(
                    (i + 1 for i, p in enumerate(picked) if p["id"] == target_id), None
                ),
            }
        )

        # 阶段 8：budget
        if fate is None:
            used = 0
            survived = True
            for p in picked:
                line_len = len(f"- [{p.get('kind')}] {(p.get('value') or '')[:90]}") + 1
                if line_len + used + 1 > budget_chars:
                    if p["id"] == target_id:
                        survived = False
                    break
                used += line_len + 1
            stages.append(
                {
                    "stage": "budget",
                    "pass": survived,
                    "detail": "budget 内" if survived else "被 budget 截断",
                }
            )
            if not survived:
                fate = "cut_by_budget"

        verdict = "injected" if fate is None else fate
        return {"id": ident, "prompt": prompt, "stages": stages, "verdict": verdict}
    finally:
        con.close()


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="Stellamem selector-audit (read-only)")
    parser.add_argument("--db", required=True)
    parser.add_argument("--id", required=True, help="记录 id / claim_id / revision_id")
    parser.add_argument("--prompt", "--query", required=True, help="召回查询串")
    parser.add_argument(
        "--namespace",
        default=None,
        help="请求方 namespace（user/persona/viewer）；与记录不一致则 blocked_by_namespace",
    )
    parser.add_argument(
        "--subject",
        default=None,
        help="--namespace 的别名，请求方主体",
    )
    parser.add_argument("--budget", type=int, default=1200)
    parser.add_argument("--max-items", type=int, default=6)
    parser.add_argument("--min-score", type=float, default=0.18)
    parser.add_argument("--no-decay", action="store_true")
    args = parser.parse_args(argv)
    requester = args.namespace if args.namespace is not None else args.subject
    if args.namespace is not None and args.subject is not None and args.namespace != args.subject:
        parser.error("--namespace and --subject disagree")

    result = audit(
        args.db,
        args.id,
        args.prompt,
        budget_chars=args.budget,
        max_items=args.max_items,
        min_score=args.min_score,
        decay=not args.no_decay,
        namespace=requester,
    )
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
