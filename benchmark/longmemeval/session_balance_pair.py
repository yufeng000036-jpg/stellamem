"""Paired research control: fixed-budget lexical snippets with or without session diversification."""

import argparse
import hashlib
import json
import math
import re
import time
from collections import Counter, defaultdict
from pathlib import Path

from reader_pair import BASE, DATA_SHA, category, evaluate_output, infer, selected, sha


def sample(cases):
    # Disjoint from the 14-case prompt pilot, frozen before reader inference.
    pilot_ids = {r["question_id"] for r in selected(cases, 2)}
    groups = defaultdict(list)
    for row in cases:
        if row["question_id"] not in pilot_ids:
            groups[category(row)].append(row)
    for key in sorted(groups):
        yield from sorted(groups[key], key=lambda r: sha("session-balance-v1:" + r["question_id"]))[
            :14
        ]


def evidence(row):
    docs, bags = [], []
    for si, turns in enumerate(row["haystack_sessions"]):
        for ti, turn in enumerate(turns):
            text = turn["content"][:700]
            key = f"s{si}t{ti}"
            line = json.dumps(
                dict(id=key, role=turn["role"], date=row["haystack_dates"][si], text=text),
                ensure_ascii=False,
            )
            docs.append((si, ti, key, line))
            bags.append(Counter(re.findall(r"[a-z0-9]+", text.lower())))
    df = Counter(word for bag in bags for word in bag)
    query = set(re.findall(r"[a-z0-9]+", row["question"].lower()))
    scores = [
        sum(
            math.log(1 + (len(docs) - df[w] + 0.5) / (df[w] + 0.5)) * bag[w] / (bag[w] + 1.2)
            for w in sorted(query)
            if w in bag
        )
        for bag in bags
    ]
    ranking = sorted(range(len(docs)), key=lambda i: (-scores[i], i))
    for balanced in (False, True):
        pool, counts, lines, refs, size = ranking.copy(), Counter(), [], {}, 0
        while pool:
            best = (
                max(pool, key=lambda i: scores[i] / (1 + 0.5 * counts[docs[i][0]]))
                if balanced
                else pool[0]
            )
            pool.remove(best)
            si, ti, key, line = docs[best]
            if size + len(line) + 1 > 4000:
                continue
            size += len(line) + 1
            lines.append(line)
            refs[key] = (si, ti)
            counts[si] += 1
        yield "\n".join(lines), refs


def run(args):
    raw = args.dataset.read_bytes()
    if hashlib.sha256(raw).hexdigest() != DATA_SHA:
        raise ValueError("dataset digest mismatch")
    with args.out.open("x") as audit, args.review.open("x") as local:
        for row in sample(json.loads(raw)):
            gold = {
                (i, j)
                for i, ts in enumerate(row["haystack_sessions"])
                for j, t in enumerate(ts)
                if t.get("has_answer")
            }
            arms = list(zip(("baseline", "candidate"), evidence(row)))
            if int(sha(row["question_id"])[0], 16) % 2:
                arms.reverse()
            fresh = {}
            for name, (memory, refs) in arms:
                user = json.dumps(
                    dict(
                        question=row["question"], question_date=row["question_date"], memory=memory
                    )
                )
                key = sha(user)
                record = dict(
                    question_id=row["question_id"],
                    category=category(row),
                    arm=name,
                    input_sha256=key,
                    prompt_sha256=sha(BASE),
                    chars=len(memory),
                    identical_reuse=key in fresh,
                )
                turns = set(refs.values())
                if category(row) != "abstention" and gold:
                    record.update(
                        full_gold_turns=gold <= turns,
                        turn_precision=len(turns & gold) / len(turns) if turns else 0,
                        recall5=len(set(list(refs.values())[:5]) & gold) / len(gold),
                    )
                start = time.monotonic()
                if key not in fresh:
                    # Transport errors interrupt the batch; they must not cascade into 98 fake scores.
                    fresh[key] = infer(args.port, BASE, user)
                choice, record["usage"] = fresh[key]
                output = choice["message"]["content"].strip()
                try:
                    if choice["finish_reason"] != "stop":
                        raise ValueError("incomplete output")
                    record.update(evaluate_output(output, refs, row))
                except (ValueError, KeyError, IndexError, TypeError) as exc:
                    record.update(error=type(exc).__name__, exact=False)
                record["seconds"] = round(time.monotonic() - start, 3)
                print(json.dumps(record), file=audit, flush=True)
                print(
                    json.dumps(dict(question_id=row["question_id"], arm=name, raw=output)),
                    file=local,
                    flush=True,
                )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument(
        "review", type=Path, help="local-only raw predictions for independent review"
    )
    parser.add_argument("--port", type=int, required=True)
    run(parser.parse_args())
