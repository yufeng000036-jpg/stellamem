"""Matched order-only follow-up on development cases with unchanged evidence membership."""

import argparse
import hashlib
import json
import time
from pathlib import Path

from reader_pair import BASE, DATA_SHA, category, evaluate_output, infer, sha
from session_balance_pair import evidence, sample


def run(args):
    raw = args.dataset.read_bytes()
    if hashlib.sha256(raw).hexdigest() != DATA_SHA:
        raise ValueError("dataset digest mismatch")
    baseline = {
        r["question_id"]: r
        for line in args.previous.read_text().splitlines()
        if (r := json.loads(line))["arm"] == "baseline"
    }
    with args.out.open("x") as output:
        for row in sample(json.loads(raw)):
            if category(row) not in {"knowledge-update", "temporal-reasoning"}:
                continue
            qid = row["question_id"]
            original, refs = next(evidence(row))
            lines = original.splitlines()
            ordered = sorted(
                lines,
                key=lambda line: (
                    row["haystack_dates"][refs[json.loads(line)["id"]][0]],
                    refs[json.loads(line)["id"]],
                ),
                reverse=True,
            )
            memory = "\n".join(ordered)

            def payload(text):
                return json.dumps(
                    dict(question=row["question"], question_date=row["question_date"], memory=text)
                )

            old = baseline[qid]
            if (
                old["input_sha256"] != sha(payload(original))
                or old["prompt_sha256"] != sha(BASE)
                or "usage" not in old
            ):
                raise ValueError("reference request or model usage mismatch")
            print(json.dumps(old), file=output, flush=True)
            if memory == original:
                result = dict(old, arm="candidate", identical_reuse=True, seconds=0)
            else:
                started = time.monotonic()
                choice, usage = infer(args.port, BASE, payload(memory))
                result = dict(
                    question_id=qid,
                    category=category(row),
                    arm="candidate",
                    input_sha256=sha(payload(memory)),
                    prompt_sha256=sha(BASE),
                    chars=len(memory),
                    usage=usage,
                    seconds=round(time.monotonic() - started, 3),
                )
                try:
                    if choice["finish_reason"] != "stop":
                        raise ValueError("incomplete generation")
                    result.update(evaluate_output(choice["message"]["content"].strip(), refs, row))
                except (ValueError, KeyError, TypeError, IndexError) as exc:
                    result.update(error=type(exc).__name__, exact=False)
            print(json.dumps(result), file=output, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("dataset", "previous", "out"):
        parser.add_argument(arg, type=Path)
    parser.add_argument("--port", type=int, required=True)
    run(parser.parse_args())
