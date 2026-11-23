"""Offline crossed replay from frozen reader outputs. Only explicit research paths are read."""

import argparse
import json
from collections import Counter
from pathlib import Path

from audit import GOOD, digest, frozen_layout, module, verify_package


def lines(path):
    return [json.loads(s) for s in path.read_text(encoding="utf-8-sig").splitlines() if s.strip()]


def by_qid(rows):
    out = {r["qid"]: r for r in rows}
    if len(out) != len(rows):
        raise ValueError("duplicate question ID")
    return out


def compare(rows, inputs, labels, diag, old, new, assembler, scorer):
    sources = {"old": old, "v2": new}
    expected = set(by_qid(rows))
    if any(
        set(view) != expected for view in [by_qid(inputs), labels, old, new]
    ) or not expected <= set(diag):
        raise ValueError("question ID alignment failed")
    outputs, reasons = {}, {}
    for name, predictions in sources.items():
        outputs[name + "_raw"] = predictions
        for bind in ["none", "completeness"]:
            key = name + "_" + bind
            outputs[key], log, _ = assembler.assemble_v2(predictions, inputs, bind=bind)
            reasons[key] = by_qid(log)
    records = []
    for row in rows:
        qid = row["qid"]
        outcomes = {}
        for arm, predictions in outputs.items():
            parsed = scorer.parse_output(predictions[qid]["raw"])
            outcomes[arm] = (
                "format_fail" if parsed is None else scorer.classify_b2(row, parsed, diag)
            )
        records.append(
            {
                "qid": qid,
                "label": labels[qid]["label"],
                "outcomes": outcomes,
                "binding": {
                    k: {"bound": v[qid]["bound_refusal"], "reason": v[qid]["reason_code"]}
                    for k, v in reasons.items()
                },
            }
        )
    return records


def count_pairs(records, a, b):
    return Counter((r["outcomes"][a], r["outcomes"][b]) for r in records)


def check_archived(records, archive, group):
    table = {
        f"{a}->{b}": v for (a, b), v in count_pairs(records, "old_raw", "v2_completeness").items()
    }
    if table != archive[group]["transitions"]:
        raise ValueError(group + " archived transitions do not match")
    observed = {
        (r["qid"], r["outcomes"]["old_raw"], r["outcomes"]["v2_completeness"])
        for r in records
        if (r["outcomes"]["old_raw"] in GOOD) != (r["outcomes"]["v2_completeness"] in GOOD)
    }
    archived = {(r["qid"], r["base"], r["chain"]) for r in archive[group]["flips"]}
    if observed != archived:
        raise ValueError(group + " archived flip IDs do not match")


def main():
    p = argparse.ArgumentParser()
    for arg in ("package", "rows", "inputs", "old", "new", "labels", "diag_root", "out"):
        p.add_argument("--" + arg.replace("_", "-"), dest=arg, type=Path, required=True)
    a = p.parse_args()
    package = a.package.resolve()
    verify_package(package)
    known = {}
    for line in (package / "EXTERNAL-EVIDENCE.sha256").read_text().splitlines():
        if line and not line.startswith("#"):
            value, fname = line.split(maxsplit=1)
            known[Path(fname).name] = value
    identities = {}
    for key, name in (
        ("rows", "b2_rows.jsonl"),
        ("inputs", "b2_inputs_v62.jsonl"),
        ("old", "c_outputs.jsonl"),
        ("new", "b2_v62_outputs.jsonl"),
    ):
        identities[key] = digest(getattr(a, key))
        if identities[key] != known[name]:
            raise ValueError(key + " differs from V6.2 external manifest")
    identities["labels"] = digest(a.labels)
    files = []
    for sub in ("replay-stage2", "replay-topup2"):
        root = a.diag_root / sub
        files.extend([root / "manifest.json", *sorted((root / "diagnostics").glob("*.json"))])
    diagnostics_hashes = {str(f.relative_to(a.diag_root)): digest(f) for f in files}
    rows, inputs = lines(a.rows), lines(a.inputs)
    labels = by_qid(lines(a.labels))
    if len(rows) != 189 or sum(r["label"] == "材料内可判" for r in labels.values()) != 63:
        raise ValueError("cohort size differs from registered 189/63")
    with frozen_layout(package):
        assembler = module("assembler", package / "code/assemble_v2.py")
    sc = assembler.scorer
    sc.W = str(a.diag_root.resolve())
    diag = sc.load_diag()
    old = by_qid([r for r in lines(a.old) if r.get("set") == "b2"])
    new = by_qid(lines(a.new))
    records = compare(rows, inputs, labels, diag, old, new, assembler, sc)
    archive = json.loads((package / "results/chain189_result.json").read_bytes())
    groups = {"full189": records, "judgeable63": [r for r in records if r["label"] == "材料内可判"]}
    totals = {}
    for group, group_rows in groups.items():
        check_archived(group_rows, archive, group)
        totals[group] = {
            arm: dict(Counter(r["outcomes"][arm] for r in group_rows))
            for arm in records[0]["outcomes"]
        }
    # Exclusive write prevents overwriting another run or a supplied source file.
    with a.out.open("x", encoding="utf-8") as f:
        json.dump(
            {
                "file_sha256": identities,
                "diagnostic_sha256": diagnostics_hashes,
                "totals": totals,
                "rows": records,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(json.dumps(totals, indent=2))


if __name__ == "__main__":
    main()
