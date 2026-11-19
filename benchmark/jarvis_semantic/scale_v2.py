"""Local, paired scale experiment on a sealed v2 packet or the public fixture."""

import argparse
from collections import Counter
import hashlib
import http.client
import json
import os
import shutil
from statistics import median
from pathlib import Path
import subprocess
import sys
import tempfile
from time import perf_counter
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from benchmark.jarvis_semantic import run as frozen
from benchmark.jarvis_semantic.scale_corpus import (
    check_base,
    distractors,
    fingerprint,
    group,
    SIZES,
)
from claim_normalizer import claim_id, claim_revision_id
from storage import load_records, render_records

CANARIES = {"persona": "排练档案的专属封签是浅金弧线。", "viewer": "展区来宾的专属封签是紫色曲线。"}


class BoundaryFailure(Exception):
    def __init__(self, stage, kind):
        self.stage, self.kind = stage, kind
        super().__init__(f"{kind} at {stage}")


def fixture_rows(root, extra, base_ids):
    """Approved gold uses real Jarvis operations. Bulk synthetic distractors are fixtures only."""
    path = root / "semantic/facts.md"
    prefix, records = load_records(path)
    template = next(r for r in records if r["status"] == "active")
    mapping = dict(base_ids)
    foreign = set()
    for row in extra + [dict(id="@" + k, text=v, namespace=k) for k, v in CANARIES.items()]:
        value = row["text"]
        digest = hashlib.sha256(value.encode()).hexdigest()
        ref = "notes/" + digest[:32] + ".txt"
        with (root / ref).open("xb") as out:
            out.write(value.encode())
        rec = dict(
            template,
            predicate=value,
            value=value,
            evidence=value,
            provenance=dict(
                template["provenance"],
                source_ref=ref,
                content_hash=digest,
                timestamp="2026-01-01T10:00:00+00:00",
            ),
        )
        if "namespace" in row:
            rec.update(subject=row["namespace"], namespace=row["namespace"])
        rec.update(claim_id=claim_id(rec), revision_id=claim_revision_id(rec))
        if rec["claim_id"] in mapping.values() or row["id"] in mapping:
            raise ValueError("fixture ID collision")
        mapping[row["id"]] = rec["claim_id"]
        if "namespace" in row:
            foreign.add(rec["claim_id"])
        records.append(rec)
    # Independent of source labels; same relative order of shared sources across tiers.
    records.sort(key=lambda r: r["claim_id"])
    path.write_text(render_records(prefix, records), "utf-8")
    return mapping, foreign


def project(home, q, ranks):
    env = dict(os.environ)
    env.pop("NODE_OPTIONS", None)
    result = subprocess.run(
        ["node", str(HERE / "project.mjs"), str(home / "memory"), str(home / "scratch")],
        input=json.dumps(dict(question=q, ranks=ranks)),
        encoding="utf-8",
        capture_output=True,
        check=True,
        timeout=120,
        env=env,
    )
    obj = json.loads(result.stdout)
    return [json.loads(s) for s in obj["context"].splitlines()[1:]], obj["records"]


def boundary(keys, foreign, old, stage):
    if foreign.intersection(keys):
        raise BoundaryFailure(stage, "cross_subject")
    if old.intersection(keys):
        raise BoundaryFailure(stage, "revoked")


def build_index(home, visible, foreign, old, args):
    start = perf_counter()
    root = home / "memory"
    active = frozen.verified_active(root)
    boundary(active, foreign, old, "index")
    if set(active) != visible:
        raise ValueError("source verification mismatch")
    items, trace = project(home, "", None)
    boundary([r["id"] for r in items], foreign, old, "neutral_projection")
    admissible = {r["claim_id"] for r in trace if r.get("preRank") or r["reason"] == "no-relevance"}
    boundary(admissible, foreign, old, "eligibility")
    if admissible != visible:
        raise ValueError("adapter policy preflight mismatch")
    if not foreign <= {r["claim_id"] for r in trace if r["reason"] == "ineligible:namespace"}:
        raise ValueError("namespace controls were not exercised")
    rows = list(active.values())
    vectors = []
    for i in range(0, len(rows), 20):
        vectors.extend(frozen.embed([r["value"] for r in rows[i : i + 20]], args.embed_model))
    return active, list(zip([r["revision_id"] for r in rows], vectors)), perf_counter() - start


def measure(
    home, question, arm, ranks, active, foreign, old, lookup, args, query_time, search_time
):
    start = perf_counter()
    items, trace = project(home, question["text"], ranks if arm == "B" else None)
    boundary([r["id"] for r in items], foreign, old, "raw_projection")
    lines = {}
    for item in items:
        key = item["id"]
        rec = active.get(key)
        if (
            rec is None
            or item["value"] != rec["value"]
            or not frozen.mem.verified(home / "memory", rec)
        ):
            raise BoundaryFailure("projected_source", "invalid_citation")
        lines[key] = f"{rec['value']} [{key}] 本人原文 {rec['provenance']['source_ref']}"
    top5 = list(lines)[:5]
    projection = perf_counter() - start
    choices = []
    delivered = []

    def reader(_, messages):
        materials = json.loads(messages[-1]["content"])["材料"]
        by_line = {lines[k]: k for k in top5}
        delivered.extend(by_line[line] for line in materials)
        reply = frozen.post(
            args.reader_port,
            "/v1/chat/completions",
            dict(
                model=args.reader_model,
                messages=messages,
                temperature=0,
                seed=7,
                max_tokens=384,
                stream=False,
            ),
        )
        choice = reply["choices"][0]["message"]["content"]
        if not isinstance(choice, str) or not choice.strip():
            raise ValueError("empty model reply")
        choices.append(choice.strip())
        return choices[-1]

    with (
        patch.object(frozen.mem, "operate", return_value="\n".join(lines.values())),
        patch.object(frozen.jarvis, "model_reply", reader),
    ):
        answer = frozen.jarvis.ask_memory(home, question["text"])
    selected = next((k for k in delivered if answer == lines[k]), None)
    abstained = answer == frozen.ABSTAIN
    if not abstained and selected is None:
        raise BoundaryFailure("final_answer", "invalid_citation")
    status = (
        "selected"
        if selected
        else "program_no_material"
        if not choices
        else "model_none"
        if choices == ["NONE"]
        else "invalid_model_selection"
    )
    gold = lookup.get(question["gold"])
    target = next((r for r in trace if r["claim_id"] == gold), {})
    revision = active[gold]["revision_id"] if gold else None
    b = arm == "B"
    return dict(
        type="question",
        id=question["id"],
        group=group(question),
        arm=arm,
        gold=gold,
        semantic_top8=ranks if b else None,
        gold_semantic_rank=(ranks.index(revision) + 1 if revision in ranks else None)
        if b
        else None,
        pre_cap_rank=target.get("preRank"),
        drop_reason=target.get("reason"),
        stage=target.get("stage"),
        projected_rank=(list(lines).index(gold) + 1 if gold in lines else None),
        top5=top5,
        recall5=(gold in top5 if gold else None),
        reader_candidates=delivered,
        gold_delivered=(gold in delivered if gold else None),
        reader_calls=len(choices),
        raw_choice=choices,
        reader_status=status,
        selected=selected,
        correct=(selected == gold if gold else abstained),
        citation_valid=True if selected else None,
        negative_answer_with_real_source=(not gold and selected is not None),
        query_embedding_seconds=query_time if b else 0,
        vector_search_seconds=search_time if b else 0,
        projection_seconds=projection,
        end_to_end_seconds=perf_counter() - start + (query_time + search_time if b else 0),
    )


def summarize(pairs):
    arms = {}
    delta = {}
    for arm in ("A", "B"):
        rows = [p[arm] for p in pairs]
        arms[arm] = dict(
            recall5=sum(r["recall5"] for r in rows if r["gold"]),
            final_correct=sum(r["correct"] for r in rows if r["gold"]),
            negative_errors=sum(not r["correct"] for r in rows if not r["gold"]),
            negative_real_source_errors=sum(r["negative_answer_with_real_source"] for r in rows),
            by_group={
                g: dict(
                    total=sum(r["group"] == g for r in rows),
                    correct=sum(r["correct"] for r in rows if r["group"] == g),
                    model_none=sum(
                        r["reader_status"] == "model_none" for r in rows if r["group"] == g
                    ),
                    gold_in_prompt=sum(bool(r["gold_delivered"]) for r in rows if r["group"] == g),
                )
                for g in ("D", "E", "N")
            },
            median_seconds={
                key: median(r[key] for r in rows)
                for key in (
                    "query_embedding_seconds",
                    "vector_search_seconds",
                    "projection_seconds",
                    "end_to_end_seconds",
                )
            },
            refusal_and_selection=dict(Counter(r["reader_status"] for r in rows)),
            gold_delivered_model_none=[
                r["id"] for r in rows if r["gold_delivered"] and r["reader_status"] == "model_none"
            ],
        )
    for kind in ("D", "E", "N"):
        gain, loss = [], []
        for p in pairs:
            a, b = p["A"], p["B"]
            if a["group"] == kind and a["correct"] != b["correct"]:
                (gain if b["correct"] else loss).append(a["id"])
        delta[kind] = dict(gains=gain, losses=loss, net=len(gain) - len(loss))
    a, b = arms["A"], arms["B"]
    gates = dict(
        positive=b["final_correct"] - a["final_correct"] >= 3,
        recall=b["recall5"] >= a["recall5"],
        entity_net=delta["E"]["net"] >= -1,
        entity_losses=len(delta["E"]["losses"]) <= 2,
        negative=b["negative_errors"] <= min(2, a["negative_errors"]),
    )
    return dict(arms=arms, delta=delta, gates=gates)


def study(data, args, emit):
    check_base(data, args.blind)
    notes = data["notes"]
    adds = distractors(notes)
    n = sum(r["status"] == "active" for r in notes)
    stages = []
    safe = {"cross_subject": 0, "revoked": 0, "invalid_citation": 0}
    with tempfile.TemporaryDirectory(prefix="Jarvis scale ") as d:
        base = Path(d).resolve()

        # Copy a single immutable approved snapshot. Shared IDs/refs survive expansion.
        seed = base / "seed"
        frozen.jarvis.initialize(seed)
        core = frozen.seed(seed, notes)
        for tier, size in enumerate(SIZES, 1):
            home = base / f"T{tier}"
            shutil.copytree(seed, home)
            extra = adds[: size - SIZES[0]]
            counts = Counter(r["kind"] for r in extra)
            transforms = Counter(r["transform"] for r in extra if r["kind"] == "H")
            emit(
                dict(
                    type="corpus",
                    tier=tier,
                    active=n + len(extra),
                    counts={k: counts[k] for k in ("F", "G", "H")},
                    h_breakdown=dict(transforms),
                    hash=fingerprint(notes + extra),
                )
            )
            lookup, foreign = fixture_rows(home / "memory", extra, core)
            old = {core[r["id"]] for r in notes if r["status"] == "superseded"}
            try:
                active, index, build = build_index(
                    home, set(lookup.values()) - foreign - old, foreign, old, args
                )
                emit(
                    dict(
                        type="index",
                        tier=tier,
                        items=len(index),
                        seconds=build,
                        foreign_controls=len(foreign),
                        revoked_controls=len(old),
                    )
                )
                labels = {lookup[r["id"]]: r.get("transform", r["kind"]) for r in extra}
                emit(
                    dict(
                        type="sources",
                        tier=tier,
                        claim_ids=lookup,
                        revision_to_claim={r["revision_id"]: k for k, r in active.items()},
                    )
                )
                pairs = []
                for i, q in enumerate(data["questions"]):
                    t = perf_counter()
                    v = frozen.embed([q["text"]], args.embed_model)[0]
                    query_time = perf_counter() - t
                    t = perf_counter()
                    order = sorted(
                        index, key=lambda p: (-sum(x * y for x, y in zip(v, p[1])), p[0])
                    )
                    ranks = [r for r, _ in order[:8]]
                    search_time = perf_counter() - t
                    pair = {}
                    for arm in ("A", "B") if i % 2 == 0 else ("B", "A"):
                        row = measure(
                            home,
                            q,
                            arm,
                            ranks,
                            active,
                            foreign,
                            old,
                            lookup,
                            args,
                            query_time,
                            search_time,
                        )
                        row["reader_source_types"] = [
                            labels.get(k, "base") for k in row["reader_candidates"]
                        ]
                        emit(dict(row, tier=tier))
                        pair[arm] = row
                    pairs.append(pair)
            except BoundaryFailure as err:
                safe[err.kind] += 1
                emit(
                    dict(
                        type="boundary_failure",
                        tier=tier,
                        stage=err.stage,
                        violations=safe,
                        passed=False,
                    )
                )
                return False
            metrics = summarize(pairs)
            emit(dict(type="metrics", tier=tier, **metrics))
            stages.append(all(metrics["gates"].values()))
    passed = all(stages[1:]) if args.blind else None
    emit(dict(type="complete", pairs=3 * len(data["questions"]), violations=safe, passed=passed))
    return passed


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--manifest", required=True, type=Path)
    cli.add_argument("--out", required=True, type=Path)
    mode = cli.add_mutually_exclusive_group(required=True)
    mode.add_argument("--public-smoke", action="store_true")
    mode.add_argument("--sealed-original", type=Path)
    cli.add_argument("--sealed-hash")
    cli.add_argument("--gguf", required=True, type=Path)
    cli.add_argument("--embed-model", required=True)
    cli.add_argument("--reader-model", required=True)
    cli.add_argument("--reader-port", type=int, required=True)
    args = cli.parse_args()
    args.blind = bool(args.sealed_original)
    if args.blind and any(
        p.resolve().is_relative_to(HERE.parents[1])
        for p in (args.manifest, args.out, args.sealed_original)
    ):
        cli.error("private files must be outside checkout")
    if args.blind and (
        not args.sealed_hash
        or not args.sealed_hash.startswith("002a5180")
        or not args.sealed_hash.endswith("397d")
        or args.sealed_hash != frozen.sha(args.sealed_original)
    ):
        cli.error("original sealed hash mismatch")
    if (
        frozen.sha(args.gguf) != frozen.MODEL_HASH
        or not 0 < args.reader_port < 65536
        or args.reader_port == 8082
    ):
        cli.error("check weights and local reader port")
    for port, model in ((8082, args.embed_model), (args.reader_port, args.reader_model)):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
        try:
            conn.request("GET", "/v1/models")
            res = conn.getresponse()
            if res.status != 200 or model not in [
                r["id"] for r in json.loads(res.read(1 << 20))["data"]
            ]:
                cli.error("local model id mismatch")
        finally:
            conn.close()
    data = json.loads(args.manifest.read_text("utf-8-sig"))
    check_base(data, args.blind)
    with args.out.open("x", encoding="utf-8") as out:

        def emit(row):
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()

        emit(
            dict(
                type="protocol",
                blind=args.blind,
                sealed=args.sealed_hash,
                mapping=frozen.sha(args.manifest),
                embedding_sha=frozen.MODEL_HASH,
                embedding_model=args.embed_model,
                reader_model=args.reader_model,
                reader_port=args.reader_port,
            )
        )
        if study(data, args, emit) is False:
            sys.exit(1)


if __name__ == "__main__":
    main()
