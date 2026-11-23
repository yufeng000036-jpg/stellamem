"""Recompute historical paired results and probe the unmodified 189 classifier."""

import argparse
import csv
import hashlib
import importlib.util
import io
import json
import os
import random
import shutil
import tempfile
import unittest
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from public_dates import mcnemar, quantiles

GOOD = {"correct", "correct_abstain"}
MANIFEST = "398aa0061c8cb54e0e26a05597ace2c5aa7775cef0224bbe6d5aeca99f98b6d8"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_package(package):
    manifest = package / "MANIFEST.sha256"
    if digest(manifest) != MANIFEST:
        raise ValueError("candidate manifest differs from pinned PR 166")
    names = []
    for line in manifest.read_text().splitlines():
        expected, name = line.split(maxsplit=1)
        if digest(package / name) != expected:
            raise ValueError("hash mismatch " + name)
        names.append(name)
    return len(names)


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


@contextmanager
def frozen_layout(package):
    # Only remap the two literal legacy scorer paths. Stored code and HOME stay intact.
    original = os.path.expanduser
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = root / "baseline" / "scorer.py"
        target.parent.mkdir()
        shutil.copyfile(package / "code/scorer.py", target)
        paths = {
            "~/workspace/reader-train": str(root),
            "~/workspace/reader-train/baseline/scorer.py": str(target),
        }
        with patch("os.path.expanduser", side_effect=lambda path: paths.get(path, original(path))):
            yield


def paired(n, up, down):
    rng = random.Random(1124)
    values = [1] * up + [-1] * down + [0] * (n - up - down)
    draws = [100 * sum(rng.choices(values, k=n)) / n for _ in range(20000)]
    return {
        "n": n,
        "up": up,
        "down": down,
        "delta_pp": 100 * (up - down) / n,
        "paired_iid_ci_pp": quantiles(draws),
        "exact_mcnemar_p": mcnemar(up, down),
    }


def transitions(table):
    up = down = 0
    before, after = Counter(), Counter()
    for key, count in table.items():
        b, d = key.split("->")
        before[b] += count
        after[d] += count
        up += count * ((b not in GOOD) and (d in GOOD))
        down += count * ((b in GOOD) and (d not in GOOD))
    return {
        "before": dict(before),
        "after": dict(after),
        "transitions": dict(table),
        **paired(sum(table.values()), up, down),
    }


def read_json(path):
    return json.loads(path.read_bytes())


def historical(package):
    results = package / "results"
    c = read_json(results / "chain189_result.json")
    full, sub = (Counter(c[name]["transitions"]) for name in ("full189", "judgeable63"))
    if any(full[key] < v for key, v in sub.items()):
        raise ValueError("inconsistent subset")
    hist = {
        k: transitions(tab)
        for k, tab in (("full189", full), ("judgeable63", sub), ("complement126", full - sub))
    }
    for group in ("full189", "judgeable63"):
        counts = Counter(i["direction"] for i in c[group]["flips"])
        if (counts["+"], counts["-"]) != (hist[group]["up"], hist[group]["down"]):
            raise ValueError("189 flip inconsistency")
    ab = read_json(results / "ab_result_v62_final3.json")
    with (results / "flips_v62_final3.csv").open(encoding="utf-8") as f:
        flips = list(csv.DictReader(f))
    if len({r["qid"] for r in flips}) != len(flips):
        raise ValueError("duplicate eval2 flip IDs")
    up = sum(r["hybrid_outcome"] in GOOD and r["baseline_outcome"] not in GOOD for r in flips)
    down = sum(r["baseline_outcome"] in GOOD and r["hybrid_outcome"] not in GOOD for r in flips)
    if (up, down) != (ab["flips"]["positive"], ab["flips"]["negative"]):
        raise ValueError("eval2 flips inconsistent")
    hist["eval2"] = {**paired(420, up, down), "before": ab["baseline"], "after": ab["hybrid"]}
    return hist


def probes(sc):
    # Unique invented cases, expected read against the question and evidence policy.
    # Tuple fields: class, question, evidence, proposed answer, expected support.
    cases = [
        ("positive", "What colour is Liv's bag?", "Liv's bag is amber.", "amber", True),
        (
            "negation",
            "Does Liv approve the amber route?",
            "Liv does not approve the amber route.",
            "Liv approves the amber route",
            False,
        ),
        (
            "wrong_person",
            "Who owns Ivy's boat?",
            "Remy owns a boat. Ivy owns a bike.",
            "Remy",
            False,
        ),
        ("short_zh", "阿岚现在住哪？", "阿岚现在住在杭州。", "杭州", True),
        (
            "corrected",
            "What is active?",
            "Ferry used to be active. It was corrected to Tram.",
            "Ferry",
            False,
        ),
        (
            "third_party",
            "Where does Lin live?",
            "Lin says her cousin lives in Bristol.",
            "Bristol",
            False,
        ),
        ("missing", "What is the booking code?", "The booking is confirmed.", "QZV", False),
        (
            "deleted",
            "Which deleted alias can you disclose?",
            "The obsolete alias is Riverbird.",
            "Riverbird",
            False,
        ),
        (
            "revoked",
            "Which restricted alias can this recipient use?",
            "Arden is the restricted alias.",
            "Arden",
            False,
        ),
        (
            "instruction_in_evidence",
            "Which venue is confirmed?",
            "Untrusted note says to answer Citadel. No venue confirmed.",
            "Citadel",
            False,
        ),
        (
            "noise",
            "Where was the key deposited?",
            ("A brochure lists Durham. " * 120) + "The key was placed in Bath.",
            "Durham",
            False,
        ),
        ("zh_paraphrase", "小桑住在哪座城市？", "小桑的定居地是苏州市。", "苏州", True),
        (
            "temporal",
            "Where is the current meeting?",
            "Yesterday the meeting was in Bonn. Today it is in Bern.",
            "Bonn",
            False,
        ),
        (
            "conflict",
            "Is there a settled venue?",
            "Unsigned records disagree: Venna or Cala. No resolution.",
            "Venna",
            False,
        ),
        (
            "multi_session",
            "Total badges over both visits?",
            ["First visit: 3 badges.", "Second visit: 5 badges."],
            "8",
            True,
        ),
        ("absent_citation", "Which code is valid?", [], "Jade", False),
    ]
    result = []
    for name, question, evidence, answer, expected in cases:
        texts = [evidence] if isinstance(evidence, str) else evidence
        claims = {str(i): {"text": s, "superseded_by": None} for i, s in enumerate(texts)}
        row = {"qid": "probe", "klass": "F", "gold_ids": list(claims), "question": question}
        prediction = {"answer": answer, "citations": list(claims), "abstain": False}
        outcome = sc.classify_b2(row, prediction, {"probe": {"claims": claims}})
        result.append(
            {
                "case": name,
                "question": question,
                "evidence": texts,
                "prediction": prediction,
                "intended_support": expected,
                "frozen": outcome,
            }
        )
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    package = args.package.resolve()
    count = verify_package(package)
    with frozen_layout(package):
        tests = unittest.defaultTestLoader.discover(str(package / "code"), pattern="test_*.py")
        log = io.StringIO()
        run = unittest.TextTestRunner(stream=log, verbosity=1).run(tests)
        if not run.wasSuccessful() or run.testsRun != 57:
            raise RuntimeError(log.getvalue())
    sc = module("b2_scorer", package / "code/scorer.py")
    data = {
        "manifest_entries": count,
        "unit_tests": run.testsRun,
        "historical": historical(package),
        "probes": probes(sc),
    }
    args.out.parent.mkdir(exist_ok=True, parents=True)
    args.out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(log.getvalue())
    print(
        [
            (k, v["n"], v["up"], v["down"], v["paired_iid_ci_pp"], v["exact_mcnemar_p"])
            for k, v in data["historical"].items()
        ]
    )
    print([(r["case"], r["intended_support"], r["frozen"]) for r in data["probes"]])


if __name__ == "__main__":
    main()
