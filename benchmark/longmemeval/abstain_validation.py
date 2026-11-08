"""Abstention-fix validation (evaluation P1, wave 1 of the abstention brief).

silver's two fixes, validated BEFORE either lands in a production path:

- 1a CoT pre-check: the reader system prompt gains a sufficiency check —
  answer only when the memory lines fully and directly support one specific
  answer (same item, same person, same time window, same quantity); a
  similar item, a different time window, or two different numbers for the
  same question means abstain. Single call, JSON output unchanged. NOTE:
  this eval path sends "thinking": {"type": "disabled"}, so the check is
  internal reasoning, not emitted chain-of-thought text. A two-call
  emitted-CoT variant is a follow-up, not what is validated here.
- 1b semantic guardrail: after the baseline answer call, a lightweight
  LLM-as-a-judge compares question vs generated answer against the same
  context; an answer containing a core fact the context does not state is
  intercepted and replaced by an abstention. The judge runs ONLY when the
  answer arm actually answered (an abstention needs no judge).

Validation sets (all contexts are real round-4 sidecars or the PR #84
negative-sample records — no new gold labels are invented):

- abstention set (n=15): the 5 round-4 projected-arm failures
  (031748ae, 0ddfec37, 2133c1b5, 6456829e, gpt4_93159ced) plus a
  fixed-seed (20261108) sample of 10 of the 24 correct abstentions.
  Gate: failures must flip to abstain; correct abstentions must stay
  abstained.
- positive controls (n=10): round-4 judged-correct non-abstention
  questions with sidecars, fixed-seed (20261108) sample. Gate: the reader
  must still answer (abstain stays false) and the answer must still match
  (gold probe contained, or identical to the round-4 answer). A drop here
  is a recall-side regression even though retrieval recall@5 is untouched.
- negative-reader cases (n=7): the PR #84 negative samples rebuilt as
  reader cases — question plus a context that DOES contain the trap record
  (as if projection leaked it). Scored per case on must_contain /
  must_not_contain, not reduced to a single pass/fail: the reader cannot
  see valid_to / outdated / status (projection lines carry no validity
  metadata), so a fail here bounds what a reader-side fix can do rather
  than proving the fix is broken. Staleness must be caught at projection;
  the reader can only catch visible conflicts (ambiguous) and visible
  date mismatches.

Cost: the baseline arm reuses the round-4 cache (identical cache key, $0).
Fresh calls are only the 1a arm (~32), the 1b judge calls (~20) and the
negative-reader baseline/answer calls (~14) — about 66 fresh calls total.
The script never writes into the round-4 cache dirs; fresh entries go into
--work/cache. Usage cost is read from the proxy usage log lines appended
during the run (flash off-peak official prices, same as merge.py:
miss $0.15/1M, hit $0.003/1M, out $0.6/1M).

Usage:
  python benchmark/longmemeval/abstain_validation.py \
    --results work/full/results.json \
    --oracle work/longmemeval_oracle.json \
    --diagnostics work/claim-replay-full/diagnostics \
    --cache-dirs work/full/shard0/cache work/full/shard1/cache \
                 work/full/shard2/cache work/full/shard3/cache \
    --work work/abstain-validation --endpoint http://127.0.0.1:$PORT \
    --usage-log work/abstain-validation/usage.jsonl
"""

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from abstain_experiment import (  # noqa: E402
    header_from_sidecars,
    parse_answer,
)
from claim_tracking import SYSTEM, build_cache_index  # noqa: E402

# 1a: baseline system prompt + sufficiency pre-check. The first fragment is
# kept byte-identical so the proxy still classifies the call as "answer".
SYSTEM_1A = SYSTEM + (
    " Before answering, first judge whether the memory lines fully and directly"
    " support one specific answer: same item, same person, same time window,"
    " same quantity. If a line is only about a similar item, a different time"
    " window, or gives two different numbers for the same question, the"
    " evidence is insufficient — abstain. Do not pick the closest-looking number."
)

SYSTEM_JUDGE = (
    "You judge whether a generated answer is fully and directly supported by"
    ' the provided memory. Return JSON {"supported": true/false, "reason":'
    ' "short reason"}. An answer is supported only if every specific fact in'
    " it (names, numbers, dates) appears in the memory lines about the same"
    " item and time window. A similar item or a different number does not"
    " support it."
)

# Flash off-peak official prices, same constants as the round-4 merge.py.
MISS = 0.15 / 1_000_000
HIT = 0.003 / 1_000_000
OUT = 0.6 / 1_000_000

FAILURES = [
    "031748ae_abs",
    "0ddfec37_abs",
    "2133c1b5_abs",
    "6456829e_abs",
    "gpt4_93159ced_abs",
]

# Fixed-seed (20261108) samples; the seed and the lists are recorded here so
# a rerun reproduces the exact same validation set.
CORRECT_ABSTENTION_SAMPLE = [
    "0862e8bf_abs",
    "15745da0_abs",
    "19b5f2b3_abs",
    "2311e44b_abs",
    "80ec1f4f_abs",
    "982b5123_abs",
    "a96c20ee_abs",
    "ba358f49_abs",
    "bc8a6e93_abs",
    "gpt4_c27434e8_abs",
]

POSITIVE_CONTROLS = [
    "0a34ad58",
    "58bf7951",
    "bc8a6e93",
    "cc5ded98",
    "d7c942c3",
    "dfde3500",
    "e66b632c",
    "e831120c",
    "gpt4_1d4ab0c9",
    "gpt4_2655b836",
]


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def call_key(
    system: str, question: str, question_date: str, memory: str, user_override=None
) -> str:
    """Cache key for a call: sha256(json.dumps([SYSTEM, user, 512])) — the
    same shape worker.py's model() computes, with the system prompt swapped
    for the arm under test. Baseline keys therefore match round 4 exactly.
    The judge arm passes its full user string via user_override."""
    user = (
        user_override
        if user_override is not None
        else json.dumps(
            {
                "question": question,
                "question_date": question_date,
                "memory": memory.strip(),
            }
        )
    )
    return sha(json.dumps([system, user, 512]))


def run_call(
    system, question, question_date, memory, endpoint, cache_index, work_cache, user_override=None
):
    """One model call with cache lookup (round-4 index, then this run's own
    cache). Returns (output, from_cache, key). Never writes into the round-4
    cache dirs; fresh entries go into work_cache."""
    key = call_key(system, question, question_date, memory, user_override)
    hit_path = cache_index.get(key + ".json")
    if hit_path is not None:
        hit = json.loads(hit_path.read_text(encoding="utf-8"))
        if "error" not in hit:
            return hit.get("output"), True, key
    path = work_cache / (key + ".json")
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        if "error" not in saved:
            return saved.get("output"), True, key
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "compiler"))
    from model_client import call_llm

    user = (
        user_override
        if user_override is not None
        else json.dumps(
            {
                "question": question,
                "question_date": question_date,
                "memory": memory.strip(),
            }
        )
    )
    started = time.monotonic()
    try:
        output = call_llm(
            system,
            user,
            max_tokens=512,
            config={
                "server_url": endpoint,
                "model_id": "stellamem-eval",
                "model_timeout": 120,
            },
        )
        saved = {"output": output, "seconds": time.monotonic() - started}
    except RuntimeError as exc:
        saved = {
            "error": type(exc).__name__,
            "detail": str(exc),
            "seconds": time.monotonic() - started,
        }
    path.write_text(json.dumps(saved), encoding="utf-8")
    if "error" in saved:
        raise RuntimeError(f"model call failed: {saved}")
    return saved["output"], False, key


def parse_judge(output: str):
    """Parse a judge verdict. Returns (supported | None, reason). None means
    the verdict was not parseable JSON — recorded as-is, never guessed."""
    try:
        data = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return None, output
    if not isinstance(data, dict):
        return None, output
    return bool(data.get("supported")), str(data.get("reason", ""))


def build_judge_user(question, question_date, memory, answer):
    """Judge user message. It must start with 'I will give you a question'
    so the eval proxy classifies the call as kind=judge in the usage log."""
    return "I will give you a question, the memory lines and a generated answer. " + json.dumps(
        {
            "question": question,
            "question_date": question_date,
            "memory": memory.strip(),
            "generated_answer": answer,
        },
        ensure_ascii=False,
    )


def judge_answer(question, question_date, memory, answer, endpoint, cache_index, work_cache):
    """1b judge: is the generated answer fully and directly supported by the
    same context? Returns (supported | None, reason, from_cache)."""
    user = build_judge_user(question, question_date, memory, answer)
    output, cached, _ = run_call(
        SYSTEM_JUDGE,
        question,
        question_date,
        memory,
        endpoint,
        cache_index,
        work_cache,
        user_override=user,
    )
    supported, reason = parse_judge(output)
    return supported, reason, cached


def build_negative_cases(negative_path: Path, header: str):
    """PR #84 negative samples rebuilt as reader cases: the trap record IS
    in the context (as if projection leaked it). The reader cannot see
    valid_to / outdated / status — projection lines carry no validity
    metadata — so these cases bound what a reader-side fix can do."""
    cases = json.loads(negative_path.read_text(encoding="utf-8"))
    out = []
    for case in cases:
        lines = [header.strip()]
        for rec in case["records"]:
            lines.append(
                json.dumps(
                    {
                        "id": rec["claim_id"],
                        "scope": rec["predicate"],
                        "predicate": rec["predicate"],
                        "value": rec["value"],
                    },
                    ensure_ascii=False,
                )
            )
        out.append(
            {
                "id": case["id"],
                "question": case["question"],
                # No question_date exists for synthetic cases; the reader
                # prompt carries an empty date rather than an invented one.
                "question_date": "",
                "memory": "\n".join(lines) + "\n",
                "must_contain": case["must_contain"],
                "must_not_contain": case["must_not_contain"],
            }
        )
    return out


def gold_probe(answer):
    """Short distinctive probe of a gold answer for positive controls:
    first 30 chars, lowercased, trailing punctuation stripped."""
    probe = (answer or "").strip().lower()
    probe = probe[:30].rstrip(" .。!！?？")
    return probe


def cost_of(entry):
    miss = entry.get("prompt_cache_miss_tokens") or 0
    hit = entry.get("prompt_cache_hit_tokens") or 0
    out = entry.get("completion_tokens") or 0
    if miss == 0 and hit == 0 and entry.get("prompt_tokens"):
        miss = entry["prompt_tokens"]
    return miss * MISS + hit * HIT + out * OUT


def usage_cost(usage_log: Path, start_seq: int):
    """Sum cost + tokens over usage-log lines appended after start_seq."""
    if usage_log is None or not usage_log.exists():
        return None
    calls = prompt = completion = 0
    total = 0.0
    for line in usage_log.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("seq", 0) <= start_seq:
            continue
        calls += 1
        prompt += entry.get("prompt_tokens") or 0
        completion += entry.get("completion_tokens") or 0
        total += cost_of(entry)
    return {
        "fresh_calls": calls,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "cost_usd": round(total, 4),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--results", type=Path, required=True)
    p.add_argument("--oracle", type=Path, required=True)
    p.add_argument("--diagnostics", type=Path, required=True)
    p.add_argument("--cache-dirs", nargs="+", type=Path, required=True)
    p.add_argument("--work", type=Path, required=True)
    p.add_argument("--endpoint", required=True)
    p.add_argument("--usage-log", type=Path, default=None)
    p.add_argument("--negative", type=Path, default=None)
    p.add_argument("--only", default="", help="comma-separated question ids")
    args = p.parse_args()

    oracle = {q["question_id"]: q for q in json.loads(args.oracle.read_text(encoding="utf-8"))}
    results = json.loads(args.results.read_text(encoding="utf-8"))
    round4_answer = {
        c["question_id"]: c.get("arms", {}).get("main", {}).get("answer_text", "")
        for c in results["cases"]
    }
    header = header_from_sidecars(args.diagnostics)
    contexts = {}
    for qid in list(oracle):
        sidecar = args.diagnostics / (sha(oracle[qid]["question"])[:16] + ".json")
        if sidecar.exists():
            contexts[qid] = json.loads(sidecar.read_text(encoding="utf-8")).get("context", "")

    only = {q for q in args.only.split(",") if q} if args.only else None

    # Validation sets: (set name, [(qid, question, date, memory), ...]).
    def sidecar_case(qid):
        q = oracle[qid]
        return (qid, q["question"], q["question_date"], contexts[qid])

    abstention_qids = FAILURES + CORRECT_ABSTENTION_SAMPLE
    sets = {
        "abstention": [sidecar_case(q) for q in abstention_qids if q in contexts],
        "positive": [sidecar_case(q) for q in POSITIVE_CONTROLS if q in contexts],
    }
    if only:
        sets = {k: [c for c in v if c[0] in only] for k, v in sets.items()}

    negative_cases = []
    if args.negative:
        negative_cases = build_negative_cases(args.negative, header)
        if only:
            negative_cases = [c for c in negative_cases if c["id"] in only]

    cache_index = build_cache_index(args.cache_dirs)
    work_cache = args.work / "cache"
    work_cache.mkdir(parents=True, exist_ok=True)
    out_path = args.work / "validation_results.json"
    out = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}

    # Usage-log seq numbers start at 0: an empty log must count the first
    # appended line, so the floor is -1, not 0.
    start_seq = -1
    if args.usage_log and args.usage_log.exists():
        for line in args.usage_log.read_text(encoding="utf-8").splitlines():
            try:
                start_seq = max(start_seq, json.loads(line).get("seq", -1))
            except json.JSONDecodeError:
                continue

    fresh = 0

    def record(set_name, qid, arm, payload):
        out.setdefault(set_name, {}).setdefault(qid, {})[arm] = payload
        out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    # Arms on the sidecar sets.
    for set_name, cases in sets.items():
        for qid, question, qdate, memory in cases:
            # Baseline: identical key to round 4 — pure cache hit.
            if "baseline" not in out.get(set_name, {}).get(qid, {}):
                output, cached, _ = run_call(
                    SYSTEM, question, qdate, memory, args.endpoint, cache_index, work_cache
                )
                abstain, answer = parse_answer(output)
                record(
                    set_name,
                    qid,
                    "baseline",
                    {"abstain": abstain, "answer": answer, "from_cache": cached},
                )
                fresh += 0 if cached else 1
            # 1a: sufficiency pre-check prompt (fresh call).
            if "1a" not in out.get(set_name, {}).get(qid, {}):
                output, cached, _ = run_call(
                    SYSTEM_1A, question, qdate, memory, args.endpoint, cache_index, work_cache
                )
                abstain, answer = parse_answer(output)
                record(
                    set_name,
                    qid,
                    "1a",
                    {"abstain": abstain, "answer": answer, "from_cache": cached},
                )
                fresh += 0 if cached else 1
            # 1b: baseline answer + judge gate (judge only if it answered).
            if "1b" not in out.get(set_name, {}).get(qid, {}):
                base = out[set_name][qid]["baseline"]
                final_abstain, final_answer = base["abstain"], base["answer"]
                judge = None
                if base["abstain"] is False:
                    supported, reason, jcached = judge_answer(
                        question,
                        qdate,
                        memory,
                        base["answer"],
                        args.endpoint,
                        cache_index,
                        work_cache,
                    )
                    fresh += 0 if jcached else 1
                    judge = {"supported": supported, "reason": reason}
                    if supported is False:
                        final_abstain, final_answer = True, ""
                record(
                    set_name,
                    qid,
                    "1b",
                    {
                        "abstain": final_abstain,
                        "answer": final_answer,
                        "judge": judge,
                        "baseline_answer": base["answer"],
                    },
                )
            print(
                f"[{set_name}] {qid}: baseline={out[set_name][qid]['baseline']['abstain']} "
                f"1a={out[set_name][qid]['1a']['abstain']} "
                f"1b={out[set_name][qid]['1b']['abstain']}",
                flush=True,
            )

    # Negative-reader cases: fresh baseline (no round-4 cache exists for
    # these synthetic contexts), then 1a and 1b the same way.
    for case in negative_cases:
        qid, question, qdate, memory = (
            case["id"],
            case["question"],
            case["question_date"],
            case["memory"],
        )
        if "baseline" not in out.get("negative", {}).get(qid, {}):
            output, cached, _ = run_call(
                SYSTEM, question, qdate, memory, args.endpoint, cache_index, work_cache
            )
            abstain, answer = parse_answer(output)
            record(
                "negative",
                qid,
                "baseline",
                {"abstain": abstain, "answer": answer, "from_cache": cached},
            )
            fresh += 0 if cached else 1
        if "1a" not in out.get("negative", {}).get(qid, {}):
            output, cached, _ = run_call(
                SYSTEM_1A, question, qdate, memory, args.endpoint, cache_index, work_cache
            )
            abstain, answer = parse_answer(output)
            record(
                "negative", qid, "1a", {"abstain": abstain, "answer": answer, "from_cache": cached}
            )
            fresh += 0 if cached else 1
        if "1b" not in out.get("negative", {}).get(qid, {}):
            base = out["negative"][qid]["baseline"]
            final_abstain, final_answer = base["abstain"], base["answer"]
            judge = None
            if base["abstain"] is False:
                supported, reason, jcached = judge_answer(
                    question,
                    qdate,
                    memory,
                    base["answer"],
                    args.endpoint,
                    cache_index,
                    work_cache,
                )
                fresh += 0 if jcached else 1
                judge = {"supported": supported, "reason": reason}
                if supported is False:
                    final_abstain, final_answer = True, ""
            record(
                "negative",
                qid,
                "1b",
                {
                    "abstain": final_abstain,
                    "answer": final_answer,
                    "judge": judge,
                    "baseline_answer": base["answer"],
                },
            )
        print(
            f"[negative] {qid}: baseline={out['negative'][qid]['baseline']['abstain']} "
            f"1a={out['negative'][qid]['1a']['abstain']} "
            f"1b={out['negative'][qid]['1b']['abstain']}",
            flush=True,
        )

    # Summary.
    def arm_stats(set_name, arm):
        rows = out.get(set_name, {})
        n = len(rows)
        abstained = sum(1 for r in rows.values() if r.get(arm, {}).get("abstain") is True)
        answered = sum(1 for r in rows.values() if r.get(arm, {}).get("abstain") is False)
        unknown = n - abstained - answered
        return n, abstained, answered, unknown

    print("\n== abstention set (want: failures flip to abstain, correct stay abstained) ==")
    for arm in ("baseline", "1a", "1b"):
        n, ab, an, un = arm_stats("abstention", arm)
        fails = [
            q
            for q in FAILURES
            if q in out.get("abstention", {})
            and out["abstention"][q].get(arm, {}).get("abstain") is not True
        ]
        print(
            f"{arm}: n={n} abstained={ab} answered={an} unparseable={un} failures-still-answering={fails}"
        )

    print("\n== positive controls (want: still answer, answer still matches) ==")
    for arm in ("baseline", "1a", "1b"):
        kept = 0
        rows = out.get("positive", {})
        for qid, r in rows.items():
            cell = r.get(arm, {})
            if cell.get("abstain") is not False:
                continue
            ans = (cell.get("answer") or "").strip().lower()
            probe = gold_probe(oracle[qid].get("answer", ""))
            if (probe and probe in ans) or ans == (
                round4_answer.get(qid, "") or ""
            ).strip().lower():
                kept += 1
        n, ab, an, un = arm_stats("positive", arm)
        print(f"{arm}: n={n} abstained={ab} answered={an} unparseable={un} answer-kept={kept}/{n}")

    print("\n== negative-reader cases (per-case must/must-not, see results file) ==")
    for arm in ("baseline", "1a", "1b"):
        safe = 0
        rows = out.get("negative", {})
        for case in negative_cases:
            qid = case["id"]
            cell = rows.get(qid, {}).get(arm, {})
            ans = cell.get("answer") or ""
            ok_not = all(s not in ans for s in case["must_not_contain"])
            ok_has = all(s in ans for s in case["must_contain"])
            if ok_not and ok_has:
                safe += 1
        print(f"{arm}: must/must-not clean={safe}/{len(negative_cases)}")

    cost = usage_cost(args.usage_log, start_seq) if args.usage_log else None
    print(f"\nfresh model calls (this run): {fresh}")
    print(
        f"usage-log cost: {json.dumps(cost, ensure_ascii=False) if cost else 'unmeasured (no --usage-log)'}"
    )
    print(f"results: {out_path}")


if __name__ == "__main__":
    main()
