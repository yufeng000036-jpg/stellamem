#!/usr/bin/env python3
"""Unit tests for the corrected prefilter. Run: python3 -m unittest test_prefilter -v"""
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from prefilter import prefilter


def rec(rid, value, rdate, scope="s", pred="p", source="user"):
    return {"id": rid, "scope": scope, "source": source, "predicate": pred,
            "value": value, "date": rdate}


class TestMarkedExpired(unittest.TestCase):
    def setUp(self):
        self.old = rec("c-old", "白翎的宝蓝保温壶收在栖梧体育馆。（注：此记录已过期）",
                       "2024-02-17T00:00:00+00:00", scope="宝蓝保温壶", pred="存放地点")
        self.new = rec("c-new", "白翎把宝蓝保温壶放在青藤茶馆。",
                       "2024-06-09T00:00:00+00:00", scope="宝蓝保温壶", pred="存放地点")

    def test_current_question_removes_marked(self):
        kept, rm = prefilter([self.old, self.new], "白翎把宝蓝保温壶放哪儿了？",
                             date(2025, 5, 3))
        self.assertEqual([r["id"] for r in kept], ["c-new"])
        self.assertEqual(rm[0]["rule"], "R1_marked_expired")

    def test_historical_question_keeps_marked(self):
        # Asking about a past state: the then-valid record must survive.
        kept, rm = prefilter([self.old, self.new],
                             "白翎当时把宝蓝保温壶放在哪儿了？", date(2025, 5, 3))
        self.assertEqual({r["id"] for r in kept}, {"c-old", "c-new"})
        self.assertEqual(rm, [])


class TestCorrection(unittest.TestCase):
    def setUp(self):
        self.wrong = rec("c-wrong", "Bruno Keller usually does cycling drill at Summit Climbing Gym.",
                         "2024-11-14T00:00:00+00:00", scope="cycling drill", pred="activity place")
        self.agree = rec("c-agree", "Bruno Keller goes to Marigold Bakery for cycling drill.",
                         "2024-11-14T00:00:00+00:00", scope="cycling drill", pred="activity place")
        self.corr = rec("c-corr", "Correction: Bruno Keller does cycling drill at Marigold Bakery; the earlier record was wrong.",
                        "2024-11-15T00:00:00+00:00", scope="cycling drill", pred="correction")
        self.other = rec("c-other", "Jonas Weber goes to Osprey Bike Shop for cycling drill.",
                         "2023-05-18T00:00:00+00:00", scope="cycling drill", pred="activity place")
        self.later = rec("c-later", "Bruno Keller usually does cycling drill at Hazelwood Pool.",
                         "2025-01-10T00:00:00+00:00", scope="cycling drill", pred="activity place")

    def test_correction_removes_earlier_same_person(self):
        kept, rm = prefilter([self.wrong, self.agree, self.corr, self.other,
                              self.later],
                             "where does Bruno Keller usually go for cycling drill?",
                             date(2025, 4, 6))
        ids = {r["id"] for r in kept}
        # correction record stays; other-person and post-correction records stay
        self.assertEqual(ids, {"c-corr", "c-other", "c-later"})
        self.assertTrue(all(r["rule"] == "R2_correction" for r in rm))

    def test_correction_applies_to_historical_too(self):
        kept, _ = prefilter([self.wrong, self.corr],
                            "Bruno Keller 当时在哪里骑行？", date(2025, 4, 6))
        self.assertEqual([r["id"] for r in kept], ["c-corr"])


class TestValidityJoin(unittest.TestCase):
    def setUp(self):
        self.code_old = rec("c-code-old", "Vera Ziegler's hiking gear rental card number is FM-2660.",
                            "2023-12-16T00:00:00+00:00", scope="hiking gear rental card", pred="code number")
        self.val_old = rec("c-val-old", "Vera Ziegler's hiking gear rental card is valid until July 14, 2024.",
                           "2023-12-16T00:00:00+00:00", scope="hiking gear rental card", pred="valid until")
        self.code_new = rec("c-code-new", "Vera Ziegler's hiking gear rental card number is PK-9458.",
                            "2024-07-26T00:00:00+00:00", scope="hiking gear rental card", pred="code number")
        self.val_new = rec("c-val-new", "Vera Ziegler's hiking gear rental card is valid until January 24, 2025.",
                           "2024-07-26T00:00:00+00:00", scope="hiking gear rental card", pred="valid until")
        self.qd = date(2024, 8, 19)

    def test_expired_pair_removed_valid_pair_kept(self):
        kept, rm = prefilter([self.code_old, self.val_old, self.code_new,
                              self.val_new],
                             "What is the current number of Vera Ziegler's card?",
                             self.qd)
        ids = {r["id"] for r in kept}
        self.assertNotIn("c-code-old", ids)      # expired credential removed
        self.assertIn("c-val-old", ids)          # validity record itself stays
        self.assertIn("c-code-new", ids)         # still-valid credential stays
        self.assertEqual(rm[0]["rule"], "R3_validity_join")

    def test_historical_keeps_expired_pair(self):
        kept, _ = prefilter([self.code_old, self.val_old],
                            "Vera Ziegler 当时的卡号是多少？", self.qd)
        self.assertEqual({r["id"] for r in kept}, {"c-code-old", "c-val-old"})

    def test_unpaired_code_kept(self):
        kept, _ = prefilter([self.code_old], "What is the current number?",
                            self.qd)
        self.assertEqual([r["id"] for r in kept], ["c-code-old"])


class TestNoDateLatestSupersede(unittest.TestCase):
    def test_unmarked_chain_fully_kept(self):
        # Same person/scope/predicate, two different values, NO explicit
        # marker: the filter must not pick a winner by date.
        a = rec("c-a", "Mateo Ferrer likes cheese omelette the most.",
                "2023-03-22T00:00:00+00:00", scope="favorite food", pred="favorite food")
        b = rec("c-b", "Mateo Ferrer's favorite food is tomato soup.",
                "2023-08-02T00:00:00+00:00", scope="favorite food", pred="favorite food")
        kept, rm = prefilter([a, b], "These days, which food is Mateo Ferrer's "
                                     "top choice?", date(2025, 5, 11))
        self.assertEqual({r["id"] for r in kept}, {"c-a", "c-b"})
        self.assertEqual(rm, [])


if __name__ == "__main__":
    unittest.main()
