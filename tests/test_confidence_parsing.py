"""Regression: model confidence may arrive as a word, not a number.

Qwen3-4B returns "high"/"medium"/"low" when the prompt does not demand a number.
Previously every such claim was flattened to 0.0, which forced the whole inbox to
pending. These tests pin the tolerant parsing and the end-to-end effect.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))
from claim_normalizer import normalize_claim, number
from pipeline import compile_messages


class NumberParsingTests(unittest.TestCase):
    def test_numeric_passthrough(self):
        self.assertAlmostEqual(number(0.95), 0.95)
        self.assertAlmostEqual(number("0.8"), 0.8)
        self.assertAlmostEqual(number(1), 1.0)

    def test_word_labels_map_to_values(self):
        self.assertAlmostEqual(number("high"), 0.9)
        self.assertAlmostEqual(number("medium"), 0.7)
        self.assertAlmostEqual(number("low"), 0.5)
        self.assertAlmostEqual(number("very high"), 0.95)
        self.assertAlmostEqual(number("certain"), 1.0)

    def test_embedded_number_is_recovered(self):
        self.assertAlmostEqual(number("high (0.9)"), 0.9)
        self.assertAlmostEqual(number("confidence: 0.85"), 0.85)

    def test_percentage_is_scaled(self):
        self.assertAlmostEqual(number("90"), 0.9)
        self.assertAlmostEqual(number("50"), 0.5)
        self.assertAlmostEqual(number("100"), 1.0)

    def test_garbage_falls_back(self):
        self.assertEqual(number(None), 0.0)
        self.assertEqual(number("unknown-word"), 0.0)
        self.assertEqual(number(""), 0.0)

    def test_clamped_to_unit_interval(self):
        # Values above 1 are read as percentages, so they scale into [0, 1].
        self.assertEqual(number(5), 0.05)
        self.assertEqual(number(-3), 0.0)
        self.assertEqual(number(0.0), 0.0)


class ConfidenceEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.config = {"chunk_chars": 2500, "model_max_tokens": 8192}

    def _msg(self, text, i=0):
        return {
            "text": text,
            "speaker": "user",
            "timestamp": "2026-01-01T15:00:%02d" % i,
            "offset": 0,
            "provenance": {"provider": "test", "precision": "message", "content_hash": str(i)},
        }

    def test_word_confidence_becomes_a_number(self):
        claim = {
            "kind": "preference",
            "subject": "user",
            "scope": "global",
            "predicate": "likes",
            "value": "oolong tea",
            "evidence": "I like oolong tea",
            "confidence": "high",
            "source": "user",
            "message_index": 0,
        }
        out = list(
            compile_messages(
                [self._msg("I like oolong tea")],
                self.config,
                lambda s, u: json.dumps([claim]),
            )
        )
        conf = out[0]["claims"][0]["confidence"]
        self.assertIsInstance(conf, float)
        self.assertGreater(conf, 0.0, "a word label must not flatten to 0.0")

    def test_normalize_claim_keeps_word_confidence(self):
        c = normalize_claim(
            {
                "kind": "fact",
                "subject": "s",
                "predicate": "p",
                "value": "v",
                "evidence": "e",
                "confidence": "medium",
            }
        )
        self.assertGreater(c["confidence"], 0.0)


if __name__ == "__main__":
    unittest.main()
