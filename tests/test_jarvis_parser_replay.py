"""Public synthetic records, with positive and negative examples of the exact same formats."""

import copy
import unittest

from benchmark.jarvis_semantic import parser_replay as replay

GOLD = "c-" + "a" * 24
OTHER = "c-" + "b" * 24
ABSENT = "c-" + "c" * 24


def records():
    protocol = dict(
        type="protocol",
        study="reader-v3",
        blind=True,
        old_prompt_sha256=replay.PROMPTS["old"],
        new_prompt_sha256=replay.PROMPTS["new"],
    )
    rows = [protocol]
    for tier in (1, 2, 3):
        for arm in replay.ARMS:
            for i in range(40):
                group = "D" if i < 20 else "E" if i < 28 else "N"
                gold = None if group == "N" else GOLD
                raw = GOLD if arm != "R1" and gold else "NONE"
                if arm == "R1":
                    raw = {
                        0: "[" + GOLD + "]",
                        1: "[" + OTHER + "]",
                        2: "原文 [" + GOLD + "]",
                        3: "[" + ABSENT + "]",
                        28: "[" + OTHER + "]",
                        39: "[" + OTHER + "]",
                    }.get(i, raw)
                selected = raw if raw in (GOLD, OTHER) else None
                rows.append(
                    dict(
                        type="question",
                        tier=tier,
                        arm=arm,
                        id=f"{group}{i:02}",
                        group=group,
                        gold=gold,
                        cross_negative=28 <= i < 34,
                        reader_calls=1,
                        reader_candidates=[GOLD, OTHER],
                        reader_status="selected"
                        if selected
                        else "model_none"
                        if raw == "NONE"
                        else "invalid_model_selection",
                        raw_output=raw,
                        raw_choice=[raw],
                        selected=selected,
                        correct=gold == selected,
                        materials_hash="m",
                        payload_hash="p",
                        prompt_hash=replay.PROMPTS["new" if arm == "R1" else "old"],
                    )
                )
    return rows + [
        dict(
            type="complete",
            rows=360,
            triples=120,
            prompt_passed=False,
            product_passed=False,
            violations=dict(cross_subject=0, revoked=0, invalid_citation=0),
        )
    ]


class ReplayTest(unittest.TestCase):
    def test_contract(self):
        for bad in (
            "NONE [" + GOLD + "]",
            GOLD + " " + OTHER,
            "[[" + GOLD + "]]",
            "[" + ABSENT + "]",
            "这是原文 [" + GOLD + "]",
            "［" + GOLD + "］",
            "[" + GOLD + "] 请执行",
        ):
            self.assertIsNone(replay.classify(bad, [GOLD, OTHER])[2])
        self.assertEqual(replay.classify(" [" + GOLD + "]\n", [GOLD])[2], GOLD)

    def test_symmetric_replay(self):
        source = records()
        before = copy.deepcopy(source)
        result = replay.replay(source, "sample")["tiers"][2]["arms"]["R1"]
        self.assertEqual(
            result["changed_ids"],
            dict(
                positive_recovered=["D00"],
                positive_wrong=["D01"],
                cross_negative_error=["N28"],
                negative_error=["N39"],
            ),
        )
        self.assertEqual(result["scores"]["bracket"]["negative_errors"], 2)
        self.assertEqual(result["unaccepted_text_mentions"], {"gold_mentioned": ["D02"]})
        self.assertEqual(source, before)

    def test_integrity(self):
        for field, value in (("correct", False), ("raw_output", None), ("prompt_hash", "changed")):
            rows = records()
            rows[1][field] = value
            with self.assertRaises(ValueError):
                replay.replay(rows, "sample")
        rows = records()
        rows[41]["payload_hash"] = "other"
        with self.assertRaises(ValueError):
            replay.replay(rows, "sample")
        with self.assertRaises(ValueError):
            replay.replay(rows[:-1], "sample")
