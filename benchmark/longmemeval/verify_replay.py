#!/usr/bin/env python3
"""Offline zero-cost verification for the LongMemEval round-4 (2026-11-08) record.

Recomputes the headline numbers from a saved results.json (and, when given,
the per-shard usage logs) and compares them with the stored summary. No model
calls, no network, standard library only.

What it checks:
  1. File identity: protocol / repo_sha / dataset_sha256 / 500 unique questions.
  2. Headline recompute per arm: judge_accuracy (count of judge_label true),
     exact_match, correct_abstention (abstention questions only), recall@1/@5
     (mean over non-null), citation_valid rate, coverage, logical/physical
     calls, cache hits, error counters -- each compared with the stored
     summary to 1e-9.
  3. by_category recompute (same metrics per category, incl. judge_accuracy).
  4. Cost recompute from shard usage.jsonl with merge.py pricing
     (flash off-peak: miss $0.15/M, hit $0.003/M, output $0.6/M).
  5. Budget-breach (25) and extract-failed (32) question lists recompute.
  6. Optional: score-integrity audit (PR #100, score_integrity.py) against a
     tracking CSV -- expects legacy 177, corrected 174 and the three known
     false positives {gpt4_a1b77f9c, 6456829e_abs, gpt4_372c3eed_abs}.
     Skipped (not failed) when the auditor module is absent.
  7. Optional: cache-manifest check against results-20261108-hashes.json.
  8. Raw worker outputs (derived from --usage-dir when shard raw/ dirs exist):
     all 2000 per-question-per-arm raw files present and agreeing with
     results.json on error / extracted / retrieved / cited (session ids
     hashed as in merge.py) / citation_valid / logical / physical /
     cache_hits; every stored judge_label re-derived from its stored
     judge_response with the runner's rule ("yes" in response.lower());
     candidate == main on every verdict field (answer, error, extracted,
     retrieved/cited sets, exact_match, abstention, judge label +
     hypothesis) -- only operational metadata may differ (cache hits,
     physical calls, timing/RSS, usage seq, extracted_turns order).
  9. Claim replay (derived from --usage-dir when the sibling
     claim-replay-full/ dir exists): replay_results.json entries agree
     with the main arm on error / extracted / retrieved / cited with
     zero physical calls; every question missing from replay_results
     still has its diagnostics sidecar (the 5 skipped by the logged run).
 10. Run freshness (optional, needs an explicit run manifest): every
     artifact in the manifest must have been generated strictly after
     the run started. A run_id can be templated into an old artifact,
     so an artifact generated at or before run_started_at -- or carrying
     a different run_id -- fails with the reason code STALE_RUN_ID.
     An artifact whose generated_at (or the manifest's run_started_at)
     is missing or unparseable is NOT_EXECUTED -- the third verdict,
     cf. negative_samples.mjs: it counts neither as a pass nor as a
     failure, and is never silently folded into a pass.

Exit codes: 0 = all performed checks pass; 1 = a check failed;
2 = usage/input error, or NOT_EXECUTED checks with no failures.

Usage:
    python verify_replay.py --results <results.json> [--usage-dir <work/full>]
        [--tracking-csv <tracking_table_20261108.csv>]
        [--hashes <results-20261108-hashes.json>]
        [--cache-root <work/full>] [--diagnostics <work/claim-replay-full/diagnostics>]
        [--run-manifest <run-manifest.json>]
"""

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ARMS = ("main", "candidate", "none", "direct")
EXPECTED = {
    "protocol": "v2-full-route-B",
    "repo_sha": "9034cdec0bf709ee5d66c281b9af48bf17c515f7",
    "dataset_sha256": "821a2034d219ab45846873dd14c14f12cfe7776e73527a483f9dac095d38620c",
    "n_questions": 500,
    "n_manifest_verified": 140,
}
MISS = 0.15 / 1_000_000
HIT = 0.003 / 1_000_000
OUT = 0.6 / 1_000_000
KNOWN_FALSE_POSITIVES = {"gpt4_a1b77f9c", "6456829e_abs", "gpt4_372c3eed_abs"}


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def arm_metrics(rows, arm):
    rs = [r["arms"][arm] for r in rows]
    return {
        "n": len(rs),
        "errors": dict(Counter(r["error"] for r in rs if r.get("error"))),
        "exact_match": mean([r.get("exact_match") for r in rs]),
        "correct_abstention": mean([r.get("correct_abstention") for r in rs]),
        "recall_at_1": mean([r.get("retrieval_recall_at_1") for r in rs]),
        "recall_at_5": mean([r.get("retrieval_recall_at_5") for r in rs]),
        "citation_reference_valid_rate": mean([r.get("citation_valid") for r in rs]),
        "extracted_evidence_turn_coverage": mean(
            [r.get("extracted_evidence_turn_coverage") for r in rs]
        ),
        "logical_model_calls": sum(r.get("logical_calls") or 0 for r in rs),
        "physical_model_calls": sum(r.get("physical_calls") or 0 for r in rs),
        "cache_hits": sum(r.get("cache_hits") or 0 for r in rs),
        "judge_accuracy": (
            sum(1 for r in rs if (r.get("judge") or {}).get("judge_label")) / len(rs)
            if rs
            else None
        ),
    }


def close(a, b, tol=1e-9):
    if a is None or b is None:
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= tol
    return a == b


def cost_of(call):
    miss = call.get("prompt_cache_miss_tokens") or 0
    hit = call.get("prompt_cache_hit_tokens") or 0
    out = call.get("completion_tokens") or 0
    if miss == 0 and hit == 0 and call.get("prompt_tokens"):
        miss = call["prompt_tokens"]
    return miss * MISS + hit * HIT + out * OUT


def hash_id(readable):
    """results.json hashes session/turn ids as sha256(id)[:16]; raw output keeps them readable."""
    return hashlib.sha256(readable.encode()).hexdigest()[:16]


def check_raw_consistency(cases, raw_root):
    """Cross-check every raw worker output against results.json.

    Returns (problems, notes). `notes` carries counts that are informative
    but not failures: operational-only candidate/main differences and
    judge-response case-only variation.
    """
    by_qid = {c.get("question_id"): c for c in cases}
    found = {}
    for shard in range(4):
        raw_dir = Path(raw_root) / f"shard{shard}" / "raw"
        if not raw_dir.is_dir():
            continue
        for path in sorted(raw_dir.glob("*_*.json")):
            stem = path.name[:-5]
            if "_" not in stem:
                continue
            qid, arm = stem.rsplit("_", 1)
            if arm in ARMS:
                found[(qid, arm)] = path
    problems = []
    notes = {}
    missing = [(qid, arm) for qid in by_qid for arm in ARMS if (qid, arm) not in found]
    if missing:
        problems.append(f"raw files missing for {len(missing)} question-arms, e.g. {missing[:3]}")
    for (qid, arm), path in sorted(found.items()):
        case = by_qid.get(qid)
        if case is None:
            problems.append(f"raw {path.name}: question {qid} not in results.json")
            continue
        try:
            raw = json.loads(path.read_text())
        except ValueError as exc:
            problems.append(f"raw {path.name}: invalid JSON: {exc}")
            continue
        rec = (case.get("arms") or {}).get(arm) or {}
        for key in (
            "error",
            "extracted",
            "citation_valid",
            "logical_calls",
            "physical_calls",
            "cache_hits",
        ):
            if raw.get(key) != rec.get(key):
                problems.append(
                    f"raw {qid}/{arm}.{key}: {raw.get(key)!r} != results {rec.get(key)!r}"
                )
        for key in ("retrieved_sessions", "cited_sessions"):
            if sorted(hash_id(s) for s in (raw.get(key) or [])) != sorted(rec.get(key) or []):
                problems.append(f"raw {qid}/{arm}.{key} mismatch")
        judge = rec.get("judge") or {}
        resp = judge.get("judge_response") or ""
        # Runner rule (full.py judge()): label = "yes" in content.lower().
        if ("yes" in resp.lower()) != bool(judge.get("judge_label")):
            problems.append(
                f"judge {qid}/{arm}: label {judge.get('judge_label')!r} "
                f"!= derived from response {resp!r}"
            )
    # Candidate must equal main on every verdict field: the two arms ran the
    # same repo, so candidate reuses main's cache and must answer identically.
    verdict_keys = (
        "answer_sha256",
        "answer_text",
        "error",
        "extracted",
        "citation_valid",
        "exact_match",
        "abstain",
    )
    resp_case_only = 0
    turns_order_only = 0
    for qid, case in sorted(by_qid.items()):
        arms = case.get("arms") or {}
        main, cand = arms.get("main") or {}, arms.get("candidate") or {}
        for key in verdict_keys:
            if main.get(key) != cand.get(key):
                problems.append(
                    f"candidate!=main {qid}.{key}: {main.get(key)!r} != {cand.get(key)!r}"
                )
        for key in ("retrieved_sessions", "cited_sessions"):
            if sorted(main.get(key) or []) != sorted(cand.get(key) or []):
                problems.append(f"candidate!=main {qid}.{key} set mismatch")
        jm, jc = main.get("judge") or {}, cand.get("judge") or {}
        if jm.get("judge_label") != jc.get("judge_label"):
            problems.append(f"candidate!=main {qid}.judge_label")
        if jm.get("hypothesis") != jc.get("hypothesis"):
            problems.append(f"candidate!=main {qid}.judge hypothesis mismatch")
        if jm.get("judge_response") != jc.get("judge_response"):
            if (jm.get("judge_response") or "").lower() == (jc.get("judge_response") or "").lower():
                resp_case_only += 1
            else:
                problems.append(
                    f"candidate!=main {qid}.judge_response: {jm.get('judge_response')!r}"
                )
        if sorted(main.get("extracted_turns") or []) != sorted(cand.get("extracted_turns") or []):
            problems.append(f"candidate!=main {qid}.extracted_turns set mismatch")
        elif (main.get("extracted_turns") or []) != (cand.get("extracted_turns") or []):
            turns_order_only += 1
    notes["raw_files"] = len(found)
    notes["judge_response_case_only"] = resp_case_only
    notes["extracted_turns_order_only"] = turns_order_only
    notes["candidate_physical_zero"] = sum(
        1
        for c in cases
        if ((c.get("arms") or {}).get("candidate") or {}).get("physical_calls") == 0
    )
    notes["none_physical"] = sum(
        (c.get("arms") or {}).get("none", {}).get("physical_calls") or 0 for c in cases
    )
    notes["direct_physical"] = sum(
        (c.get("arms") or {}).get("direct", {}).get("physical_calls") or 0 for c in cases
    )
    return problems, notes


def check_replay_consistency(cases, replay_root):
    """Cross-check the claim replay against the main arm.

    Returns (problems, notes). Questions absent from replay_results.json
    (skipped because their sidecar already existed) must still have a
    sidecar; every replayed entry must match the main arm with zero
    physical calls.
    """
    by_qid = {c.get("question_id"): c for c in cases}
    problems = []
    notes = {}
    try:
        manifest = json.loads((Path(replay_root) / "manifest.json").read_text())
        replayed = json.loads((Path(replay_root) / "replay_results.json").read_text())
    except (OSError, ValueError) as exc:
        return [f"replay files unreadable: {exc}"], notes
    manifest_qids = {entry.get("question_id") for entry in manifest.values()}
    if manifest_qids != set(by_qid):
        problems.append(
            f"replay manifest covers {len(manifest_qids)} questions, results has {len(by_qid)}"
        )
    diag_dir = Path(replay_root) / "diagnostics"
    for qid, entry in sorted(replayed.items()):
        case = by_qid.get(qid)
        if case is None:
            problems.append(f"replay entry {qid} not in results.json")
            continue
        main = (case.get("arms") or {}).get("main") or {}
        if entry.get("physical_calls") != 0:
            problems.append(f"replay {qid}: physical_calls {entry.get('physical_calls')} != 0")
        if entry.get("error") != main.get("error"):
            problems.append(f"replay {qid}.error: {entry.get('error')!r} != {main.get('error')!r}")
        if entry.get("extracted") != main.get("extracted"):
            problems.append(f"replay {qid}.extracted mismatch")
        for key in ("retrieved_sessions", "cited_sessions"):
            if sorted(hash_id(s) for s in (entry.get(key) or [])) != sorted(main.get(key) or []):
                problems.append(f"replay {qid}.{key} mismatch")
    skipped = sorted(manifest_qids - set(replayed))
    notes["replayed"] = len(replayed)
    notes["skipped"] = skipped
    # A sidecar exists exactly when the main arm got past extraction: every
    # error-free question and the single ModelError one have one; the 31
    # ChunkFailure questions have none (extraction failed before projection).
    for qid in sorted(manifest_qids):
        sha = next((k for k, v in manifest.items() if v.get("question_id") == qid), None)
        if sha is None:
            problems.append(f"replay manifest has no entry for {qid}")
            continue
        main = (by_qid.get(qid, {}).get("arms") or {}).get("main") or {}
        want_sidecar = main.get("error") != "ChunkFailure"
        has_sidecar = (diag_dir / (sha + ".json")).is_file()
        if want_sidecar and not has_sidecar:
            problems.append(f"replay {qid}: no diagnostics sidecar")
        if not want_sidecar and has_sidecar:
            problems.append(f"replay {qid}: unexpected diagnostics sidecar for ChunkFailure")
    return problems, notes


def check_cost(usage_dir, stored):
    usage_all = []
    for i in range(4):
        path = Path(usage_dir) / f"shard{i}" / "usage.jsonl"
        for line in path.read_text().splitlines():
            if line.strip():
                usage_all.append(json.loads(line))
    by_kind = {}
    for entry in usage_all:
        kind = entry.get("kind") or "unknown"
        d = by_kind.setdefault(
            kind, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}
        )
        d["calls"] += 1
        d["prompt_tokens"] += entry.get("prompt_tokens") or 0
        d["completion_tokens"] += entry.get("completion_tokens") or 0
        d["cost_usd"] += cost_of(entry)
    total_usd = sum(d["cost_usd"] for d in by_kind.values())
    total_calls = sum(d["calls"] for d in by_kind.values())
    for d in by_kind.values():
        d["cost_usd"] = round(d["cost_usd"], 4)
    recomputed = {
        "by_kind": by_kind,
        "total_calls": total_calls,
        "total_usd": round(total_usd, 4),
        "per_question_usd": round(total_usd / 500, 5),
    }
    problems = []
    if recomputed["total_calls"] != stored["total_calls"]:
        problems.append(
            f"total_calls {recomputed['total_calls']} != stored {stored['total_calls']}"
        )
    if recomputed["total_usd"] != stored["total_usd"]:
        problems.append(f"total_usd {recomputed['total_usd']} != stored {stored['total_usd']}")
    if recomputed["per_question_usd"] != stored["per_question_usd"]:
        problems.append("per_question_usd mismatch")
    for kind, d in stored["by_kind"].items():
        got = by_kind.get(kind)
        if got is None:
            problems.append(f"missing kind {kind}")
            continue
        for k in ("calls", "prompt_tokens", "completion_tokens", "cost_usd"):
            if got[k] != d[k]:
                problems.append(f"{kind}.{k}: {got[k]} != stored {d[k]}")
    return problems


def parse_timestamp(value):
    """Parse an ISO-8601 timestamp; naive values are read as UTC.

    Returns an aware datetime, or None when the value is not a parseable
    timestamp string. The trailing "Z" is rewritten because
    datetime.fromisoformat only accepts it from Python 3.11.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text[-1] in ("Z", "z"):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp


def check_run_freshness(manifest):
    """Check artifact freshness against an explicit run manifest (Hermes H-1).

    The manifest is a JSON object of the shape
        {"run_id": ..., "run_started_at": <ISO-8601>,
         "artifacts": {name: {"generated_at": <ISO-8601>, "run_id": ...}}}
    An artifact counts as this run's output only when it was generated
    strictly after the run started: a run_id can be templated into an old
    artifact, so a matching id never proves freshness on its own. An
    artifact generated at or before run_started_at, or carrying a
    different run_id than the manifest's, is a problem and is reported
    with the reason code STALE_RUN_ID. Where the manifest carries no
    usable timestamp the check cannot be executed for that artifact: it
    is returned as NOT_EXECUTED (the third verdict, cf.
    negative_samples.mjs), never as a pass.

    Returns (problems, not_executed, notes).
    """
    problems, not_executed = [], []
    notes = {"fresh": 0, "artifacts": 0, "run_id": None}
    if not isinstance(manifest, dict):
        return ["run manifest is not a JSON object"], not_executed, notes
    run_id = manifest.get("run_id")
    notes["run_id"] = run_id
    started_raw = manifest.get("run_started_at")
    started = parse_timestamp(started_raw)
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        not_executed.append("run manifest lists no artifacts; freshness not executed")
        return problems, not_executed, notes
    notes["artifacts"] = len(artifacts)
    for name in sorted(artifacts):
        entry = artifacts[name]
        if not isinstance(entry, dict):
            not_executed.append(f"{name}: manifest entry is not an object")
            continue
        if started is None:
            not_executed.append(
                f"{name}: run manifest has no usable run_started_at "
                f"({started_raw!r}); freshness not executed"
            )
            continue
        generated = parse_timestamp(entry.get("generated_at"))
        if generated is None:
            not_executed.append(
                f"{name}: no usable generated_at in run manifest "
                f"({entry.get('generated_at')!r}); freshness not executed"
            )
            continue
        entry_run = entry.get("run_id")
        if entry_run is not None and run_id is not None and entry_run != run_id:
            problems.append(
                f"{name}: STALE_RUN_ID artifact run_id {entry_run!r} != run run_id {run_id!r}"
            )
            continue
        if generated <= started:
            problems.append(
                f"{name}: STALE_RUN_ID generated_at {entry.get('generated_at')!r} "
                f"is not after run_started_at {started_raw!r}"
            )
            continue
        notes["fresh"] += 1
    return problems, not_executed, notes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--usage-dir", type=Path, default=None)
    parser.add_argument("--tracking-csv", type=Path, default=None)
    parser.add_argument("--hashes", type=Path, default=None)
    parser.add_argument("--cache-root", type=Path, default=None)
    parser.add_argument("--diagnostics", type=Path, default=None)
    parser.add_argument("--run-manifest", type=Path, default=None)
    args = parser.parse_args()

    failures, passes, skips, not_executed = [], [], [], []

    try:
        data = json.loads(args.results.read_text())
    except (OSError, ValueError) as exc:
        print(f"FAIL: cannot read results: {exc}")
        return 2
    cases = data.get("cases") or []

    # 1. Identity.
    ident_problems = []
    for key, want in EXPECTED.items():
        if data.get(key) != want:
            ident_problems.append(f"{key}: {data.get(key)!r} != {want!r}")
    qids = [c.get("question_id") for c in cases]
    if len(cases) != 500 or len(set(qids)) != 500:
        ident_problems.append(f"cases={len(cases)} unique_qids={len(set(qids))}")
    if ident_problems:
        failures.append("identity: " + "; ".join(ident_problems))
    else:
        passes.append("identity: protocol/repo_sha/dataset_sha256/500 unique questions")

    # 2. Headline recompute per arm.
    stored_summary = data.get("summary") or {}
    for arm in ARMS:
        got = arm_metrics(cases, arm)
        want = stored_summary.get(arm) or {}
        bad = []
        for key, gv in got.items():
            if key in ("mean_elapsed_seconds", "peak_worker_rss_kib"):
                continue  # timing/RSS are not reproduced by recompute; stored as-is
            if not close(gv, want.get(key)):
                bad.append(f"{key}: recomputed {gv!r} != stored {want.get(key)!r}")
        # deltas (merge.py second pass)
        for key, gv in (
            (
                "judge_delta_vs_none",
                got["judge_accuracy"] - arm_metrics(cases, "none")["judge_accuracy"],
            ),
            (
                "exact_delta_vs_none",
                (got["exact_match"] or 0) - (arm_metrics(cases, "none")["exact_match"] or 0),
            ),
        ):
            if not close(gv, want.get(key)):
                bad.append(f"{key}: recomputed {gv!r} != stored {want.get(key)!r}")
        if bad:
            failures.append(f"summary[{arm}]: " + "; ".join(bad))
        else:
            judge_n = sum(
                1 for c in cases if (c["arms"][arm].get("judge") or {}).get("judge_label")
            )
            passes.append(
                f"summary[{arm}]: judge {judge_n}/500 = {got['judge_accuracy']:.3f}, "
                f"recall@5 {got['recall_at_5']:.3f}, abstention {got['correct_abstention']}"
            )

    # 3. by_category recompute.
    stored_by_cat = data.get("by_category") or {}
    cat_bad = []
    for cat in sorted({c.get("category") for c in cases}):
        rows = [c for c in cases if c.get("category") == cat]
        for arm in ARMS:
            got = arm_metrics(rows, arm)
            want = (stored_by_cat.get(cat) or {}).get(arm) or {}
            for key, gv in got.items():
                if key in ("mean_elapsed_seconds", "peak_worker_rss_kib"):
                    continue
                if not close(gv, want.get(key)):
                    cat_bad.append(f"{cat}/{arm}.{key}: {gv!r} != {want.get(key)!r}")
            if not close(got["judge_accuracy"], want.get("judge_accuracy")):
                cat_bad.append(f"{cat}/{arm}.judge_accuracy mismatch")
    if cat_bad:
        failures.append("by_category: " + "; ".join(cat_bad[:10]))
    else:
        passes.append(f"by_category: {len(stored_by_cat)} categories recompute clean")

    # 4. Cost.
    if args.usage_dir is not None:
        try:
            problems = check_cost(args.usage_dir, data.get("cost") or {})
        except (OSError, ValueError, KeyError) as exc:
            failures.append(f"cost: unreadable usage logs: {exc}")
        else:
            if problems:
                failures.append("cost: " + "; ".join(problems))
            else:
                passes.append(
                    f"cost: {data['cost']['total_calls']} calls, "
                    f"${data['cost']['total_usd']} total recomputed from usage.jsonl"
                )
    else:
        skips.append("cost: --usage-dir not given")

    # 5. Breach / extract-failed lists.
    breach = [
        r["question_id"]
        for r in cases
        if r["arms"]["main"].get("error") == "ChunkFailure"
        and r["arms"]["main"].get("extraction_failure_code") == "RuntimeError"
        and (r["arms"]["main"].get("logical_calls") or 0) > 32
        and r["arms"]["main"].get("extracted") == 0
    ]
    failed = [r["question_id"] for r in cases if r["arms"]["main"].get("error") is not None]
    # Stored lists are in shard (row) order; cases are sorted by case hash,
    # so compare as sets, not in order.
    if sorted(breach) != sorted(data.get("budget_breach_question_ids") or []):
        failures.append("budget_breach list mismatch")
    elif sorted(failed) != sorted(data.get("extract_failed_main_question_ids") or []):
        failures.append("extract_failed list mismatch")
    else:
        passes.append(f"lists: budget breach {len(breach)}, extract-failed {len(failed)}")

    # 6. Score-integrity audit (optional, needs PR #100 module + tracking CSV).
    if args.tracking_csv is not None:
        here = Path(__file__).resolve().parent
        sys.path.insert(0, str(here))
        try:
            import score_integrity
        except ImportError:
            skips.append("score-integrity audit: score_integrity.py not present beside this script")
        else:
            try:
                report = score_integrity.audit_file(args.tracking_csv)
            except (OSError, ValueError) as exc:
                failures.append(f"score-integrity audit: invalid input: {exc}")
            else:
                s = report["summary"]["all"]
                fps = {r["question_id"] for r in report["rows"] if r.get("false_positive_detected")}
                if (
                    s["legacy_judge_correct"] == 177
                    and s["corrected_correct"] == 174
                    and fps == KNOWN_FALSE_POSITIVES
                ):
                    passes.append(
                        "score-integrity audit: legacy 177 -> corrected 174, "
                        "3 known false positives, input "
                        f"{report['input_sha256'][:12]}..."
                    )
                else:
                    failures.append(
                        "score-integrity audit: "
                        f"legacy {s['legacy_judge_correct']} corrected "
                        f"{s['corrected_correct']} fps {sorted(fps)}"
                    )
    else:
        skips.append("score-integrity audit: --tracking-csv not given")

    # 7. Cache/diagnostics manifest check (optional).
    if args.hashes is not None and args.cache_root is not None:
        try:
            manifest = json.loads(args.hashes.read_text())
            union = {}
            for i in range(4):
                root = Path(args.cache_root) / f"shard{i}" / "cache"
                for p in sorted(root.rglob("*")):
                    if p.is_file():
                        union[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
            blob = ("\n".join(f"{union[k]}  {k}" for k in sorted(union)) + "\n").encode()
            got_hash = hashlib.sha256(blob).hexdigest()
            want_hash = manifest["directories"]["work/full/shard{0..3}/cache (union)"][
                "sha256_of_sorted_file_hash_list"
            ]
            if got_hash == want_hash and len(union) == 8859:
                passes.append(f"cache union: 8859 files, hash {got_hash[:12]}... matches manifest")
            else:
                failures.append(
                    f"cache union: {len(union)} files hash {got_hash[:12]}... "
                    f"!= manifest {want_hash[:12]}..."
                )
        except (OSError, ValueError, KeyError) as exc:
            failures.append(f"cache manifest check: {exc}")
        if args.diagnostics is not None:
            try:
                n_diag = sum(1 for _ in Path(args.diagnostics).iterdir())
                if n_diag == 469:
                    passes.append("diagnostics: 469 sidecars present")
                else:
                    failures.append(f"diagnostics: {n_diag} files, expected 469")
            except OSError as exc:
                failures.append(f"diagnostics: {exc}")
    elif args.hashes is not None:
        skips.append("cache manifest check: --cache-root not given")
    else:
        skips.append("cache manifest check: --hashes not given")

    # 8. Raw worker outputs (derived from --usage-dir).
    raw_root = Path(args.usage_dir) if args.usage_dir is not None else None
    if raw_root is not None and any((raw_root / f"shard{i}" / "raw").is_dir() for i in range(4)):
        try:
            raw_problems, raw_notes = check_raw_consistency(cases, raw_root)
        except (OSError, ValueError, KeyError) as exc:
            failures.append(f"raw: unreadable raw outputs: {exc}")
        else:
            if raw_problems:
                failures.append("raw: " + "; ".join(raw_problems[:10]))
            else:
                passes.append(
                    f"raw: {raw_notes['raw_files']} raw files agree with results.json; "
                    f"candidate==main on all verdict fields "
                    f"(physical 0 on {raw_notes['candidate_physical_zero']}/500, "
                    f"none physical {raw_notes['none_physical']}, "
                    f"direct physical {raw_notes['direct_physical']}); "
                    f"judge labels re-derived from stored responses; "
                    f"{raw_notes['judge_response_case_only']} judge responses differ "
                    f"main-vs-candidate by case only, "
                    f"{raw_notes['extracted_turns_order_only']} extracted_turns order-only"
                )
    else:
        skips.append("raw: no shard raw/ dirs under --usage-dir")

    # 9. Claim replay (derived from --usage-dir: sibling claim-replay-full/).
    replay_root = raw_root.parent / "claim-replay-full" if raw_root is not None else None
    if replay_root is not None and replay_root.is_dir():
        try:
            replay_problems, replay_notes = check_replay_consistency(cases, replay_root)
        except (OSError, ValueError, KeyError) as exc:
            failures.append(f"replay: unreadable replay outputs: {exc}")
        else:
            if replay_problems:
                failures.append("replay: " + "; ".join(replay_problems[:10]))
            else:
                skipped = replay_notes["skipped"]
                passes.append(
                    f"replay: {replay_notes['replayed']} entries match the main arm "
                    f"with 0 physical calls; {len(skipped)} skipped "
                    f"({', '.join(skipped)}) all have sidecars"
                )
    else:
        skips.append("replay: no claim-replay-full/ dir beside --usage-dir")

    # 10. Run freshness (optional, needs an explicit run manifest).
    if args.run_manifest is not None:
        try:
            run_manifest = json.loads(args.run_manifest.read_text())
        except (OSError, ValueError) as exc:
            failures.append(f"run-freshness: unreadable run manifest: {exc}")
        else:
            fresh_problems, fresh_not_executed, fresh_notes = check_run_freshness(run_manifest)
            if fresh_problems:
                failures.append("run-freshness: " + "; ".join(fresh_problems[:10]))
            not_executed.extend("run-freshness: " + entry for entry in fresh_not_executed)
            if not fresh_problems and not fresh_not_executed:
                passes.append(
                    f"run-freshness: {fresh_notes['fresh']} artifacts generated after "
                    f"run start {run_manifest.get('run_started_at')!r} "
                    f"(run_id {fresh_notes['run_id']!r})"
                )
    else:
        skips.append("run-freshness: --run-manifest not given")

    for line in passes:
        print("PASS: " + line)
    for line in skips:
        print("SKIP: " + line)
    for line in not_executed:
        print("NOT_EXECUTED: " + line)
    for line in failures:
        print("FAIL: " + line)
    result_line = f"result: {len(passes)} passed, {len(skips)} skipped, {len(failures)} failed"
    if args.run_manifest is not None:
        result_line += f", {len(not_executed)} not executed"
    print(result_line)
    if failures:
        return 1
    if not_executed:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
