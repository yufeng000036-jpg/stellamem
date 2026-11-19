"""Matched v4 evaluation: one B selection, followed by at most one independent support check."""

import argparse
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import sys
from statistics import median
from time import perf_counter

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from benchmark.jarvis_semantic import scale_v2 as scale
from benchmark.jarvis_semantic import support_public as pilot
from benchmark.jarvis_semantic.parser_replay import classify
from benchmark.jarvis_semantic.reader_v3 import arms_for_projection
from benchmark.jarvis_semantic.scale_corpus import group, fingerprint

PROTOCOL = dict(
    study="support-v4",
    selector_sha256=pilot.digest(pilot.OLD),
    checker_sha256=pilot.digest(pilot.CHECK),
    parser_contract="PR153-bracket-ca7ccac5",
    A_primary="shared bracket",
    A_sensitivity="strict trial, same raw output",
    checker_tiers=[1, 2, 3],
    product_tiers=[2, 3],
    p95_method="nearest rank",
)
ROLES = ("entity_parent", "attribute_parent", "other")
UNKNOWN = "insufficient_evidence"


def validate(data, blind):
    scale.check_base(data, blind)
    active = {n["id"] for n in data["notes"] if n["status"] == "active"}
    pairs = []
    for q in data["questions"]:
        flag = q.get("cross_negative", False)
        refs = q.get("cross_sources")
        if type(flag) is not bool or (not flag and refs is not None):
            raise ValueError("invalid cross-negative annotation")
        if flag:
            if (
                group(q) != "N"
                or not isinstance(refs, list)
                or len(refs) != 2
                or not all(isinstance(v, str) for v in refs)
                or len(set(refs)) != 2
                or not set(refs) <= active
            ):
                raise ValueError(
                    "cross_sources must be [active entity-parent ID, active attribute-parent ID]"
                )
            pairs.append(frozenset(refs))
    if blind and (len(pairs) != 8 or len(set(pairs)) != 8):
        raise ValueError("expected eight cross-negatives for eight entity pairs")


def outcome(row, selected, status):
    return dict(
        row,
        selected=selected,
        correct=selected == row["gold"],
        reader_status=status,
        negative_answer_with_real_source=row["gold"] is None and selected is not None,
        citation_valid=True if selected else None,
    )


def selection(row, q, lookup):
    form, strict, selected = classify(row["raw_output"], row["reader_candidates"])
    if strict != row["selected"]:
        raise ValueError("strict selector replay mismatch")
    refs = [lookup[k] for k in q.get("cross_sources", [])]
    parents = {
        role: dict(claim_id=cid, delivered=cid in row["reader_candidates"])
        for role, cid in zip(ROLES[:2], refs)
    }
    role = None
    if refs and selected:
        role = next((r for r, p in parents.items() if p["claim_id"] == selected), "other")
    row = outcome(row, selected, "selected" if selected else row["reader_status"])
    return dict(
        row, selection_form=form, strict_selected=strict, parents=parents, selected_parent_role=role
    )


def verify(row, q, home, active, args):
    selected = row["selected"]
    reply = None
    decision = "SKIP"
    seconds = 0.0
    source_hash = None
    if selected:
        rec = active[selected]
        if selected not in row["reader_candidates"] or not scale.frozen.mem.verified(
            home / "memory", rec
        ):
            raise scale.BoundaryFailure("before_support", "invalid_citation")
        payload = {"问题": q["text"], "原文": rec["value"]}
        source_hash = fingerprint(payload)
        reply, seconds = pilot.chat(args.reader_port, args.reader_model, pilot.CHECK, payload, 384)
        decision = reply.strip() if reply.strip() in ("YES", "NO") else "INVALID"
    kept = selected if decision == "YES" else None
    status = row["reader_status"] if decision in ("YES", "SKIP") else "checker_" + decision.lower()
    return dict(
        outcome(row, kept, status),
        arm="B1",
        upstream_selected=selected,
        upstream_parent_role=row["selected_parent_role"],
        selected_parent_role=row["selected_parent_role"] if kept else None,
        reused_selector=True,
        reader_calls=0,
        checker_calls=int(selected is not None),
        checker_payload_hash=source_hash,
        checker_raw=reply,
        checker_verdict=decision,
        checker_seconds=seconds,
        end_to_end_seconds=row["end_to_end_seconds"] + seconds,
    )


def effects(rows):
    return dict(
        n=len(rows),
        explicit_no=[r["id"] for r in rows if r["checker_verdict"] == "NO"],
        invalid=[r["id"] for r in rows if r["checker_verdict"] == "INVALID"],
        yes=[r["id"] for r in rows if r["checker_verdict"] == "YES"],
    )


def summarize(triples):
    b1 = [p["B1"] for p in triples]
    checked = [r for r in b1 if r["checker_calls"]]
    errors = [r for r in checked if r["cross_negative"]]
    correct = [r for r in checked if r["gold"] and r["gold"] == r["upstream_selected"]]
    blocked = effects(errors)
    correct_effects = effects(correct)
    lost = len(correct_effects["explicit_no"]) + len(correct_effects["invalid"])
    status = (
        UNKNOWN
        if len(errors) < 4
        else ("pass" if 2 * len(blocked["explicit_no"]) >= len(errors) and lost <= 1 else "fail")
    )
    delays = sorted(r["checker_seconds"] for r in checked)
    return dict(
        contribution=status,
        cross_errors=blocked,
        cross_errors_by_parent={
            role: effects([r for r in errors if r["upstream_parent_role"] == role])
            for role in ROLES
        },
        correct_selections=correct_effects,
        positive_loss=lost,
        checker_calls=len(checked),
        decisions=dict(Counter(r["checker_verdict"] for r in b1)),
        median_seconds=median(delays) if delays else None,
        p95_seconds=delays[(95 * len(delays) + 99) // 100 - 1] if delays else None,
    )


def product_metrics(triples, strict=False):
    pairs = []
    for p in triples:
        a = p["A"]
        if strict:
            picked = a["strict_selected"]
            a = outcome(
                a,
                picked,
                "invalid_model_selection" if picked != a["selected"] else a["reader_status"],
            )
        pairs.append(dict(A=a, B=p["B1"]))
    result = scale.summarize(pairs)
    result["arms"]["B1"] = result["arms"].pop("B")
    return result


def combined(statuses):
    if UNKNOWN in statuses:
        return UNKNOWN
    return "pass" if all(s == "pass" for s in statuses) else "fail"


def study(data, args, emit):
    validate(data, args.blind)
    verdicts = []
    gates = []
    strict_gates = []
    violations = dict(cross_subject=0, revoked=0, invalid_citation=0)
    with closing(scale.corpora(data, args, emit)) as tiers:
        for tier, home, lookup, foreign, old, extra in tiers:
            try:
                active, index, elapsed = scale.build_index(
                    home, set(lookup.values()) - foreign - old, foreign, old, args
                )
                emit(dict(type="index", tier=tier, items=len(index), seconds=elapsed))
                emit(
                    dict(
                        type="sources",
                        tier=tier,
                        claim_ids=lookup,
                        revision_to_claim={r["revision_id"]: k for k, r in active.items()},
                    )
                )
                labels = {lookup[r["id"]]: r.get("transform", r["kind"]) for r in extra}
                triples = []
                for i, q in enumerate(data["questions"]):
                    start = perf_counter()
                    vector = scale.frozen.embed([q["text"]], args.embed_model)[0]
                    tq = perf_counter() - start
                    start = perf_counter()
                    order = sorted(
                        index, key=lambda p: (-sum(x * y for x, y in zip(vector, p[1])), p[0])
                    )
                    ranks = [rid for rid, _ in order[:8]]
                    ts = perf_counter() - start
                    p = {}
                    for channel in ("A", "B") if i % 2 == 0 else ("B", "A"):
                        arm = "A" if channel == "A" else "B0"
                        raw = arms_for_projection(
                            home,
                            q,
                            channel,
                            ranks,
                            active,
                            foreign,
                            old,
                            lookup,
                            args,
                            tq,
                            ts,
                            (arm,),
                        )[arm]
                        row = selection(raw, q, lookup)
                        row["reader_source_types"] = [
                            labels.get(k, "base") for k in row["reader_candidates"]
                        ]
                        p[arm] = row
                        emit(dict(row, tier=tier))
                    p["B1"] = verify(p["B0"], q, home, active, args)
                    emit(dict(p["B1"], tier=tier))
                    triples.append(p)
            except scale.BoundaryFailure as err:
                violations[err.kind] += 1
                emit(
                    dict(
                        type="boundary_failure",
                        tier=tier,
                        stage=err.stage,
                        violations=violations,
                        contribution="fail",
                        product_passed=False,
                        passed=False,
                    )
                )
                return False
            check = summarize(triples)
            product = product_metrics(triples)
            alt = product_metrics(triples, strict=True)
            emit(
                dict(
                    type="metrics", tier=tier, checker=check, product=product, product_strict_A=alt
                )
            )
            verdicts.append(check["contribution"])
            gates.append(all(product["gates"].values()))
            strict_gates.append(all(alt["gates"].values()))
    contribution = combined(verdicts) if args.blind else "public_only"
    product_passed = all(gates[1:]) if args.blind else None
    passed = contribution == "pass" and product_passed if args.blind else None
    emit(
        dict(
            type="complete",
            triples=3 * len(data["questions"]),
            rows=9 * len(data["questions"]),
            contribution=contribution,
            product_passed=product_passed,
            strict_A_product_passed=all(strict_gates[1:]) if args.blind else None,
            checker_by_tier=verdicts,
            violations=violations,
            passed=passed,
        )
    )
    return passed


if __name__ == "__main__":
    if "--validate-map" in sys.argv:
        cli = argparse.ArgumentParser()
        cli.add_argument("--validate-map", required=True, type=Path)
        path = cli.parse_args().validate_map
        validate(json.loads(path.read_text("utf-8-sig")), True)
        print("v4 mapping structure verified:", scale.frozen.sha(path))
    else:
        scale.run_cli(study, "249c06ed", "7bc8", PROTOCOL)
