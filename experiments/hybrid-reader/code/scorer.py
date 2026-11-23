"""Baseline scorer for the reader-train experiment. RULES FROZEN at write
time (2026-11-22, before any baseline output existed). Do not edit the
classification rules after outputs are produced; if a rule must change,
that is a new scorer version and all arms must be re-scored with it.

Shared output parsing (both sets):
  - strip whitespace; strip one level of ``` code fences; json.loads.
  - Require: object with answer:str, citations:list, abstain:bool (strict).
    Anything else = FORMAT_FAIL (counted as an error, listed separately).
  - Non-string citation entries are dropped. Cited ids not present in the
    question's slate are counted in the nonexistent-citation metric.

HOLDOUT set (gold = holdout_v1.jsonl: gold_answer / gold_citations /
gold_abstain):
  - gold_abstain=True : abstain -> CORRECT_ABSTAIN; answered -> WRONG_ANSWER
    (false answer on an abstain question).
  - gold_abstain=False: abstain -> OVER_ABSTAIN; answered -> CORRECT iff
    answers_equal(pred, gold) else WRONG_ANSWER.
  - answers_equal: (a) normalized strings equal, where normalization =
    lowercase, full-width -> half-width, strip a trailing unit char from a
    fixed unit list, keep only [0-9a-z CJK]; or (b) both sides parse as a
    single number (Arabic or Chinese numerals) with equal value; or
    (c) both sides contain a full calendar date, the date tuples are
    equal, and the normalized remainders (dates removed) are equal.
  - Citation validity: among CORRECT rows, fraction with
    set(citations) == set(gold_citations).

B2 189 set (gold source = the frozen experiment artifacts; no gold answer
strings exist for these questions):
  - gold support set = b2_rows.jsonl `gold_ids` (from prep_reader_data.py:
    stage-2 claim-tracking CSVs restricted to the replay diagnostics
    slate). NOTE: gold_ids equals the whole slate on 186/189 rows, so it
    is not a discriminating support set; correctness therefore follows
    the established caliber of analyze_reader2.py (strict), copied below:
    answered + some cited gold claim whose diagnostics text "carries" the
    answer (heuristic below, verbatim), with the outdated-bearing
    downgrade (that carrying claim superseded_by another slate claim ->
    WRONG). klass H = gold abstain; F/G abstain -> OVER_ABSTAIN.
  - A cross-check column also computes the analyze_reader.py (v1) caliber
    (same but WITHOUT the outdated downgrade) for the A arm.
  - Citation validity (189 adaptation, documented in the report): among
    CORRECT rows, fraction with set(citations) == set of slate claims
    whose text carries the model's answer.

Latency: per (set, arm) mean and p95 (nearest-rank) of recorded secs.
A:b2 latencies and raw outputs come from the frozen v4 results.jsonl
(B2:BASE records); all other cells from baseline_outputs.jsonl.
"""
import glob, json, math, os, re, sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
HOLDOUT = "/home/hatch/workspace/reader-train/data/holdout_v1.jsonl"
EXP = "/home/hatch/workspace/stellamem-takeover/v4-reader-exp-20261121"
W = "/home/hatch/workspace/longmemeval-wse-20261118/work"

# ---------------------------------------------------------------- parsing
def parse_output(raw):
    """Return dict or None (format failure)."""
    v = (raw or "").strip()
    if v.startswith("```"):
        v = v.split("\n", 1)[1].rsplit("```", 1)[0] if "\n" in v else v
    try:
        a = json.loads(v)
    except Exception:
        return None
    if not isinstance(a, dict):
        return None
    if not isinstance(a.get("answer"), str):
        return None
    if not isinstance(a.get("citations"), list):
        return None
    if type(a.get("abstain")) is not bool:
        return None
    a = dict(a)
    a["citations"] = [c for c in a["citations"] if isinstance(c, str)]
    return a

def slate_ids(context):
    ids = []
    for line in (context or "").split("\n")[1:]:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if obj.get("id"):
            ids.append(obj["id"])
    return ids

# ------------------------------------------------------- holdout equality
UNITS = "元块个本张次天年月日号岁米克人只条件套瓶盒包份台节课"
FW = {chr(0xFF01 + i): chr(0x21 + i) for i in range(94)}

def halfwidth(s):
    return "".join(FW.get(ch, ch) for ch in s)

def norm(s):
    s = halfwidth((s or "").lower()).strip()
    s = re.sub(r"\s+", "", s)
    if s and s[-1] in UNITS and len(s) > 1:
        s = s[:-1]
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", s)

CN_NUM = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
          "六": 6, "七": 7, "八": 8, "九": 9}

def parse_cn_number(s):
    if not s or any(ch not in CN_NUM and ch not in "十百千万" for ch in s):
        return None
    total, section, number = 0, 0, 0
    for ch in s:
        if ch in CN_NUM:
            number = CN_NUM[ch]
        elif ch == "十":
            section += (number or 1) * 10; number = 0
        elif ch == "百":
            section += (number or 1) * 100; number = 0
        elif ch == "千":
            section += (number or 1) * 1000; number = 0
        elif ch == "万":
            total += (section + number) * 10000; section, number = 0, 0
    return total + section + number

def parse_number(s):
    """Parse s as a single number if the whole normalized string is one."""
    t = halfwidth((s or "").strip()).replace(",", "")
    t = re.sub(r"\s+", "", t)
    if t and t[-1] in UNITS and len(t) > 1:
        t = t[:-1]
    if re.fullmatch(r"\d+(?:\.\d+)?", t):
        return float(t)
    v = parse_cn_number(t)
    return float(v) if v is not None else None

DATE_RE = re.compile(r"(\d{4})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})\s*日?")

def extract_date(s):
    m = DATE_RE.search(s or "")
    if not m:
        return None, s
    tup = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return tup, (s[:m.start()] + s[m.end():])

def answers_equal(pred, gold):
    if norm(pred) == norm(gold):
        return True
    np_, ng = parse_number(pred), parse_number(gold)
    if np_ is not None and ng is not None and np_ == ng:
        return True
    dp, rp = extract_date(pred)
    dg, rg = extract_date(gold)
    if dp is not None and dg is not None and dp == dg and norm(rp) == norm(rg):
        return True
    return False

# ------------------------------------------- b2 historical caliber (verbatim
# helpers from analyze_reader2.py, plus the v1 variant without downgrade)
STOP = set("the a an of to in on for and or is was were are be been at by with as it its from that this i my me we you he she they them his her our your their not no yes do does did have has had".split())

def b2_norm(s):
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", (s or "").lower())

def content_tokens(s):
    toks = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", (s or "").lower())
    return [t for t in toks if t not in STOP and len(t) >= 2 or (t.isdigit())]

def numbers(s):
    return re.findall(r"\d+(?:\.\d+)?", (s or "").replace(",", ""))

def carries(claim_text, hyp):
    text_n, hyp_n = b2_norm(claim_text), b2_norm(hyp)
    if hyp_n and len(hyp_n) >= 4 and hyp_n in text_n:
        return True
    ht = content_tokens(hyp)
    if ht:
        ct = set(content_tokens(claim_text))
        hit = sum(1 for t in ht if t in ct)
        if len(ht) >= 2 and hit / len(ht) >= 0.6:
            return True
        if len(ht) == 1 and ht[0] in ct:
            return True
    hn = numbers(hyp)
    if hn and all(n in numbers(claim_text) for n in hn) and len(b2_norm(hyp)) <= 8:
        return True
    return False

def load_diag():
    diag = {}
    for d in ("replay-stage2", "replay-topup2"):
        man = json.load(open(f"{W}/{d}/manifest.json", encoding="utf-8"))
        by_prefix = {f.rsplit("/", 1)[-1][:-5]: f for f in glob.glob(f"{W}/{d}/diagnostics/*.json")}
        for prefix, meta in man.items():
            f = by_prefix.get(prefix)
            if not f:
                continue
            dg = json.load(open(f, encoding="utf-8"))
            claims, order = {}, []
            for line in (dg.get("context") or "").split("\n")[1:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                cid = obj.get("id")
                if not cid or cid.startswith("stellamem:"):
                    continue
                claims[cid] = {
                    "text": " ".join(str(obj.get(k) or "") for k in ("scope", "predicate", "value")),
                    "superseded_by": obj.get("superseded_by"),
                }
                order.append(cid)
            diag[meta["question_id"]] = {"claims": claims, "order": order}
    return diag

def classify_b2(row, a, diag, downgrade=True):
    """a = parsed output dict. Returns outcome string."""
    dg = diag[row["qid"]]
    gold = set(row["gold_ids"])
    if row["klass"] == "H":
        return "correct_abstain" if a["abstain"] else "wrong_answer"
    if a["abstain"]:
        return "over_abstain"
    cited = [c for c in a["citations"] if c in dg["claims"]]
    for cid in cited:
        if cid in gold and carries(dg["claims"][cid]["text"], a["answer"]):
            if downgrade:
                sb = dg["claims"][cid].get("superseded_by")
                if sb and sb in dg["claims"]:
                    return "wrong_answer"
            return "correct"
    return "wrong_answer"

# ---------------------------------------------------------------- scoring
def pct(num, den):
    return round(100.0 * num / den, 2) if den else None

def lat_stats(secs):
    if not secs:
        return {"n": 0, "mean": None, "p95": None}
    ss = sorted(secs)
    p95 = ss[min(len(ss) - 1, math.ceil(0.95 * len(ss)) - 1)]
    return {"n": len(ss), "mean": round(sum(ss) / len(ss), 2), "p95": round(p95, 2)}

def score_holdout(rows, outputs):
    per_q, by_klass = {}, defaultdict(Counter)
    secs = []
    cit_valid = cit_total = 0
    nonexistent_rows = nonexistent_ids = 0
    for row in rows:
        qid = row["qid"]
        rec = outputs.get(qid)
        slate = set(slate_ids(row["context"]))
        if rec is None or "raw" not in rec:
            outcome = "missing"
            a = None
        else:
            if rec.get("secs") is not None:
                secs.append(rec["secs"])
            a = parse_output(rec["raw"])
            if a is None:
                outcome = "format_fail"
            else:
                bad = [c for c in a["citations"] if c not in slate]
                if bad:
                    nonexistent_rows += 1
                    nonexistent_ids += len(bad)
                if row["gold_abstain"]:
                    outcome = "correct_abstain" if a["abstain"] else "wrong_answer"
                elif a["abstain"]:
                    outcome = "over_abstain"
                elif answers_equal(a["answer"], row["gold_answer"]):
                    outcome = "correct"
                    cit_total += 1
                    if set(a["citations"]) == set(row["gold_citations"]):
                        cit_valid += 1
                else:
                    outcome = "wrong_answer"
        per_q[qid] = outcome
        by_klass[row["klass"]][outcome] += 1
        by_klass["ALL"][outcome] += 1
    agg = by_klass["ALL"]
    n = len(rows)
    n_ans = sum(1 for r in rows if not r["gold_abstain"])
    n_abs = n - n_ans
    return {
        "n": n, "n_answerable": n_ans, "n_abstain_gold": n_abs,
        "correct": agg["correct"], "wrong_answer": agg["wrong_answer"],
        "over_abstain": agg["over_abstain"], "correct_abstain": agg["correct_abstain"],
        "format_fail": agg["format_fail"], "missing": agg["missing"],
        "accuracy_answerable": pct(agg["correct"], n_ans),
        "accuracy_overall": pct(agg["correct"] + agg["correct_abstain"], n),
        "citation_valid_among_correct": [cit_valid, cit_total, pct(cit_valid, cit_total)],
        "nonexistent_citation_rows": nonexistent_rows,
        "nonexistent_citation_ids": nonexistent_ids,
        "latency": lat_stats(secs),
        "by_klass": {k: dict(v) for k, v in by_klass.items() if k != "ALL"},
        "per_question": per_q,
    }

def score_b2(rows, outputs, diag, downgrade=True):
    per_q = {}
    agg = Counter()
    secs = []
    cit_valid = cit_total = 0
    nonexistent_rows = nonexistent_ids = 0
    for row in rows:
        qid = row["qid"]
        rec = outputs.get(qid)
        dg = diag[qid]
        if rec is None or "raw" not in rec:
            outcome = "missing"
        else:
            if rec.get("secs") is not None:
                secs.append(rec["secs"])
            a = parse_output(rec["raw"])
            if a is None:
                outcome = "format_fail"
            else:
                bad = [c for c in a["citations"] if c not in dg["claims"]]
                if bad:
                    nonexistent_rows += 1
                    nonexistent_ids += len(bad)
                outcome = classify_b2(row, a, diag, downgrade=downgrade)
                if outcome == "correct":
                    cit_total += 1
                    carriers = {cid for cid, cl in dg["claims"].items()
                                if carries(cl["text"], a["answer"])}
                    if set(a["citations"]) == carriers:
                        cit_valid += 1
        per_q[qid] = outcome
        agg[outcome] += 1
    n = len(rows)
    n_ans = sum(1 for r in rows if r["klass"] != "H")
    return {
        "n": n, "n_answerable": n_ans, "n_abstain_gold": n - n_ans,
        "correct": agg["correct"], "wrong_answer": agg["wrong_answer"],
        "over_abstain": agg["over_abstain"], "correct_abstain": agg["correct_abstain"],
        "format_fail": agg["format_fail"], "missing": agg["missing"],
        "accuracy_answerable": pct(agg["correct"], n_ans),
        "accuracy_overall": pct(agg["correct"] + agg["correct_abstain"], n),
        "citation_valid_among_correct": [cit_valid, cit_total, pct(cit_valid, cit_total)],
        "nonexistent_citation_rows": nonexistent_rows,
        "nonexistent_citation_ids": nonexistent_ids,
        "latency": lat_stats(secs),
        "per_question": per_q,
    }

def main():
    holdout_rows = [json.loads(l) for l in open(HOLDOUT, encoding="utf-8")]
    b2_rows = [json.loads(l) for l in open(f"{EXP}/b2_rows.jsonl", encoding="utf-8")]
    diag = load_diag()

    new_out = {}
    out_path = os.path.join(HERE, "baseline_outputs.jsonl")
    if os.path.exists(out_path):
        for line in open(out_path, encoding="utf-8"):
            line = line.strip()
            if line:
                r = json.loads(line)
                new_out.setdefault((r["set"], r["arm"]), {})[r["qid"]] = r
    old_out = {}
    for line in open(f"{EXP}/results.jsonl", encoding="utf-8"):
        r = json.loads(line)
        if r.get("exp") == "B2" and r.get("arm") == "BASE":
            old_out[r["qid"]] = r

    scores = {}
    scores["holdout_A"] = score_holdout(holdout_rows, new_out.get(("holdout", "A"), {}))
    scores["holdout_B"] = score_holdout(holdout_rows, new_out.get(("holdout", "B"), {}))
    scores["b2_A_rescored"] = score_b2(b2_rows, old_out, diag, downgrade=True)
    scores["b2_A_v1caliber_crosscheck"] = score_b2(b2_rows, old_out, diag, downgrade=False)
    scores["b2_B"] = score_b2(b2_rows, new_out.get(("b2", "B"), {}), diag, downgrade=True)

    with open(os.path.join(HERE, "scores.json"), "w", encoding="utf-8") as fh:
        json.dump(scores, fh, ensure_ascii=False, indent=1)
    for name, s in scores.items():
        print(f"{name}: n={s['n']} correct={s['correct']} wrong={s['wrong_answer']} "
              f"over_abstain={s['over_abstain']} correct_abstain={s['correct_abstain']} "
              f"format_fail={s['format_fail']} missing={s['missing']} "
              f"acc_ans={s['accuracy_answerable']} acc_all={s['accuracy_overall']} "
              f"cit_valid={s['citation_valid_among_correct']} nonexist_rows={s['nonexistent_citation_rows']} "
              f"lat={s['latency']}")

if __name__ == "__main__":
    main()
