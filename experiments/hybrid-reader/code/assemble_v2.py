#!/usr/bin/env python3
"""Assembly v2 (V6.2): reader outputs -> deterministic layer -> frozen
scorer, with the V6.2 safety changes:

  - Binding refusals (stage 1A): on a compute-routed question, a
    completeness-class refusal (insufficient_operands / no_valid_evidence)
    is BINDING -- the final output is an abstention, never the reader's
    guessed value. A set whose completeness cannot be proved must not be
    computed over, and must not be guessed over either.
  - Constrained operand completion (stage 2): the v2 (person, scope)
    series completion gains predicate consistency, same-event dedupe,
    explicit time-range restriction, and a per-record audit receipt.
    Completion keys come ONLY from the reader's own citations and the
    question's context records -- never from gold or spec files.
  - Citation-support guards (stage 1B/1A backstops), all forward checks
    on the reader's own citations (NOT the banned reverse number lookup):
      G2 validity-citation: an answer citing only validity/expiry
         records cannot state the value those records refer to -> abstain.
      G3 hop-owner: on a role-indirect question ('the person who serves
         as X in ORG'), if the role record uniquely resolves to person P
         and every cited record is extractably about someone else, the
         answer is unsupported -> abstain.
      G4 attribute: if the asked attribute class (kept-location /
         buy-place / price) is determinable and every cited record states
         a different, determinable attribute class -> abstain.
    Guards only ever convert an answer into an abstention, and only when
    their preconditions are fully determinable; otherwise they no-op.
  - Every assembly log entry carries routing / completion / guard fields
    (fixes the run-1 provenance gap).
"""
import argparse
import csv
from datetime import date
import importlib.util
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
HP = os.path.dirname(HERE)
RT = os.path.expanduser("~/workspace/reader-train")
sys.path.insert(0, HERE)
sys.path.insert(0, HP)

import det_module
from prefilter_v2 import candidate_persons, person_of
from records import parse_context, parse_question_date, record_date

spec = importlib.util.spec_from_file_location(
    "frozen_scorer", os.path.join(RT, "baseline", "scorer.py"))
scorer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scorer)

GOOD = ("correct", "correct_abstain")
_VALIDITY_PREDICATES = ("valid until", "有效期")


def load_outputs(path):
    out = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line:
            r = json.loads(line)
            out[r["qid"]] = r
    return out


def _abstain_output(rec):
    final = {"answer": "", "citations": [], "abstain": True}
    return {"qid": rec["qid"], "arm": rec.get("arm"),
            "raw": json.dumps(final, ensure_ascii=False),
            "secs": rec.get("secs")}


# ------------------------------------------------------- completion v2
def complete_operands_v2(route, cited, all_records, question):
    """Constrained series completion. Returns (records, info); info is
    None when no completion was applied, or a dict with the audit receipt
    (added records with association reasons, deduped records, blocked
    reason). Keys derive only from the reader's citations + context."""
    if route not in ("count", "sum", "nth_date") or not cited:
        return cited, None
    cands = candidate_persons(all_records)
    keys = {(person_of(r.get("value"), cands, r.get("scope")),
             r.get("scope"), r.get("predicate")) for r in cited}
    if len(keys) != 1:
        return cited, {"blocked": "cited records do not share one "
                                   "(person, scope, predicate) key"}
    (person, scope, pred), = keys
    if person is None:
        return cited, {"blocked": "series person unresolved"}
    pool = [r for r in all_records
            if r.get("scope") == scope and r.get("predicate") == pred
            and person_of(r.get("value"), cands, r.get("scope")) == person]
    # explicit time range stated in the question restricts the series
    ym = re.search(r"(20\d{2})\s*年", question or "") or \
        re.search(r"\bin\s+(20\d{2})\b", question or "")
    year = int(ym.group(1)) if ym else None
    if year is not None:
        pool = [r for r in pool
                if (det_module.event_date(r)[0] or
                    record_date(r) or date(1, 1, 1)).year == year]
    if route == "sum":
        pool = [r for r in pool if det_module._quantity(r) is not None]
    else:
        pool = [r for r in pool
                if det_module.event_date(r)[1] == "event_date"]
    # same-event dedupe: duplicate statements of one event count once
    seen, deduped, uniq = set(), [], []
    for r in pool:
        if route == "sum":
            sig = (det_module.event_date(r)[0] or record_date(r),
                   det_module._quantity(r))
        else:
            sig = det_module.event_date(r)[0]
        if sig in seen:
            deduped.append(r["id"])
            continue
        seen.add(sig)
        uniq.append(r)
    pool = uniq
    if len(pool) <= len(cited):
        return cited, None
    cited_ids = {r["id"] for r in cited}
    reason = (f"series match: person={person} scope={scope} "
              f"predicate={pred}" + (f" year={year}" if year else ""))
    info = {"key": [person, scope, pred], "n_cited": len(cited),
            "n_completed": len(pool),
            "added": [{"id": r["id"], "reason": reason}
                      for r in pool if r["id"] not in cited_ids],
            "deduped": deduped}
    return pool, info


# ------------------------------------------------------------- guards
def guard_validity_citation(a, cited_records, question):
    """G2: answer rests only on validity/expiry records -> cannot state
    the referred value. Skips questions that ask about validity itself
    and countdown-routed flow (handled before guards)."""
    if not cited_records:
        return None
    if re.search(r"到期|过期|失效|expire|expiry|valid until|有效期",
                 question or ""):
        return None
    if all((r.get("predicate") or "") in _VALIDITY_PREDICATES
           for r in cited_records):
        return "answer cites only validity/expiry records, which do not " \
               "state the asked value"
    return None


_HOP_PATTERNS = [
    re.compile(r"(.+?)里担任(.+?)的那位"),
    re.compile(r"(.+?)里负责(.+?)的那位"),
    re.compile(r"the person who serves as (.+?) in (.+?)(?:\s+—|\s+-|,|\?)"),
    re.compile(r"the person who is the (.+?) of (.+?)(?:\s+—|\s+-|,|\?)"),
]


def _resolve_hop_person(question, all_records, cands):
    """Resolve a role-indirect question to a person via the role record
    in context. Returns the person or None (unresolvable/ambiguous)."""
    q = question or ""
    org = role = None
    for pat in _HOP_PATTERNS:
        m = pat.search(q)
        if m:
            if pat in _HOP_PATTERNS[:2]:
                org, role = m.group(1), m.group(2)
            else:
                role, org = m.group(1), m.group(2)
            break
    if org is None:
        return None
    persons = set()
    for r in all_records:
        v = r.get("value") or ""
        if org in v and role in v:
            p = person_of(v, cands, r.get("scope"))
            if p:
                persons.add(p)
    if len(persons) == 1:
        return persons.pop()
    return None


def _guard_person_view(cands):
    """Candidate view for guard comparisons: drop tokens that are strict
    superstrings of another candidate -- those are glued extractions of
    the shorter name ('韩越购入' harvested from a purchase record vs the
    clean '韩越'), not distinct persons."""
    return {c for c in cands
            if not any(o != c and c.startswith(o) for o in cands)}


def _same_person(cp, p, view):
    if cp is None or p is None:
        return False
    if cp == p:
        return True
    # glued token of the same name (name + verb chars), unless a longer
    # distinct candidate matches the token better
    if cp.startswith(p):
        return not any(c != p and cp.startswith(c) and len(c) > len(p)
                       for c in view)
    return False


def guard_hop_owner(a, cited_records, question, all_records, cands):
    """G3: role-indirect question resolved to P, but every cited record
    is extractably about someone other than P -> unsupported answer."""
    if not cited_records:
        return None
    p = _resolve_hop_person(question, all_records, cands)
    if p is None:
        return None
    view = _guard_person_view(cands)
    cited_persons = []
    for r in cited_records:
        cp = person_of(r.get("value"), cands, r.get("scope"))
        if cp is None:
            return None  # unextractable citation -> conservative no-op
        cited_persons.append(cp)
    if cited_persons and not any(_same_person(cp, p, view)
                                 for cp in cited_persons):
        return (f"question resolves to {p} via the role record, but the "
                f"cited records are about {sorted(set(cited_persons))}")
    return None


_ASKED_PATTERNS = [
    ("price", re.compile(r"多少钱|价格是多少|花了多少|how much did .* "
                         r"(?:pay|cost)|what is the price")),
    ("buyplace", re.compile(r"在哪里买|在哪儿买|哪里购入|在哪里购|"
                            r"where did .* buy|where .* bought")),
    ("loc_item", re.compile(r"搁在哪儿|放在哪儿|收在哪儿|放哪儿了|放在哪|"
                            r"where does .* keep|where .* stored|"
                            r"where .* keep .*")),
]
_PRED_CLASS = [
    ("price", ("价格", "price paid", "price")),
    ("buyplace", ("购买地点", "bought at", "buy place")),
    ("loc_item", ("存放地点", "kept at", "stored at", "kept in")),
]


def _pred_class(pred):
    pred = pred or ""
    for cls, names in _PRED_CLASS:
        if pred in names:
            return cls
    return None


def guard_attribute(a, cited_records, question):
    """G4: asked attribute class determinable and every cited record
    states a different determinable attribute class -> the citation does
    not support the asked attribute."""
    if not cited_records:
        return None
    asked = None
    for cls, pat in _ASKED_PATTERNS:
        if pat.search(question or ""):
            asked = cls
            break
    if asked is None:
        return None
    classes = [_pred_class(r.get("predicate")) for r in cited_records]
    if any(c is None for c in classes):
        return None
    if all(c != asked for c in classes):
        return (f"question asks {asked}, but cited records state "
                f"{sorted(set(classes))}")
    return None


_CODE_QUESTION = re.compile(
    r"编号|号码|证件号|what is the (?:current )?number|"
    r"what .* (?:number|code) (?:of|is)|membership number|pass number",
    re.IGNORECASE)
_CODE_TOKEN = re.compile(r"[A-Z]{1,4}-\d{3,}")


def identifier_repair(a, cited_records, question, all_records, cands):
    """G5 (deterministic repair, not a vote): the question asks for an
    identifier/code, the reader answered with a bare integer (a
    count-shaped string, never an identifier), and the records the
    reader itself cited contain exactly one identifier token, carried
    by a record about the person named in the question. Replace the
    answer with that identifier and log a receipt. If the cited
    evidence carries identifiers but they are ambiguous or belong to
    someone else, the bare-integer answer is unsupported -> abstain
    reason. Otherwise no-op."""
    if not _CODE_QUESTION.search(question or ""):
        return None
    if not re.fullmatch(r"\d+", (a.get("answer") or "").strip()):
        return None
    found = {}
    for r in cited_records:
        for tok in _CODE_TOKEN.findall(r.get("value") or ""):
            found.setdefault(tok, r)
    if not found:
        return None
    named = [c for c in _guard_person_view(cands) if c in (question or "")]
    if len(found) == 1 and len(named) == 1:
        tok, rec = next(iter(found.items()))
        view = _guard_person_view(cands)
        owner = person_of(rec.get("value"), cands, rec.get("scope"))
        if _same_person(owner, named[0], view):
            return {"replace": tok, "from_record": rec["id"],
                    "reason": (f"reader answered a bare integer; the cited "
                               f"record {rec['id']} states exactly one "
                               f"identifier ({tok}) for the person named "
                               f"in the question")}
    return {"abstain": (f"reader answered a bare integer to an "
                        f"identifier question; cited records carry "
                        f"identifier(s) {sorted(found)} that do not "
                        f"uniquely match the named person")}


_PAST_MARKERS = re.compile(
    r"当时|那时|去年|上个月|曾经|起初|最初|at that time|back then|"
    r"originally|used to")


def guard_stale_citation(a, cited_records, question, all_records, cands,
                         qdate):
    """G6: current-state question; the reader cited a fact record while
    a later record with the same (person, scope, predicate) key exists
    in context and was NOT cited -> the citation is provably stale.
    Converts to abstention only; never substitutes the later value
    (no automatic supersession by date). Past-state questions and
    validity/correction records are out of scope."""
    q = question or ""
    if _PAST_MARKERS.search(q):
        return None
    if not cited_records:
        return None
    cited_ids = {r["id"] for r in cited_records}
    view = _guard_person_view(cands)
    for r in cited_records:
        if (r.get("predicate") or "") in _VALIDITY_PREDICATES:
            continue
        p = person_of(r.get("value"), cands, r.get("scope"))
        if p is None:
            continue
        key = (p, r.get("scope"), r.get("predicate"))
        sibs = []
        for s in all_records:
            if (s.get("predicate") or "") in _VALIDITY_PREDICATES:
                continue
            sp = person_of(s.get("value"), cands, s.get("scope"))
            if sp is not None and _same_person(sp, p, view) and \
                    (sp, s.get("scope"), s.get("predicate"))[1:] == key[1:]:
                sibs.append(s)
        dated = [(record_date(s), s) for s in sibs]
        dated = [(d, s) for d, s in dated if d is not None]
        if not dated:
            continue
        dmax, latest = max(dated, key=lambda t: t[0])
        rd = record_date(r)
        if rd is not None and dmax > rd and latest["id"] not in cited_ids \
                and (qdate is None or dmax <= qdate):
            # corroboration escape: if the reader's answer also appears
            # in the latest record's value, the newest evidence supports
            # the answer despite the stale citation -> do not convert.
            ans = (a.get("answer") or "").strip()
            if ans and ans in (latest.get("value") or ""):
                continue
            return (f"cited record {r['id']} ({rd}) is stale: a later "
                    f"same-key record {latest['id']} ({dmax}) exists and "
                    f"was not cited")
    return None


def guard_direct_owner(a, cited_records, question, all_records, cands):
    """G3b: the question directly names person P; P has a record with
    the same (scope, predicate) as a cited record, yet every cited
    record is extractably about someone other than P -> the answer
    rests on another person's record about the same item."""
    if not cited_records:
        return None
    view = _guard_person_view(cands)
    named = [c for c in view if c in (question or "")]
    if len(named) != 1:
        return None
    p = named[0]
    cited_persons = []
    for r in cited_records:
        cp = person_of(r.get("value"), cands, r.get("scope"))
        if cp is None:
            return None
        cited_persons.append(cp)
    if any(_same_person(cp, p, view) for cp in cited_persons):
        return None
    own_keys = {(r.get("scope"), r.get("predicate")) for r in all_records
                if _same_person(person_of(r.get("value"), cands,
                                          r.get("scope")), p, view)}
    cited_keys = {(r.get("scope"), r.get("predicate"))
                  for r in cited_records}
    if own_keys & cited_keys:
        # corroboration escape: the reader's answer appears in one of
        # P's own records -> the evidence exists (the citation pointer
        # is wrong, the answer is not unsupported) -> do not convert.
        ans = (a.get("answer") or "").strip()
        if ans and any(ans in (r.get("value") or "") for r in all_records
                       if _same_person(person_of(r.get("value"), cands,
                                                 r.get("scope")), p, view)
                       or p in (r.get("value") or "")):
            return None
        return (f"question names {p}, who has same-key records in "
                f"context, but the cited records are about "
                f"{sorted(set(cited_persons))}")
    return None


# ------------------------------------------------------------ assembly
def assemble_v2(reader_outputs, rows, routing="pattern_first",
                complete=True, bind="completeness",
                guards=("validity", "hop_owner", "direct_owner", "attribute", "stale", "identifier")):
    assembled, log, det_secs = {}, [], []
    for row in rows:
        qid = row["qid"]
        rec = reader_outputs.get(qid)
        entry = {"qid": qid, "route": None, "route_source": None,
                 "compute_status": None, "reason_code": None,
                 "receipt": None, "reader_qtype": None,
                 "operands_completed": None, "bound_refusal": False,
                 "guard": None, "repair": None}
        if rec is None:
            log.append(entry)
            continue
        a = scorer.parse_output(rec.get("raw"))
        if a is None or a["abstain"]:
            assembled[qid] = rec
            entry["compute_status"] = "not_applicable"
            log.append(entry)
            continue
        entry["reader_qtype"] = a.get("qtype")
        reader_qtype = a.get("qtype") if a.get("qtype") in det_module.COMPUTE_TYPES else None
        pattern_qtype, _ = det_module.classify_question(row["question"])
        if routing == "pattern_first" and pattern_qtype != "lookup":
            route, source = pattern_qtype, "pattern"
        elif reader_qtype:
            route, source = reader_qtype, "reader"
        elif pattern_qtype != "lookup":
            route, source = pattern_qtype, "pattern_fallback"
        else:
            route, source = None, None
        entry["route"], entry["route_source"] = route, source

        _, pairs = parse_context(row["context"])
        all_records = [r for _, r in pairs]
        by_id = {r["id"]: r for r in all_records}
        cited = [by_id[c] for c in a["citations"] if c in by_id]
        final = None

        if route is not None:
            compute_records = cited
            if complete:
                compute_records, comp = complete_operands_v2(
                    route, cited, all_records, row["question"])
                entry["operands_completed"] = comp
            qdate = parse_question_date(row["question_date"])
            t0 = time.perf_counter()
            res = det_module.compute(route, row["question"],
                                     compute_records, qdate)
            det_secs.append(time.perf_counter() - t0)
            entry["compute_status"] = res["status"]
            if res["status"] == "ok":
                entry["receipt"] = res["receipt"]
                ans = {"answer": res["answer"],
                       "citations": a["citations"], "abstain": False}
                final = {"qid": qid, "arm": rec.get("arm"),
                         "raw": json.dumps(ans, ensure_ascii=False),
                         "secs": rec.get("secs")}
            else:
                entry["reason_code"] = res["reason_code"]
                binding = (bind == "all" or
                           (bind == "completeness" and
                            res["reason_code"] in
                            ("insufficient_operands", "no_valid_evidence")))
                if binding:
                    entry["bound_refusal"] = True
                    final = _abstain_output(rec)
        if final is None:
            # reader answer stands unless a repair/guard converts it
            cands = candidate_persons(all_records)
            if "identifier" in guards:
                rep = identifier_repair(a, cited, row["question"],
                                        all_records, cands)
                if rep and "replace" in rep:
                    entry["repair"] = {"name": "identifier_extraction",
                                       **rep}
                    ans = {"answer": rep["replace"],
                           "citations": a["citations"], "abstain": False}
                    final = {"qid": qid, "arm": rec.get("arm"),
                             "raw": json.dumps(ans, ensure_ascii=False),
                             "secs": rec.get("secs")}
                elif rep and "abstain" in rep:
                    entry["guard"] = {"name": "identifier",
                                      "reason": rep["abstain"]}
                    final = _abstain_output(rec)
            if final is None:
                qdate2 = parse_question_date(row["question_date"])
                checks = []
                if "validity" in guards:
                    checks.append(("validity_citation",
                                   guard_validity_citation(
                                       a, cited, row["question"])))
                if "hop_owner" in guards:
                    checks.append(("hop_owner", guard_hop_owner(
                        a, cited, row["question"], all_records, cands)))
                if "direct_owner" in guards:
                    checks.append(("direct_owner", guard_direct_owner(
                        a, cited, row["question"], all_records, cands)))
                if "attribute" in guards:
                    checks.append(("attribute", guard_attribute(
                        a, cited, row["question"])))
                if "stale" in guards:
                    checks.append(("stale_citation", guard_stale_citation(
                        a, cited, row["question"], all_records, cands,
                        qdate2)))
                for name, reason in checks:
                    if reason:
                        entry["guard"] = {"name": name, "reason": reason}
                        final = _abstain_output(rec)
                        break
            if final is None:
                final = rec
        assembled[qid] = final
        log.append(entry)
    return assembled, log, det_secs


def score_arm(gold_rows, outputs):
    return scorer.score_holdout(gold_rows, outputs)


def abstain_diag(gold_rows, per_q):
    """REPORTING ONLY: reads eval2 specs to break abstentions down by
    missing_type. Never feeds the pipeline (no runtime use of gold/spec
    keys anywhere in assemble_v2)."""
    specs = {json.loads(l)["qid"]: json.loads(l)
             for l in open(os.path.join(RT, "eval2", "eval2_specs.jsonl"),
                           encoding="utf-8")}
    by_mt = defaultdict(Counter)
    for g in gold_rows:
        if g["gold_abstain"]:
            by_mt[specs[g["qid"]].get("missing_type")][per_q[g["qid"]]] += 1
    return {mt: dict(c) for mt, c in by_mt.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reader-outputs", required=True)
    ap.add_argument("--reader-inputs", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--routing", default="pattern_first",
                    choices=("reader_first", "pattern_first"))
    ap.add_argument("--bind", default="completeness",
                    choices=("none", "completeness", "all"))
    ap.add_argument("--guards", default="validity,hop_owner,direct_owner,attribute,stale,identifier")
    ap.add_argument("--complete", default="v2", choices=("v2", "off"))
    args = ap.parse_args()

    gold_rows = [json.loads(l) for l in open(
        os.path.join(RT, "eval2", "eval2.jsonl"), encoding="utf-8")]
    rows = [json.loads(l) for l in open(args.reader_inputs, encoding="utf-8")]
    reader_outputs = load_outputs(args.reader_outputs)
    baseline_outputs = load_outputs(os.path.join(
        RT, "eval2-baselines", "kaggle-output-c1", "c1_outputs.jsonl"))
    guards = tuple(g for g in args.guards.split(",") if g)

    assembled, log, det_secs = assemble_v2(
        reader_outputs, rows, routing=args.routing,
        complete=(args.complete == "v2"), bind=args.bind, guards=guards)
    hyb = score_arm(gold_rows, assembled)
    base = score_arm(gold_rows, baseline_outputs)

    log_by_qid = {e["qid"]: e for e in log}
    flips = []
    for g in gold_rows:
        qid = g["qid"]
        b, h = base["per_question"][qid], hyb["per_question"][qid]
        direction = 0
        if b not in GOOD and h in GOOD:
            direction = 1
        elif b in GOOD and h not in GOOD:
            direction = -1
        e = log_by_qid.get(qid, {})
        flips.append({"qid": qid, "klass": g["klass"], "sub": g.get("sub"),
                      "baseline_outcome": b, "hybrid_outcome": h,
                      "direction": direction, "route": e.get("route"),
                      "route_source": e.get("route_source"),
                      "compute_status": e.get("compute_status"),
                      "reason_code": e.get("reason_code"),
                      "bound_refusal": e.get("bound_refusal"),
                      "guard": (e.get("guard") or {}).get("name")})
    with open(os.path.join(HERE, f"flips_{args.tag}.csv"), "w",
              encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(flips[0].keys()))
        w.writeheader()
        w.writerows(flips)

    det_lat = scorer.lat_stats([s * 1000 for s in det_secs])
    result = {
        "tag": args.tag, "routing": args.routing, "bind": args.bind,
        "guards": list(guards), "complete": args.complete,
        "baseline": {k: base[k] for k in
                     ("correct", "wrong_answer", "over_abstain",
                      "correct_abstain", "format_fail",
                      "citation_valid_among_correct", "latency", "by_klass")},
        "hybrid": {k: hyb[k] for k in
                   ("correct", "wrong_answer", "over_abstain",
                    "correct_abstain", "format_fail",
                    "citation_valid_among_correct", "latency", "by_klass")},
        "hybrid_abstain_by_missing_type": abstain_diag(gold_rows, hyb["per_question"]),
        "baseline_abstain_by_missing_type": abstain_diag(gold_rows, base["per_question"]),
        "flips": {"positive": sum(1 for f in flips if f["direction"] == 1),
                  "negative": sum(1 for f in flips if f["direction"] == -1)},
        "det_module_latency_ms": det_lat,
        "assembly_log": log,
    }
    with open(os.path.join(HERE, f"ab_result_{args.tag}.json"), "w",
              encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
    print(f"[{args.tag}] baseline correct={base['correct']} wrong={base['wrong_answer']} "
          f"over={base['over_abstain']} ca={base['correct_abstain']}")
    print(f"[{args.tag}] hybrid   correct={hyb['correct']} wrong={hyb['wrong_answer']} "
          f"over={hyb['over_abstain']} ca={hyb['correct_abstain']} "
          f"flips +{result['flips']['positive']} / -{result['flips']['negative']}")
    print(f"[{args.tag}] det latency ms: {det_lat}")


if __name__ == "__main__":
    main()
