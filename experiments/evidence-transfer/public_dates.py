"""Fixed one-day evidence reader on public LoCoMo. Python 3.10+, standard library."""
import argparse
import calendar
import hashlib
import itertools
import json
import math
import random
import re
import time
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
MONTHS.update({name.lower(): i for i, name in enumerate(calendar.month_abbr) if name})
MONTH = "(?:" + "|".join(sorted(MONTHS, key=len, reverse=True)) + ")"
DAY = r"(\d{1,2})(?:st|nd|rd|th)?"
YEAR = r"(\d{4})"
REGEX = [
    (re.compile(r"\b" + YEAR + r"-(\d{1,2})-(\d{1,2})\b"), "ymd"),
    (re.compile(r"\b" + DAY + r"\s+(" + MONTH + r")\.?[,]?\s+" + YEAR + r"\b", re.I), "dmy"),
    (re.compile(r"\b(" + MONTH + r")\.?\s+" + DAY + r"[,]?\s+" + YEAR + r"\b", re.I), "mdy"),
]
OFFSETS = {"the day before yesterday": -2, "yesterday": -1, "today": 0, "tomorrow": 1}
REL = re.compile(r"\b(?:" + "|".join(OFFSETS) + r")\b", re.I)
STOP = set("a an the of in on at by to from when what date did does do is are was were has have had for with and or their his her its she he they it".split())


def literal_days(s):
    result = set()
    for pattern, order in REGEX:
        for match in pattern.finditer(s):
            parts = dict(zip(order, match.groups()))
            month = parts["m"]
            try:
                result.add(date(int(parts["y"]), int(month) if month.isdigit() else MONTHS[month.lower()], int(parts["d"])))
            except ValueError:
                continue
    return result


def gold_day(s):
    # Whole answer must be a full day. A date embedded in a range does not qualify.
    answer = re.sub(r"^(?:on\s+)", "", str(s).strip(), flags=re.I).strip(" .")
    if not any(pattern.fullmatch(answer) for pattern, _ in REGEX):
        return None
    return next(iter(literal_days(answer)), None)


def tokenize(s):
    return [w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in STOP]


def turns(conversation):
    result = []
    for key in sorted((key for key in conversation if re.fullmatch(r"session_\d+", key)), key=lambda k: int(k.split("_")[1])):
        session = literal_days(conversation[key + "_date_time"])
        if len(session) != 1:
            raise ValueError(f"ambiguous session day {key}")
        for turn in conversation[key]:
            result.append((turn["dia_id"], str(turn["speaker"]), turn.get("text", ""), next(iter(session))))
    if len(set(r[0] for r in result)) != len(result):
        raise ValueError("duplicate dialogue ID")
    return result


class BM25:
    def __init__(self, history):
        self.history = history
        self.docs = [Counter(tokenize(speaker + " " + body)) for _, speaker, body, _ in history]
        self.df = Counter(term for doc in self.docs for term in doc)
        self.lengths = [sum(doc.values()) for doc in self.docs]
        self.average = sum(self.lengths) / len(history)

    def retrieve(self, query):
        scores = [0.0] * len(self.docs)
        for term in sorted(set(tokenize(query))):
            freq = self.df[term]
            idf = math.log1p((len(self.docs) - freq + 0.5) / (freq + 0.5))
            for i, doc in enumerate(self.docs):
                tf = doc[term]
                if tf:
                    scores[i] += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * self.lengths[i] / self.average))
        order = sorted(range(len(scores)), key=lambda i: -scores[i])
        return [self.history[i] for i in order[:5] if scores[i] > 0]


def read(slate, relative):
    for cid, _, text, session in slate[:5]:
        body = " ".join(text.split()[:96])
        days = literal_days(body)
        if relative and not days:
            offsets = {OFFSETS[s.lower()] for s in REL.findall(body)}
            anchored = {session + timedelta(days=delta) for delta in offsets}
            days = anchored
        if len(days) == 1:
            return next(iter(days)).isoformat(), cid
    return None, None


def mcnemar(up, down):
    n = up + down
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(min(up, down) + 1)) / 2**n)


def quantiles(values):
    ordered = sorted(values)
    return [ordered[round((len(ordered) - 1) * q)] for q in (0.025, 0.975)]


def summary(rows):
    result = {"n": len(rows), "arms": {}}
    if not rows:
        return result
    for arm in "ABDE":
        values = [row["arms"][arm] for row in rows]
        result["arms"][arm] = {key: sum(v[key] for v in values) for key in ("supported", "exact", "wrong", "refused", "unsupported_exact", "hit1", "hit5", "words")}
        result["arms"][arm]["mean_gold_fraction"] = sum(v["gold_fraction"] for v in values) / len(rows)
    cluster = defaultdict(list)
    for row in rows:
        cluster[row["history"]].append(int(row["arms"]["D"]["supported"]) - int(row["arms"]["B"]["supported"]))
    groups = list(cluster.values())
    signs = list(itertools.product([-1, 1], repeat=len(groups)))
    diffs = list(itertools.chain.from_iterable(groups))
    observed = sum(diffs)
    rng = random.Random(1124)
    sizes_and_sums = [(len(g), sum(g)) for g in groups]
    draws = []
    for _ in range(20000):
        selected = rng.choices(sizes_and_sums, k=len(groups))
        draws.append(100 * sum(s for _, s in selected) / sum(n for n, _ in selected))
    result["paired"] = {"up": diffs.count(1), "down": diffs.count(-1), "delta_pp": 100*observed/len(rows),
                        "cluster_n": len(groups), "cluster_ci_pp": quantiles(draws),
                        "cluster_sign_flip_p":sum(abs(sum(s*sum(g) for s,g in zip(sign,groups))) >= abs(observed) for sign in signs)/len(signs),
                        "question_mcnemar_p": mcnemar(diffs.count(1), diffs.count(-1))}
    return result


def evaluate(data, split):
    sorted_data = sorted(data, key=lambda r: hashlib.sha256(r["sample_id"].encode()).digest())
    subset = {"development": sorted_data[:2], "validation": sorted_data[2:4], "test": sorted_data[4:]}[split]
    rows, exclusions = [], Counter()
    for item in subset:
        history = turns(item["conversation"])
        ids = {t[0] for t in history}
        index = BM25(history)
        for i, qa in enumerate(item["qa"]):
            query = qa["question"].strip()
            if not re.match(r"^(?:when|on what date|what date)\b", query, re.I):
                exclusions["other_question"] += 1
                continue
            if str(qa["category"]) == "5":
                exclusions["adversarial"] += 1
                continue
            gold_ids = set(qa.get("evidence") or [])
            if not gold_ids or gold_ids - ids:
                exclusions["missing_or_unresolved_evidence"] += 1
                continue
            day = gold_day(qa.get("answer", ""))
            if day is None:
                exclusions["not_single_explicit_day"] += 1
                continue
            slate = index.retrieve(query)
            oracle = [turn for turn in history if turn[0] in gold_ids][:5]
            row = {"history":item["sample_id"],"question_index":i,"gold":day.isoformat(),"gold_ids":sorted(gold_ids),"arms":{}}
            for arm, evidence in (("A", []),("B",slate),("D",slate),("E",oracle)):
                answer, citation = read(evidence, relative=arm in "DE")
                exact = answer == day.isoformat()
                selected = [r[0] for r in evidence]
                supported = exact and citation in gold_ids
                row["arms"][arm] = {"answer":answer,"cite":citation,"slate":selected,"exact":exact,"supported":supported,
                                    "unsupported_exact":exact and not supported,"wrong":answer is not None and not exact,
                                    "refused":answer is None,"hit1":bool(set(selected[:1]) & gold_ids),
                                    "hit5":bool(set(selected) & gold_ids),"gold_fraction":len(set(selected) & gold_ids)/len(gold_ids),
                                    "words":sum(min(len(t[2].split()),96) for t in evidence)}
            rows.append(row)
    return rows, {"histories":[d["sample_id"] for d in subset],"exclusions":dict(exclusions), **summary(rows)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("data", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--split", required=True, choices=["development","validation","test"])
    args = parser.parse_args()
    started = time.process_time()
    raw = args.data.read_bytes()
    rows, report = evaluate(json.loads(raw), args.split)
    report.update({"sha256":hashlib.sha256(raw).hexdigest(),"data_bytes":len(raw),"cpu_s":round(time.process_time()-started,4),"split":args.split,"gpu_s":0,"model_tokens":0,"api_cost":0})
    args.out.mkdir(exist_ok=True,parents=True)
    (args.out / (args.split+".json")).write_text(json.dumps(report, indent=2)+"\n",encoding="utf-8")
    (args.out / (args.split+".jsonl")).write_text("".join(json.dumps(row, sort_keys=True)+"\n" for row in rows),encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
