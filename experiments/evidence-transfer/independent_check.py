"""Secondary audit of published decisions via SQL and source-byte hashes; no ranking code reused."""

import argparse
import hashlib
import json
import sqlite3
import re
from datetime import datetime
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("data", type=Path)
    p.add_argument("results", type=Path)
    a = p.parse_args()
    raw = a.data.read_bytes()
    if (
        hashlib.sha256(raw).hexdigest()
        != "79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4"
    ):
        raise ValueError("different LoCoMo data")
    histories = {h["sample_id"]: h for h in json.loads(raw)}
    splits = []
    ids = sorted(histories, key=lambda k: hashlib.sha256(k.encode()).digest())
    for name, expected in (("development", ids[:2]), ("validation", ids[2:4]), ("test", ids[4:])):
        totals = json.loads((a.results / (name + ".json")).read_bytes())
        if set(expected) != set(totals["histories"]):
            raise ValueError("split membership drift")
        db = sqlite3.connect(":memory:")
        db.execute(
            "CREATE TABLE outcomes(h TEXT,q INTEGER,arm TEXT,ok INTEGER,exact INTEGER,wrong INTEGER,ref INTEGER,hit INTEGER, PRIMARY KEY(h,q,arm))"
        )
        for line in (a.results / (name + ".jsonl")).read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r["history"] not in expected:
                raise ValueError("split contamination")
            qa = histories[r["history"]]["qa"][r["question_index"]]
            support = set(qa["evidence"])
            if support != set(r["gold_ids"]):
                raise ValueError("evidence drift")
            # Independently normalize full civil date answers for the included rows.
            answer = re.sub(
                r"(?<=\d)(?:st|nd|rd|th)\b", "", str(qa["answer"]).strip(" ."), flags=re.I
            )
            answer = re.sub(r"^on\s+", "", answer.replace(",", ""), flags=re.I)
            for fmt in ("%d %B %Y", "%B %d %Y", "%d %b %Y", "%b %d %Y", "%Y-%m-%d"):
                try:
                    day = datetime.strptime(answer, fmt).date().isoformat()
                    break
                except ValueError:
                    pass
            else:
                raise ValueError("unknown gold day: " + answer)
            if day != r["gold"]:
                raise ValueError("calendar normalization differed")
            if r["arms"]["B"]["slate"] != r["arms"]["D"]["slate"]:
                raise ValueError("unmatched retrieval")
            for arm, x in r["arms"].items():
                exact = day == x["answer"]
                good = exact and x["cite"] in support
                if x["cite"] is not None and x["cite"] not in x["slate"]:
                    raise ValueError("unseen citation")
                db.execute(
                    "INSERT INTO outcomes VALUES (?,?,?,?,?,?,?,?)",
                    (
                        r["history"],
                        r["question_index"],
                        arm,
                        good,
                        exact,
                        x["answer"] is not None and not exact,
                        x["answer"] is None,
                        bool(set(x["slate"]) & support),
                    ),
                )
        for arm, n, ok, exact, wrong, ref, hit in db.execute(
            "SELECT arm,count(*),sum(ok),sum(exact),sum(wrong),sum(ref),sum(hit) FROM outcomes GROUP BY arm"
        ):
            expected_totals = totals["arms"][arm]
            if (n, ok, exact, wrong, ref, hit) != (
                totals["n"],
                *(expected_totals[k] for k in ("supported", "exact", "wrong", "refused", "hit5")),
            ):
                raise ValueError("counts disagree")
        splits.append((name, totals["n"]))
        db.close()
    print("SQL outcome checks passed:", splits)


if __name__ == "__main__":
    main()
