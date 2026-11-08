#!/usr/bin/env python3
"""Replay LongMemEval main-arm projection with claim diagnostics on.

Re-runs worker.py --arm main per question against the run's saved model-call
cache (no new model spend when the cache is complete) with
STELLAMEM_PROJECT_DIAGNOSTICS_DIR set, so adapters/openclaw/index.js writes
one diagnostics sidecar per question. worker.py is frozen and untouched;
the only difference from the original run is the diagnostics env var, which
does not change the projected string (verified by
tests/adapter-diagnostics.test.mjs: byte-identical output with it on/off).

The per-question cache is COPIED to the replay work dir first, so a cache
miss can never poison the original run's cache (a miss writes an error entry
into the copy only, and is reported). A non-zero physical_calls count on any
question means the cache was incomplete for it.

Usage:
    python replay_project.py --repo <repo> --dataset <oracle.json> \\
        --questions <shard0_questions.json> [<shard1_questions.json> ...] \\
        --cache-root <work/full> --work <replay work dir> \\
        --endpoint <proxy url> [--results <results.json>] [--limit N]
        [--only qid,qid...] [--force]
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_id(readable: str) -> str:
    """results.json hashes session/turn ids as sha256(id)[:16] (see merge.py);
    the worker's raw output keeps them readable. Hash before comparing."""
    return hashlib.sha256(readable.encode()).hexdigest()[:16]


def prompt_sha(question: str) -> str:
    return hashlib.sha256(question.encode()).hexdigest()[:16]


def clean_case(case):
    return {
        "question": case["question"],
        "question_date": case["question_date"],
        "sessions": [
            {
                "id": sid,
                "date": date,
                "turns": [{"role": t["role"], "content": t["content"]} for t in turns],
            }
            for sid, date, turns in zip(
                case["haystack_session_ids"], case["haystack_dates"], case["haystack_sessions"]
            )
        ],
    }


def main():
    p = argparse.ArgumentParser(description="Replay LongMemEval projection with diagnostics on.")
    p.add_argument("--repo", type=Path, required=True)
    p.add_argument("--dataset", type=Path, required=True, help="longmemeval_oracle.json")
    p.add_argument(
        "--questions",
        nargs="+",
        type=Path,
        required=True,
        help="shard questions files (lists of question ids, in shard order)",
    )
    p.add_argument(
        "--cache-root",
        type=Path,
        required=True,
        help="run work dir holding shard0..3, each with cache/<digest(qid)>",
    )
    p.add_argument("--work", type=Path, required=True, help="replay work dir (created)")
    p.add_argument("--endpoint", required=True, help="model endpoint (only used on a cache miss)")
    p.add_argument(
        "--results",
        type=Path,
        default=None,
        help="this round's results.json, for the retrieved/cited fidelity check",
    )
    p.add_argument("--limit", type=int, default=0, help="run at most N questions (smoke)")
    p.add_argument("--only", default="", help="comma-separated question ids to run")
    p.add_argument("--force", action="store_true", help="re-run even when a sidecar exists")
    args = p.parse_args()

    oracle = {q["question_id"]: q for q in json.loads(args.dataset.read_text())}
    qid_to_shard = {}
    qids = []
    for shard, qfile in enumerate(args.questions):
        for qid in json.loads(qfile.read_text()):
            qid_to_shard[qid] = shard
            qids.append(qid)
    only = {q for q in args.only.split(",") if q} if args.only else None
    if only:
        qids = [q for q in qids if q in only]
    if args.limit:
        qids = qids[: args.limit]

    diag_dir = args.work / "diagnostics"
    diag_dir.mkdir(parents=True, exist_ok=True)
    cache_work = args.work / "cache"
    cache_work.mkdir(parents=True, exist_ok=True)
    manifest_path = args.work / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    prev = None
    if args.results:
        prev = {
            c["question_id"]: c["arms"]["main"]
            for c in json.loads(args.results.read_text())["cases"]
        }

    worker = args.repo / "benchmark" / "longmemeval" / "worker.py"
    env = dict(os.environ)
    env["STELLAMEM_PROJECT_DIAGNOSTICS_DIR"] = str(diag_dir)

    ran, skipped, errors, physical_total, fidelity_bad = 0, 0, [], 0, []
    results_out = {}
    for n, qid in enumerate(qids, 1):
        case = oracle[qid]
        sha = prompt_sha(case["question"])
        if not args.force and (diag_dir / (sha + ".json")).exists():
            skipped += 1
            continue
        shard = qid_to_shard[qid]
        qdigest = digest(qid.encode())
        src_cache = args.cache_root / f"shard{shard}" / "cache" / qdigest
        dst_cache = cache_work / qdigest
        if src_cache.is_dir():
            shutil.copytree(src_cache, dst_cache, dirs_exist_ok=True)
        else:
            dst_cache.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            [
                sys.executable,
                str(worker),
                "--repo",
                str(args.repo),
                "--arm",
                "main",
                "--endpoint",
                args.endpoint,
                "--cache",
                str(dst_cache),
            ],
            input=json.dumps(clean_case(case)),
            text=True,
            capture_output=True,
            timeout=1800,
            env=env,
        )
        if proc.returncode:
            errors.append(qid)
            print(
                f"[{n}/{len(qids)}] {qid} WORKER FAILED rc={proc.returncode}: {proc.stderr[-300:]}",
                flush=True,
            )
            continue
        raw = json.loads(proc.stdout)
        physical_total += raw.get("physical_calls", 0)
        if raw.get("physical_calls"):
            print(
                f"[{n}/{len(qids)}] {qid} CACHE MISS physical_calls={raw['physical_calls']}",
                flush=True,
            )
        results_out[qid] = {
            "error": raw.get("error"),
            "physical_calls": raw.get("physical_calls"),
            "extracted": raw.get("extracted"),
            "retrieved_sessions": raw.get("retrieved_sessions"),
            "cited_sessions": raw.get("cited_sessions"),
        }
        manifest[sha] = {
            "question_id": qid,
            "question": case["question"],
            "question_date": case["question_date"],
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        if prev is not None:
            pm = prev[qid]
            if sorted(hash_id(s) for s in (raw.get("retrieved_sessions") or [])) != sorted(
                pm.get("retrieved_sessions") or []
            ) or sorted(hash_id(s) for s in (raw.get("cited_sessions") or [])) != sorted(
                pm.get("cited_sessions") or []
            ):
                fidelity_bad.append(qid)
        ran += 1
        print(
            f"[{n}/{len(qids)}] {qid} ok error={raw.get('error')} "
            f"physical={raw.get('physical_calls')} "
            f"retrieved={len(raw.get('retrieved_sessions') or [])}",
            flush=True,
        )

    (args.work / "replay_results.json").write_text(
        json.dumps(results_out, ensure_ascii=False, indent=2)
    )
    print(
        f"ran={ran} skipped={skipped} errors={len(errors)} "
        f"physical_calls_total={physical_total} fidelity_mismatch={len(fidelity_bad)}"
    )
    if errors:
        print("errors:", errors)
    if fidelity_bad:
        print("fidelity mismatch (retrieved/cited differ from results.json):", fidelity_bad)
    return 1 if (errors or fidelity_bad) else 0


if __name__ == "__main__":
    raise SystemExit(main())
