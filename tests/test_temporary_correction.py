"""Regression tests: deterministic correction of the model's temporary flag.

Diagnosis (2026-11-18, round-4 LongMemEval attribution): the extraction
prompt lists ``temporary`` as an output field but defines it nowhere, and
the model applies it to any time-bound content. Of the 1,115 gold claims
dropped as ``ineligible:temporary-flag``, 1,112 carried the flag from the
model itself, 2 from the source-message rule match, 0 from the claim-text
rules; 92% of all temporary-flagged active claims were gold.

The correction (security.correct_temporary_flag, wired into the pipeline
where the evidence has been located in a specific message) is
one-directional and fail-closed: plans, hypotheses and unsettled content
keep the flag, the system's own transient-state rules keep the flag, and
only a claim with a positive past anchor and no intent marker is
released. These tests pin both directions.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))
from claim_normalizer import normalize_claim
from pipeline import compile_messages
from security import correct_temporary_flag


def claim(**updates):
    c = dict(
        kind="fact",
        subject="user",
        scope="global",
        predicate="did",
        value="something",
        evidence="I did something.",
        source="user",
        confidence=0.9,
        temporary=True,
    )
    c.update(updates)
    return normalize_claim(c)


class CorrectionUnitTests(unittest.TestCase):
    def correct(self, message_text="", **kw):
        return correct_temporary_flag(claim(**kw), message_text)

    def test_completed_past_fact_is_released(self):
        c, corrected = self.correct(
            value="Coach leather wallet",
            evidence="I ordered a nice leather wallet from Coach last week",
        )
        self.assertTrue(corrected)
        self.assertFalse(c["temporary"])
        self.assertEqual(c["temporary_correction"], "past-anchor-v1")
        self.assertNotIn("temporary", c["policy_reasons"])

    def test_plan_keeps_flag(self):
        c, corrected = self.correct(
            value="road bike",
            evidence="I don't have a road bike yet, but I'm planning to invest in one soon",
        )
        self.assertFalse(corrected)
        self.assertTrue(c["temporary"])

    def test_undecided_keeps_flag_even_with_perfect_anchor(self):
        # "I've been looking" is a past anchor, but "not sure" is an
        # unsettled marker and intent is checked first.
        c, corrected = self.correct(
            value="Korg or Roland synth",
            evidence=(
                "I've been looking at some of the newer models from Korg and "
                "Roland, but I'm not sure what to get yet"
            ),
        )
        self.assertFalse(corrected)
        self.assertTrue(c["temporary"])

    def test_transient_state_rules_keep_flag(self):
        c, corrected = self.correct(
            value="fed up",
            evidence="today I am fed up and tired; yesterday I bought milk",
        )
        self.assertFalse(corrected)
        self.assertTrue(c["temporary"])

    def test_message_level_transient_rules_keep_flag(self):
        c, corrected = self.correct(
            "today I am so tired and angry about everything, never again. "
            "I ordered groceries online last week",
            value="groceries",
            evidence="I ordered groceries online last week",
        )
        self.assertFalse(corrected)
        self.assertTrue(c["temporary"])

    def test_no_anchor_stays_flagged(self):
        c, corrected = self.correct(
            value="leftover peppers and onions",
            evidence="leftover peppers and onions that I have in my fridge",
        )
        self.assertFalse(corrected)
        self.assertTrue(c["temporary"])

    def test_chinese_past_fact_is_released(self):
        c, corrected = self.correct(
            value="已经读完《夜莺》",
            evidence="已经读完这本书",
        )
        self.assertTrue(corrected)
        self.assertFalse(c["temporary"])

    def test_chinese_unsettled_keeps_flag_despite_date(self):
        c, corrected = self.correct(
            value="1月28日下单，尚未到货",
            evidence=(
                "I ordered some cable organizers online on January 28th, "
                "but they still haven't arrived"
            ),
        )
        self.assertFalse(corrected)
        self.assertTrue(c["temporary"])

    def test_unflagged_claim_is_untouched(self):
        c = claim(temporary=False)
        out, corrected = correct_temporary_flag(c, "")
        self.assertFalse(corrected)
        self.assertFalse(out["temporary"])
        self.assertNotIn("temporary_correction", out)


def msg(text, mid):
    return {
        "text": text,
        "speaker": "user",
        "timestamp": "2026-01-01T15:00:00",
        "offset": 0,
        "provenance": {
            "provider": "test",
            "precision": "message",
            "message_id": mid,
            "speaker": "user",
            "timestamp": "2026-01-01T15:00:00",
        },
    }


def run_pipeline(text, raw_claims):
    def llm(system, user):
        return json.dumps(raw_claims)

    claims = [
        c
        for batch in compile_messages([msg(text, "m1")], {"chunk_chars": 2500}, llm)
        for c in batch["claims"]
    ]
    assert len(claims) == 1, claims
    return claims[0]


class CorrectionPipelineTests(unittest.TestCase):
    def test_pipeline_releases_mislabelled_past_fact(self):
        c = run_pipeline(
            "I ordered a nice leather wallet from Coach last week and I love it.",
            [
                dict(
                    kind="fact",
                    subject="user",
                    predicate="bought",
                    value="Coach leather wallet",
                    evidence="I ordered a nice leather wallet from Coach last week",
                    source="user",
                    confidence=0.9,
                    message_index=0,
                    temporary=True,
                )
            ],
        )
        self.assertFalse(c["temporary"])
        self.assertEqual(c["temporary_correction"], "past-anchor-v1")

    def test_pipeline_keeps_plan_flagged(self):
        c = run_pipeline(
            "I'm planning to invest in a road bike soon.",
            [
                dict(
                    kind="fact",
                    subject="user",
                    predicate="plans to buy",
                    value="road bike",
                    evidence="I'm planning to invest in a road bike soon",
                    source="user",
                    confidence=0.9,
                    message_index=0,
                    temporary=True,
                )
            ],
        )
        self.assertTrue(c["temporary"])
        self.assertNotIn("temporary_correction", c)


if __name__ == "__main__":
    unittest.main()
