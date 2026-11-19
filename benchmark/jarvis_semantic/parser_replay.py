"""Offline counterfactual on complete #152 blind JSONL. Does not import Jarvis or load models."""

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re

BASE = "9b819bbd1a3a6fdbd79514615067e0e98ff92fb4"
ARMS = ("A", "R0", "R1")
PROMPTS = {
    "old": "014cb6757cc675d2b4ee48945e9f2d4126400a96c37c83d16a305e629af1282a",
    "new": "3d3c139de5cf1f78df3ce1b70bc353ccaab84f96e484f0a5b1e416282be09b37",
}
ID = re.compile(r"c-[0-9a-f]{24}")
WRAPPED = re.compile(r"\[(c-[0-9a-f]{24})\]")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def classify(raw, delivered):
    """Return a surface category, old selection and candidate selection. Never consult gold."""
    if raw is None:
        return "program_no_material", None, None
    text = raw.strip()
    if text == "NONE":
        return "model_none", None, None
    if text in delivered:
        return "bare_delivered", text, text
    wrapped = WRAPPED.fullmatch(text)
    if wrapped and wrapped[1] in delivered:
        return "bracket_delivered", None, wrapped[1]
    if ID.fullmatch(text) or wrapped:
        return "unoffered_id", None, None
    # Diagnostic only. An ID embedded in prose need not express a selection.
    return ("other_text_with_id" if ID.search(text) else "other_invalid"), None, None


def replay(records, source_sha):
    protocol, end = records[0], records[-1]
    require(
        protocol.get("type") == "protocol"
        and protocol.get("study") == "reader-v3"
        and protocol.get("blind") is True,
        "expected a #152 sealed run",
    )
    require(
        all(protocol.get(k + "_prompt_sha256") == v for k, v in PROMPTS.items()), "prompt mismatch"
    )
    require(
        end.get("type") == "complete"
        and (end.get("triples"), end.get("rows")) == (120, 360)
        and end.get("violations") == dict(cross_subject=0, revoked=0, invalid_citation=0),
        "expected a complete, boundary-clean input",
    )
    rows = [r for r in records if r["type"] == "question"]
    require(len(rows) == 360, "incorrect row count")
    seen = set()
    pairs = defaultdict(dict)
    tiers = {}
    reference = {}
    for tier in (1, 2, 3):
        arms = {}
        for arm in ARMS:
            arm_rows = [r for r in rows if r["tier"] == tier and r["arm"] == arm]
            require(
                Counter(r["group"] for r in arm_rows) == dict(D=20, E=8, N=12)
                and sum(r["cross_negative"] for r in arm_rows) == 6,
                "wrong group sizes",
            )
            scores = {
                mode: Counter(
                    positive_correct=0, positive_wrong=0, negative_errors=0, cross_negative_errors=0
                )
                for mode in ("strict", "bracket")
            }
            changes = defaultdict(list)
            forms = Counter()
            textual_mentions = defaultdict(list)
            for r in arm_rows:
                q = r["id"]
                require(
                    isinstance(q, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,39}", q),
                    "expected an anonymous question ID",
                )
                key = tier, arm, q
                require(key not in seen, "duplicate row")
                seen.add(key)
                gold = r["gold"]
                meta = (r["group"], gold, r["cross_negative"])
                require(
                    (r["group"] == "N") == (gold is None)
                    and (gold is None or ID.fullmatch(gold))
                    and (not r["cross_negative"] or gold is None)
                    and reference.setdefault(q, meta) == meta,
                    "question metadata mismatch",
                )
                delivered = r["reader_candidates"]
                require(
                    isinstance(delivered, list)
                    and len(delivered) <= 5
                    and all(ID.fullmatch(x) for x in delivered)
                    and len(set(delivered)) == len(delivered),
                    "invalid delivered ID list",
                )
                raw = r["raw_output"]
                require(
                    (isinstance(raw, str) and raw.strip() and r["reader_calls"] == 1 and delivered)
                    or (raw is None and r["reader_calls"] == 0 and not delivered),
                    "raw/call mismatch",
                )
                form, old, new = classify(raw, delivered)
                status = (
                    "selected"
                    if old
                    else form
                    if form in ("model_none", "program_no_material")
                    else "invalid_model_selection"
                )
                require(
                    old == r["selected"]
                    and r["reader_status"] == status
                    and r["correct"] is (old == gold)
                    and r["raw_choice"] == ([raw.strip()] if raw is not None else [])
                    and r["prompt_hash"] == PROMPTS["new" if arm == "R1" else "old"],
                    "strict replay mismatch",
                )
                forms[form] += 1
                if form == "other_text_with_id":
                    mentions = set(ID.findall(raw))
                    tag = (
                        "multiple_ids"
                        if len(mentions) != 1
                        else "undelivered"
                        if not mentions <= set(delivered)
                        else "gold_mentioned"
                        if gold in mentions
                        else "negative_delivered"
                        if gold is None
                        else "other_delivered"
                    )
                    textual_mentions[tag].append(q)
                for label, choice in (("strict", old), ("bracket", new)):
                    s = scores[label]
                    if gold is None and choice is not None:
                        s["negative_errors"] += 1
                        s["cross_negative_errors"] += int(r["cross_negative"])
                    if gold is not None:
                        s["positive_correct"] += int(choice == gold)
                        s["positive_wrong"] += int(choice is not None and choice != gold)
                if new != old:
                    category = (
                        ("cross_negative_error" if r["cross_negative"] else "negative_error")
                        if gold is None
                        else ("positive_recovered" if new == gold else "positive_wrong")
                    )
                    changes[category].append(q)
                pairs[tier, q][arm] = (r["materials_hash"], r["payload_hash"], delivered)
            arms[arm] = dict(
                scores=scores,
                forms=forms,
                changed_ids=changes,
                unaccepted_text_mentions=textual_mentions,
            )
        tiers[tier] = dict(
            arms=arms,
            contrast={
                mode: dict(
                    R1_minus_R0=arms["R1"]["scores"][mode]["positive_correct"]
                    - arms["R0"]["scores"][mode]["positive_correct"],
                    R1_minus_A=arms["R1"]["scores"][mode]["positive_correct"]
                    - arms["A"]["scores"][mode]["positive_correct"],
                    R1_minus_frozen_A=arms["R1"]["scores"][mode]["positive_correct"]
                    - arms["A"]["scores"]["strict"]["positive_correct"],
                )
                for mode in ("strict", "bracket")
            },
        )
    require(
        len(reference) == 40
        and len(pairs) == 120
        and all(set(p) == set(ARMS) and p["R0"] == p["R1"] for p in pairs.values()),
        "incomplete or unmatched triples",
    )
    return dict(
        kind="posthoc_parser_replay",
        baseline=BASE,
        source_sha256=source_sha,
        official_prompt_passed=end["prompt_passed"],
        official_product_passed=end["product_passed"],
        tiers=tiers,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("original_v3_jsonl", type=Path)
    parser.add_argument("new_summary", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    if any(p.resolve().is_relative_to(root) for p in (args.original_v3_jsonl, args.new_summary)):
        parser.error("keep private files outside the checkout")
    raw = args.original_v3_jsonl.read_bytes()
    records = [json.loads(line) for line in raw.decode("utf-8-sig").splitlines()]
    require(len(records) >= 2, "incomplete input")
    result = replay(records, hashlib.sha256(raw).hexdigest())
    with args.new_summary.open("x", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write("\n")


if __name__ == "__main__":
    main()
