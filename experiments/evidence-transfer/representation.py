"""Exploratory RQ1 audit of bytes and explicit dialogue pointers in official representations."""

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from public_dates import turns


def encoded_bytes(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def pointers(value):
    # Published references may be one ID or a list. Count only resolvable explicit IDs.
    strings = value if isinstance(value, list) else [value]
    return {s for entry in strings for s in re.findall(r"\bD\d+:\d+\b", str(entry))}


def audit(data):
    totals = {}
    reasons = Counter()
    for history in data:
        source = history["conversation"]
        ids = {t[0] for t in turns(source)}
        bank = history["observation"]
        note_ids = set()
        for entries in bank.values():
            for items in entries.values():
                for _, references in items:
                    note_ids.update(pointers(references))
        summary = history["session_summary"]
        summary_ids = {cid for text in summary.values() for cid in pointers(text)}
        golds = []
        for qa in history["qa"]:
            if str(qa["category"]) == "5":
                reasons["adversarial"] += 1
                continue
            labels = set(qa.get("evidence") or [])
            if not labels or labels - ids:
                reasons["missing_or_unresolvable"] += 1
                continue
            golds.append(labels)
        representations = {
            "raw": (source, ids),
            "observations": (bank, note_ids),
            "summary": (summary, summary_ids),
            "raw_plus_observations": ([source, bank], ids | note_ids),
        }
        for name, (value, refs) in representations.items():
            row = totals.setdefault(name, Counter())
            row["bytes"] += encoded_bytes(value)
            row["valid_distinct_turn_pointers"] += len(refs & ids)
            row["invalid_distinct_pointers"] += len(refs - ids)
            row["source_turns"] += len(ids)
            row["eligible_questions"] += len(golds)
            row["questions_with_all_gold_pointers"] += sum(not (g - refs) for g in golds)
            row["questions_with_any_gold_pointer"] += sum(bool(g & refs) for g in golds)
    return {
        "scope": "Post hoc on static supplied LoCoMo representations, not storage engine size or semantic fact retention. Unknown/invalid QA pointers excluded for all arms.",
        "definitions": "Canonical UTF-8 compact JSON per representation per history. Explicit resolvable turn IDs, not session IDs, inferred alignments, or quote entailment. Raw+notes duplicates public material by construction.",
        "excluded": dict(reasons),
        "arms": totals,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("data", type=Path)
    p.add_argument("out", type=Path)
    a = p.parse_args()
    result = audit(json.loads(a.data.read_bytes()))
    a.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["arms"]))


if __name__ == "__main__":
    main()
