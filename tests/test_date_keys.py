"""Regression tests: deterministic date-key derivation (lever B).

Diagnosis: extraction paraphrases relative times ("last week", 上周)
into claim values, and projection matching is lexical — a question that
names the absolute date ("on March 13", 3月13日) then scores the claim
as irrelevant even though it is stored. security.derive_date_keys
resolves each relative expression against the source message's own
timestamp into ``date_keys`` (ISO day keys, ``YYYY-MM`` month keys),
following the #131 post-extraction correction pattern: deterministic,
additive fields only, original text untouched, fail-closed whenever the
timestamp or the expression is not calendar-precise.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))
from security import derive_date_keys

# A Wednesday.
TS = "2023-03-15T10:00:00+00:00"


def claim(**updates):
    c = dict(
        kind="fact",
        subject="user",
        scope="global",
        predicate="did",
        value="something",
        evidence="I did something.",
        source="user",
        provenance={"timestamp": TS, "session_id": "s", "message_id": "s:0"},
    )
    c.update(updates)
    return c


def keys(**kw):
    c, changed = derive_date_keys(claim(**kw))
    return c, changed


class EnglishExpressionTests(unittest.TestCase):
    def test_yesterday_and_days_ago(self):
        c, changed = keys(evidence="I ordered it yesterday.")
        self.assertTrue(changed)
        self.assertEqual(c["date_keys"], ["2023-03-14"])
        c, _ = keys(evidence="I ordered it 3 days ago.")
        self.assertEqual(c["date_keys"], ["2023-03-12"])
        c, _ = keys(evidence="It arrived a day ago.")
        self.assertEqual(c["date_keys"], ["2023-03-14"])

    def test_last_week_is_previous_iso_week(self):
        c, _ = keys(evidence="I went hiking last week.")
        self.assertEqual(
            c["date_keys"],
            [
                "2023-03-06",
                "2023-03-07",
                "2023-03-08",
                "2023-03-09",
                "2023-03-10",
                "2023-03-11",
                "2023-03-12",
            ],
        )

    def test_weeks_and_months_ago(self):
        c, _ = keys(evidence="I started 2 weeks ago.")
        self.assertEqual(c["date_keys"], ["2023-03-01"])
        c, _ = keys(evidence="I moved here 2 months ago.")
        self.assertEqual(c["date_keys"], ["2023-01-15"])
        c, _ = keys(evidence="I visited last month.")
        self.assertEqual(c["date_keys"], ["2023-02"])

    def test_last_weekday(self):
        # Base is Wednesday 2023-03-15; last Monday is 2023-03-13.
        c, _ = keys(evidence="I called her last Monday.")
        self.assertEqual(c["date_keys"], ["2023-03-13"])

    def test_value_text_is_scanned_too(self):
        c, changed = keys(value="went hiking last week", evidence="We went hiking.")
        self.assertTrue(changed)
        self.assertIn("2023-03-06", c["date_keys"])


class ChineseExpressionTests(unittest.TestCase):
    def test_day_expressions(self):
        c, _ = keys(evidence="我昨天买了这个。")
        self.assertEqual(c["date_keys"], ["2023-03-14"])
        c, _ = keys(evidence="三天前下的单。")
        self.assertEqual(c["date_keys"], ["2023-03-12"])
        c, _ = keys(evidence="我前天去的。")
        self.assertEqual(c["date_keys"], ["2023-03-13"])

    def test_week_and_month_expressions(self):
        c, _ = keys(evidence="上周去徒步了。")
        self.assertIn("2023-03-06", c["date_keys"])
        self.assertIn("2023-03-12", c["date_keys"])
        c, _ = keys(evidence="上个月开始试用的。")
        self.assertEqual(c["date_keys"], ["2023-02"])
        c, _ = keys(evidence="两个月前搬来的。")
        self.assertEqual(c["date_keys"], ["2023-01-15"])


class FailClosedTests(unittest.TestCase):
    def test_vague_expressions_yield_nothing(self):
        for text in ("I did it a few days ago.", "I saw her recently.", "去年去过一次。"):
            c, changed = keys(evidence=text)
            self.assertFalse(changed, text)
            self.assertNotIn("date_keys", c)

    def test_no_timestamp_no_keys(self):
        c, changed = derive_date_keys(claim(evidence="I did it yesterday.", provenance={}))
        self.assertFalse(changed)
        self.assertNotIn("date_keys", c)

    def test_unparseable_timestamp_no_keys(self):
        c, changed = derive_date_keys(
            claim(evidence="I did it yesterday.", provenance={"timestamp": "not-a-date"})
        )
        self.assertFalse(changed)

    def test_no_expression_no_fields(self):
        c, changed = keys(evidence="I like this a lot.")
        self.assertFalse(changed)
        self.assertNotIn("date_keys", c)
        self.assertNotIn("date_keys_derivation", c)

    def test_original_text_untouched_and_trace_recorded(self):
        original = claim(evidence="I ordered it last week.")
        c, changed = derive_date_keys(original)
        self.assertTrue(changed)
        self.assertEqual(c["evidence"], "I ordered it last week.")
        self.assertEqual(c["value"], original["value"])
        self.assertEqual(c["date_keys_derivation"], "relative-time-v1")
        self.assertIn("last week", c["date_keys_expressions"])
        self.assertNotIn("date_keys", original)


if __name__ == "__main__":
    unittest.main()
