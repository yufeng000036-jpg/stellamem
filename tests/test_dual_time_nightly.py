"""Step 7 tests: dual-time + nightly consolidation.

Synthetic placeholder data only. No model calls, no cloud, no writes.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

import dual_time as DT
import nightly as NI


class DualTimeTests(unittest.TestCase):
    def test_as_of_fact_interval(self):
        recs = [
            {"claim_id": "a", "valid_from": "2026-01-01T00:00:00+00:00", "valid_to": "2026-06-01T00:00:00+00:00"},
            {"claim_id": "b", "valid_from": "2026-06-01T00:00:00+00:00", "valid_to": None},
        ]
        self.assertEqual([r["claim_id"] for r in DT.as_of_fact(recs, "2026-03-01T00:00:00+00:00")], ["a"])
        self.assertEqual([r["claim_id"] for r in DT.as_of_fact(recs, "2026-07-01T00:00:00+00:00")], ["b"])

    def test_as_of_fact_open_interval(self):
        recs = [{"claim_id": "a", "valid_from": None, "valid_to": None}]
        self.assertEqual(len(DT.as_of_fact(recs, "2026-01-01T00:00:00+00:00")), 1)

    def test_as_of_recorded(self):
        recs = [
            {"claim_id": "a", "recorded_at": "2026-01-01T00:00:00+00:00"},
            {"claim_id": "b", "recorded_at": "2026-12-01T00:00:00+00:00"},
        ]
        self.assertEqual([r["claim_id"] for r in DT.as_of_recorded(recs, "2026-06-01T00:00:00+00:00")], ["a"])

    def test_supersede_keeps_old_version(self):
        recs = [{"claim_id": "a", "valid_from": "2026-01-01T00:00:00+00:00", "valid_to": None}]
        new = {"claim_id": "b", "valid_from": "2026-06-01T00:00:00+00:00", "valid_to": None}
        out = DT.supersede(recs, "a", new)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["valid_to"], "2026-06-01T00:00:00+00:00")
        self.assertEqual(recs[0]["valid_to"], None)  # input untouched

    def test_supersede_missing_id(self):
        with self.assertRaises(KeyError):
            DT.supersede([], "nope", {"claim_id": "b", "valid_from": "2026-01-01T00:00:00+00:00"})

    def test_supersede_requires_valid_from(self):
        with self.assertRaises(ValueError):
            DT.supersede([{"claim_id": "a"}], "a", {"claim_id": "b"})


class NightlyTests(unittest.TestCase):
    def _ev(self, eid, subject, predicate, value, **kw):
        row = {"event_id": eid, "subject": subject, "predicate": predicate, "value": value}
        row.update(kw)
        return row

    def test_score_formula(self):
        ev = self._ev("e1", "占位主题", "占位谓词", "占位值", importance=6, age_days=10, emotion_weight=1, consequence_weight=1)
        self.assertAlmostEqual(NI.score_event(ev), 7.0)

    def test_keep_above_threshold_forget_below(self):
        result = NI.consolidate([
            self._ev("e1", "占位主题", "喜欢", "占位值A", importance=8),
            self._ev("e2", "占位主题", "讨厌", "占位值B", importance=1),
        ])
        self.assertEqual(result["kept_count"], 1)
        self.assertEqual(result["forgotten_count"], 1)
        self.assertFalse(result["model_called"])
        self.assertFalse(result["cloud_called"])
        self.assertFalse(result["changed_anything"])

    def test_grouping_keeps_best_value(self):
        result = NI.consolidate([
            self._ev("e1", "占位主题", "喜欢", "低分值", importance=2),
            self._ev("e2", "占位主题", "喜欢", "高分值", importance=9),
        ])
        self.assertEqual(result["kept_count"], 1)
        self.assertEqual(result["kept"][0]["value"], "高分值")
        self.assertEqual(result["kept"][0]["member_count"], 2)
        self.assertEqual(result["kept"][0]["evidence_event_ids"], ["e2", "e1"])

    def test_budget_truncates_text(self):
        result = NI.consolidate([self._ev("e1", "占位主题", "喜欢", "很长占位值" * 20, importance=9)], budget_chars=10)
        self.assertEqual(result["chars"], 10)

    def test_negative_budget_rejected(self):
        with self.assertRaises(ValueError):
            NI.consolidate([], budget_chars=-1)

    def test_empty_input(self):
        result = NI.consolidate([])
        self.assertEqual(result["kept_count"], 0)
        self.assertEqual(result["forgotten_count"], 0)


if __name__ == "__main__":
    unittest.main()
