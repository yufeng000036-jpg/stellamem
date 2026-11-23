"""Run the public research package in a fresh directory. Python 3.10+ and Git only."""

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

from public_dates import read, turns

HERE = Path(__file__).resolve().parent
CANDIDATE = "9e554da2e40084f7ad0a1de55a6a053469cc6352"
DATA_REV = "3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376"
MAIN = "eaf21965ed5333b539f5ef5f968b785a029174bb"


def git(folder, *args):
    return subprocess.check_output(["git", "-C", str(folder), *args])


def snapshot(folder, url, revision, paths):
    folder.mkdir(exist_ok=True)
    if not (folder / ".git").exists():
        git(folder, "init", "-q")
        git(folder, "remote", "add", "origin", url)
    git(folder, "fetch", "-q", "--depth", "1", "origin", revision)
    filenames = (
        git(folder, "ls-tree", "-r", "--name-only", revision, "--", *paths).decode().splitlines()
    )
    if not filenames:
        raise ValueError("empty snapshot")
    for name in filenames:
        dest = folder / name
        dest.parent.mkdir(exist_ok=True, parents=True)
        dest.write_bytes(git(folder, "cat-file", "blob", revision + ":" + name))


def curves(data, results):
    rows = [
        json.loads(line)
        for line in (results / "test.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    histories = {
        h["sample_id"]: {r[0]: r for r in turns(h["conversation"])}
        for h in json.loads(data.read_bytes())
    }
    out = {}
    for k in (1, 3, 5):
        counts = Counter()
        for row in rows:
            ev = [histories[row["history"]][i] for i in row["arms"]["D"]["slate"][:k]]
            ans, cite = read(ev, True)
            counts["supported"] += ans == row["gold"] and cite in row["gold_ids"]
            counts["exact"] += ans == row["gold"]
            counts["wrong"] += ans is not None and ans != row["gold"]
            counts["refused"] += ans is None
            counts["words"] += sum(min(96, len(t[2].split())) for t in ev)
        out[str(k)] = dict(counts)
    (results / "posthoc_k.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("new_directory", type=Path, help="new, unused path; no overwrites")
    args = p.parse_args()
    root = args.new_directory.resolve()
    root.mkdir(parents=True, exist_ok=False)
    source = root / "source"
    snapshot(
        source,
        "https://github.com/yufeng000036-jpg/stellamem.git",
        CANDIDATE,
        ["experiments/hybrid-reader"],
    )
    archive = source / "benchmark/longmemeval"
    snapshot(
        source,
        "https://github.com/yufeng000036-jpg/stellamem.git",
        MAIN,
        [
            "benchmark/longmemeval/results-20261108-summary.json",
            "benchmark/longmemeval/tracking_table_20261108.csv",
        ],
    )
    corpus = root / "locomo"
    snapshot(
        corpus,
        "https://github.com/snap-research/locomo.git",
        DATA_REV,
        ["data/locomo10.json", "LICENSE.txt"],
    )
    data = corpus / "data/locomo10.json"
    if (
        hashlib.sha256(data.read_bytes()).hexdigest()
        != "79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4"
    ):
        raise ValueError("LoCoMo file hash mismatch")
    results = root / "results"
    package = source / "experiments/hybrid-reader"

    def run(script, *params):
        subprocess.run([sys.executable, str(HERE / script), *map(str, params)], check=True)

    run("audit.py", package, results / "audit.json")
    run("archive500.py", archive, package, results / "archive500.json")
    for split in ("development", "validation", "test"):
        run("public_dates.py", data, results, "--split", split)
    run("independent_check.py", data, results)
    curves(data, results)
    for name in (
        "audit.json",
        "archive500.json",
        "development.jsonl",
        "validation.jsonl",
        "test.jsonl",
        "posthoc_k.json",
    ):
        original = (
            json.loads((HERE / "results" / name).read_bytes())
            if name.endswith(".json")
            else [json.loads(s) for s in (HERE / "results" / name).read_bytes().splitlines()]
        )
        replay = (
            json.loads((results / name).read_bytes())
            if name.endswith(".json")
            else [json.loads(s) for s in (results / name).read_bytes().splitlines()]
        )
        if original != replay:
            raise RuntimeError("mismatch with published results: " + name)
    print("Public reproduction matches archived records:", results)


if __name__ == "__main__":
    main()
