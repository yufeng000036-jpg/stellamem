#!/usr/bin/env python3
"""Unit tests for det_module. Run: python3 -m unittest test_det_module -v"""

import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import det_module as dm


def rec(rid, value, rdate="2024-01-01T00:00:00+00:00", scope="s", pred="p"):
    return {
        "id": rid,
        "scope": scope,
        "source": "user",
        "predicate": pred,
        "value": value,
        "date": rdate,
    }


class TestDateOps(unittest.TestCase):
    def test_gap_leap_year(self):
        # 2024 is a leap year: Feb 28 -> Mar 1 is 2 days, not 1.
        a = rec(
            "c-1",
            "Ana Ruiz attended choir practice on February 28, 2024.",
            "2024-02-28T10:00:00+00:00",
        )
        b = rec(
            "c-2",
            "Ben Osei attended choir practice on March 1, 2024.",
            "2024-03-01T10:00:00+00:00",
        )
        r = dm.compute("date_gap", "How many days apart were the two?", [a, b], None)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["answer"], "2")
        self.assertEqual(r["receipt"]["operation"], "date_gap_days")
        self.assertEqual(r["receipt"]["record_ids"], ["c-1", "c-2"])
        self.assertTrue(all(o["provenance"] == "event_date" for o in r["receipt"]["operands"]))

    def test_gap_zh_dates(self):
        a = rec("c-1", "韩越于2024年2月28日参加合唱练习。", "2024-02-28T10:00:00+00:00")
        b = rec("c-2", "罗一苇于2024年3月1日参加合唱练习。", "2024-03-01T10:00:00+00:00")
        r = dm.compute("date_gap", "相隔多少天？", [a, b], None)
        self.assertEqual((r["status"], r["answer"]), ("ok", "2"))

    def test_weekday_known_dates(self):
        # 2000-01-01 was a Saturday; 2024-02-29 (leap day) was a Thursday.
        a = rec("c-1", "Ana Ruiz attended choir practice on January 1, 2000.")
        r = dm.compute("weekday", "On what day of the week did Ana Ruiz attend?", [a], None)
        self.assertEqual((r["status"], r["answer"]), ("ok", "Saturday"))
        b = rec("c-2", "韩越于2024年2月29日参加合唱练习。")
        r = dm.compute("weekday", "韩越参加合唱练习那天是星期几？", [b], None)
        self.assertEqual((r["status"], r["answer"]), ("ok", "星期四"))

    def test_session_date_only_refused(self):
        # Record text states NO event date; only the session (record) date
        # exists. A date op must refuse, never silently use the session date.
        a = rec(
            "c-1",
            "Ana Ruiz usually does choir practice at the hall.",
            "2024-03-05T10:00:00+00:00",
        )
        r = dm.compute("weekday", "On what day of the week did Ana Ruiz attend?", [a], None)
        self.assertEqual(r["status"], "refused")
        self.assertEqual(r["reason_code"], "insufficient_operands")

    def test_ambiguous_date_refused(self):
        a = rec("c-1", "Ana Ruiz moved the drill from March 1, 2024 to March 5, 2024.")
        b = rec("c-2", "Ben Osei attended choir practice on March 3, 2024.")
        r = dm.compute("date_gap", "How many days apart?", [a, b], None)
        self.assertEqual(r["status"], "refused")
        self.assertEqual(r["reason_code"], "ambiguous_date")

    def test_nth_occurrence_and_from_last(self):
        recs = [
            rec("c-1", "贺兰庭于2023年11月17日参加园艺修剪。"),
            rec("c-2", "贺兰庭在2023年12月31日参加了园艺修剪。"),
            rec("c-3", "贺兰庭于2024年2月10日参加园艺修剪。"),
        ]
        r = dm.compute("nth_date", "贺兰庭第2次参加园艺修剪是哪一天？", recs, None)
        self.assertEqual((r["status"], r["answer"]), ("ok", "2023-12-31"))
        r = dm.compute("nth_date", "贺兰庭倒数第1次参加园艺修剪是哪一天？", recs, None)
        self.assertEqual((r["status"], r["answer"]), ("ok", "2024-02-10"))
        r = dm.compute("nth_date", "贺兰庭第5次参加园艺修剪是哪一天？", recs, None)
        self.assertEqual(r["status"], "refused")
        self.assertEqual(r["reason_code"], "insufficient_operands")

    def test_countdown(self):
        a = rec(
            "c-1",
            "方既明的图书馆借阅证有效期截止到2024年8月16日。",
            "2024-05-18T10:00:00+00:00",
            pred="有效期",
        )
        r = dm.compute("countdown", "方既明的图书馆借阅证还有多少天到期？", [a], date(2024, 8, 9))
        self.assertEqual((r["status"], r["answer"]), ("ok", "7"))
        provs = {o["provenance"] for o in r["receipt"]["operands"]}
        self.assertEqual(provs, {"text_extracted", "question_date"})
        # Already expired at question time -> no fake precise result.
        r = dm.compute("countdown", "方既明的图书馆借阅证还有多少天到期？", [a], date(2024, 9, 1))
        self.assertEqual(r["status"], "refused")


class TestAggregateOps(unittest.TestCase):
    def test_count_with_dedup(self):
        a = rec("c-1", "Marta Kowalska attended choir practice on May 20, 2024.")
        b = rec("c-2", "Marta Kowalska attended choir practice on June 12, 2024.")
        r = dm.compute(
            "count",
            "How many times did Marta Kowalska attend choir practice in total?",
            [a, b, a],
            None,
        )
        self.assertEqual((r["status"], r["answer"]), ("ok", "2"))

    def test_sum_en_and_zh(self):
        recs = [
            rec(
                "c-1",
                "Ruben Costa purchased 4 copies of opera collections on July 30, 2025.",
            ),
            rec(
                "c-2",
                "Ruben Costa bought 3 copies of opera collections on May 16, 2025.",
            ),
            rec("c-3", "冯抱朴在2024年12月5日买了5本旅行手记。"),
            rec("c-4", "冯抱朴于2024年8月26日购入旅行手记4本。"),
        ]
        r = dm.compute("sum", "How many copies in total?", recs[:2], None)
        self.assertEqual((r["status"], r["answer"]), ("ok", "7"))
        r = dm.compute("sum", "一共买了多少本？", recs[2:], None)
        self.assertEqual((r["status"], r["answer"]), ("ok", "9"))

    def test_sum_missing_quantity_refused(self):
        a = rec("c-1", "Ruben Costa purchased some copies of opera collections.")
        r = dm.compute("sum", "How many copies in total?", [a], None)
        self.assertEqual(r["status"], "refused")
        self.assertEqual(r["reason_code"], "insufficient_operands")

    def test_max_and_tie(self):
        recs = [
            rec("c-1", "Hugo Almeida covered 57 km in dawn run on February 26, 2023."),
            rec("c-2", "Peter Almasi ran 60 km during dawn run on November 9, 2024."),
            rec("c-3", "萧未央在2024年7月30日的晨练跑了50公里。"),
        ]
        r = dm.compute("max", "who ran the farthest?", recs, None)
        self.assertEqual((r["status"], r["answer"]), ("ok", "Peter Almasi"))
        tie = [
            rec("c-4", "Hugo Almeida covered 60 km in dawn run on February 26, 2023."),
            rec("c-5", "Peter Almasi ran 60 km during dawn run on November 9, 2024."),
        ]
        r = dm.compute("max", "who ran the farthest?", tie, None)
        self.assertEqual(r["status"], "refused")
        self.assertEqual(r["reason_code"], "conflicting_records")

    def test_max_incomplete_named_set_refused(self):
        # The question names three runners but only two records are cited:
        # the set is provably incomplete, so no precise result may be made.
        recs = [
            rec("c-1", "Hugo Almeida covered 57 km in dawn run on February 26, 2023."),
            rec("c-2", "Peter Almasi ran 60 km during dawn run on November 9, 2024."),
        ]
        r = dm.compute(
            "max",
            "Among Hugo Almeida, Felix Wagner and Peter "
            "Almasi, who ran the farthest during dawn run?",
            recs,
            None,
        )
        self.assertEqual(r["status"], "refused")
        self.assertEqual(r["reason_code"], "insufficient_operands")
        recs.append(rec("c-3", "Felix Wagner ran 13 km during dawn run on April 1, 2023."))
        r = dm.compute(
            "max",
            "Among Hugo Almeida, Felix Wagner and Peter "
            "Almasi, who ran the farthest during dawn run?",
            recs,
            None,
        )
        self.assertEqual((r["status"], r["answer"]), ("ok", "Peter Almasi"))

    def test_no_evidence_refused(self):
        r = dm.compute("count", "How many times?", [], None)
        self.assertEqual(r["status"], "refused")
        self.assertEqual(r["reason_code"], "no_valid_evidence")


class TestClassifier(unittest.TestCase):
    def test_routes(self):
        cases = {
            "袁山月参加合唱练习那天是星期几？": "weekday",
            "On what day of the week did Elias Brandt attend?": "weekday",
            "How many days apart were the two drills?": "date_gap",
            "韩越参加志愿导览的日子，和罗一苇参加园艺修剪的日子，相隔多少天？": "date_gap",
            "贺兰庭第2次参加园艺修剪是哪一天？": "nth_date",
            "方既明的图书馆借阅证还有多少天到期？": "countdown",
            "How many days are left before the pass expires?": "countdown",
            "薛采薇总共参加了几次合唱练习？": "count",
            "How many times did Marta Kowalska attend choir practice in total?": "count",
            "How many copies of opera collections did Ruben Costa buy in total?": "sum",
            "冯抱朴一共买了多少本旅行手记？": "sum",
            "三个人里，谁晨练跑得最远？": "max",
            "which color does Oliver like best?": "lookup",
        }
        for q, want in cases.items():
            got, _ = dm.classify_question(q)
            self.assertEqual(got, want, q)


if __name__ == "__main__":
    unittest.main()
