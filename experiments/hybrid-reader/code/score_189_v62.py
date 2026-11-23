#!/usr/bin/env python3
"""Score the V6.2 189 FULL CHAIN against the sealed C-arm 189 outputs,
same frozen B2 caliber (scorer.classify_b2) for both arms.

Chain arm: b2 reader outputs (prompt v2 on prefilter_v2-filtered
contexts) assembled by assemble_v2 with the frozen candidate config.
Baseline arm: train-run1 C outputs (set == 'b2'), raw.

Reports full-189 and judgeable-63 (labels_189.jsonl, label 材料内可判)
outcome transitions. Observational regression only.
Usage: python3 score_189_v62.py --reader-outputs kaggle-output-189/b2_v62_outputs.jsonl
"""

import argparse
import importlib.util
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
HP = os.path.dirname(HERE)
RT = os.path.expanduser("~/workspace/reader-train")
sys.path.insert(0, HERE)
sys.path.insert(0, HP)

spec = importlib.util.spec_from_file_location(
    "sc", os.path.join(RT, "baseline", "scorer.py")
)
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)

import assemble_v2  # noqa: E402

B2 = os.path.expanduser(
    "~/workspace/stellamem-takeover/v4-reader-exp-20261121/b2_rows.jsonl"
)
GOOD = ("correct", "correct_abstain")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reader-outputs", required=True)
    args = ap.parse_args()

    rows = [json.loads(line) for line in open(B2, encoding="utf-8")]
    inputs = [
        json.loads(line)
        for line in open(os.path.join(HERE, "b2_inputs_v62.jsonl"), encoding="utf-8")
    ]
    labels = {
        json.loads(line)["qid"]: json.loads(line)["label"]
        for line in open(
            os.path.join(RT, "regression-189", "labels_189.jsonl"), encoding="utf-8"
        )
    }
    diag = sc.load_diag()

    chain_out = {
        json.loads(line)["qid"]: json.loads(line)
        for line in open(args.reader_outputs, encoding="utf-8")
    }
    assert len(chain_out) == 189, len(chain_out)
    assembled, log, det_lat = assemble_v2.assemble_v2(
        chain_out,
        inputs,
        routing="pattern_first",
        complete=True,
        bind="completeness",
        guards=(
            "validity",
            "hop_owner",
            "direct_owner",
            "attribute",
            "stale",
            "identifier",
        ),
    )

    c_out = {}
    for line in open(
        os.path.join(RT, "train-run1", "c_outputs.jsonl"), encoding="utf-8"
    ):
        r = json.loads(line)
        if r.get("set") == "b2":
            c_out[r["qid"]] = r
    assert len(c_out) == 189, len(c_out)

    def outcomes(outputs, assembled_mode):
        res = {}
        for row in rows:
            qid = row["qid"]
            raw = outputs[qid]["raw"]
            a = sc.parse_output(raw)
            if a is None:
                res[qid] = "format_fail"
            else:
                res[qid] = sc.classify_b2(row, a, diag)
        return res

    base_o = outcomes(c_out, False)
    chain_o = outcomes(assembled, True)

    def summarize(sel, name):
        trans = Counter()
        good_b = good_c = 0
        flips = []
        for row in rows:
            qid = row["qid"]
            if sel and qid not in sel:
                continue
            b, c = base_o[qid], chain_o[qid]
            trans[(b, c)] += 1
            good_b += b in GOOD
            good_c += c in GOOD
            if (b in GOOD) != (c in GOOD):
                flips.append(
                    {
                        "qid": qid,
                        "klass": row["klass"],
                        "label": labels.get(qid),
                        "base": b,
                        "chain": c,
                        "direction": "+" if c in GOOD else "-",
                    }
                )
        net = sum(1 for f in flips if f["direction"] == "+") - sum(
            1 for f in flips if f["direction"] == "-"
        )
        print(
            f"[{name}] C good={good_b} chain good={good_c} "
            f"flips +{sum(1 for f in flips if f['direction'] == '+')} / "
            f"-{sum(1 for f in flips if f['direction'] == '-')} net {net:+d}"
        )
        return {
            "good_base": good_b,
            "good_chain": good_c,
            "transitions": {f"{k[0]}->{k[1]}": v for k, v in sorted(trans.items())},
            "flips": flips,
            "net": net,
        }

    judgeable = {q for q, lb in labels.items() if lb == "材料内可判"}
    result = {
        "note": "V6.2 189 full chain (prefilter_v2 -> prompt v2 reader -> "
        "det assembly, frozen candidate config) vs sealed C-arm "
        "outputs, frozen classify_b2 caliber. Observational only.",
        "full189": summarize(None, "full-189"),
        "judgeable63": summarize(judgeable, "judgeable-63"),
        "det_latency_ms": det_lat,
        "base_outcomes": Counter(base_o.values()),
        "chain_outcomes": Counter(chain_o.values()),
    }
    result["base_outcomes"] = dict(result["base_outcomes"])
    result["chain_outcomes"] = dict(result["chain_outcomes"])
    with open(os.path.join(HERE, "chain189_result.json"), "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
    print("wrote chain189_result.json")


if __name__ == "__main__":
    main()
