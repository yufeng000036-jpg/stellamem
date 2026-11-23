#!/usr/bin/env python3
"""Tests for prefilter_v2 (V6.2 stage 3). Run:
python3 -m unittest test_prefilter_v2 -v
Covers: the E0076 regression (glued Chinese name pairing), the tightened
R2 (conflict-only removal), and the mandated negative classes -- wrong
version relation, ambiguous reference, multi-person mixing, unclear
correction scope (>=2 cases each) -- plus scope isolation, historical
stance, and validity-record immunity."""
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from prefilter_v2 import prefilter_v2

QD = date(2025, 5, 3)


def rec(rid, value, rdate, scope="s", pred="p", source="user"):
    return {"id": rid, "scope": scope, "source": source, "predicate": pred,
            "value": value, "date": rdate}


def kept_ids(records, q="现在的情况是什么？", qd=QD):
    kept, rm = prefilter_v2(records, q, qd)
    return {r["id"] for r in kept}, rm


class TestE0076Regression(unittest.TestCase):
    """The exact E0076 shape: correction value glues name+activity, one
    earlier record agrees with the correction, one conflicts."""

    def setUp(self):
        self.corr = rec("c-corr", "更正：谭景行园艺修剪去听涛泳池，此前记录有误。",
                        "2023-04-21T07:05:11+00:00", scope="园艺修剪", pred="更正",
                        source="assistant")
        self.agree = rec("c-agree", "谭景行通常在听涛泳池园艺修剪。",
                         "2023-04-16T15:00:00+00:00", scope="园艺修剪",
                         pred="活动地点", source="assistant")
        self.wrong = rec("c-wrong", "谭景行园艺修剪一般去折柳自行车行。",
                         "2023-04-16T09:00:00+00:00", scope="园艺修剪",
                         pred="活动地点", source="assistant")

    def test_conflicting_removed_agreeing_kept(self):
        ids, rm = kept_ids([self.corr, self.agree, self.wrong],
                           "核对一下：谭景行平时在哪儿园艺修剪？")
        self.assertEqual(ids, {"c-corr", "c-agree"})
        self.assertEqual([r["id"] for r in rm], ["c-wrong"])
        self.assertEqual(rm[0]["rule"], "R2_correction_conflict")


class TestCorrectionSemanticsEN(unittest.TestCase):
    def setUp(self):
        self.wrong = rec("c-wrong", "Bruno Keller usually does cycling drill at Summit Climbing Gym.",
                         "2024-11-14T00:00:00+00:00", scope="cycling drill", pred="activity place")
        self.agree = rec("c-agree", "Bruno Keller goes to Marigold Bakery for cycling drill.",
                         "2024-11-14T00:00:00+00:00", scope="cycling drill", pred="activity place")
        self.corr = rec("c-corr", "Correction: Bruno Keller does cycling drill at Marigold Bakery; the earlier record was wrong.",
                        "2024-11-15T00:00:00+00:00", scope="cycling drill", pred="correction")

    def test_agreeing_record_now_kept(self):
        # v1 removed BOTH earlier records; v2 keeps the one that agrees
        # with the correction (it is consistent evidence, not revoked).
        ids, rm = kept_ids([self.wrong, self.agree, self.corr],
                           "where does Bruno Keller usually go for cycling drill?")
        self.assertEqual(ids, {"c-agree", "c-corr"})
        self.assertEqual([r["id"] for r in rm], ["c-wrong"])


class TestWrongVersionRelation(unittest.TestCase):
    def test_other_persons_record_not_removed(self):
        # Correction is about Bruno; Jonas's earlier record in the same
        # scope states a different place but is NOT Bruno's -> keep.
        corr = rec("c-corr", "Correction: Bruno Keller does cycling drill at Marigold Bakery; the earlier record was wrong.",
                   "2024-11-15T00:00:00+00:00", scope="cycling drill", pred="correction")
        other = rec("c-other", "Jonas Weber goes to Osprey Bike Shop for cycling drill.",
                    "2023-05-18T00:00:00+00:00", scope="cycling drill", pred="activity place")
        ids, rm = kept_ids([corr, other], "where does Jonas Weber go?")
        self.assertEqual(ids, {"c-corr", "c-other"})
        self.assertEqual(rm, [])

    def test_record_after_correction_not_removed(self):
        # A conflicting record dated AFTER the correction is a later fact,
        # not a target of the correction -> keep.
        corr = rec("c-corr", "Correction: Bruno Keller does cycling drill at Marigold Bakery; the earlier record was wrong.",
                   "2024-11-15T00:00:00+00:00", scope="cycling drill", pred="correction")
        later = rec("c-later", "Bruno Keller usually does cycling drill at Hazelwood Pool.",
                    "2025-01-10T00:00:00+00:00", scope="cycling drill", pred="activity place")
        ids, rm = kept_ids([corr, later], "where does Bruno Keller go?")
        self.assertEqual(ids, {"c-corr", "c-later"})
        self.assertEqual(rm, [])

    def test_different_scope_not_removed(self):
        # Same person, earlier date, conflicting value, but a DIFFERENT
        # attribute (scope): a correction never crosses attributes.
        corr = rec("c-corr", "更正：谭景行园艺修剪去听涛泳池，此前记录有误。",
                   "2023-04-21T07:05:11+00:00", scope="园艺修剪", pred="更正",
                   source="assistant")
        other_attr = rec("c-attr", "谭景行晨练一般去折柳自行车行。",
                         "2023-04-16T09:00:00+00:00", scope="晨练",
                         pred="活动地点", source="assistant")
        ids, rm = kept_ids([corr, other_attr], "谭景行晨练去哪儿？")
        self.assertEqual(ids, {"c-corr", "c-attr"})
        self.assertEqual(rm, [])


class TestAmbiguousReference(unittest.TestCase):
    def test_correction_without_person_removes_nothing(self):
        corr = rec("c-corr", "更正：该记录有误。",
                   "2023-04-21T07:05:11+00:00", scope="园艺修剪", pred="更正",
                   source="assistant")
        target = rec("c-t", "谭景行园艺修剪一般去折柳自行车行。",
                     "2023-04-16T09:00:00+00:00", scope="园艺修剪",
                     pred="活动地点", source="assistant")
        ids, rm = kept_ids([corr, target], "谭景行园艺修剪去哪儿？")
        self.assertEqual(ids, {"c-corr", "c-t"})
        self.assertEqual(rm, [])

    def test_correction_about_absent_person_removes_nothing(self):
        # The correction names a person who has no records in this
        # context; nothing here is its target.
        corr = rec("c-corr", "Correction: Greta Nilsen does cycling drill at Marigold Bakery; the earlier record was wrong.",
                   "2024-11-15T00:00:00+00:00", scope="cycling drill", pred="correction")
        target = rec("c-t", "Bruno Keller usually does cycling drill at Summit Climbing Gym.",
                     "2024-11-14T00:00:00+00:00", scope="cycling drill", pred="activity place")
        ids, rm = kept_ids([corr, target], "where does Bruno Keller go?")
        self.assertEqual(ids, {"c-corr", "c-t"})
        self.assertEqual(rm, [])


class TestMultiPersonMixed(unittest.TestCase):
    def setUp(self):
        self.corr = rec("c-corr", "更正：谭景行园艺修剪去听涛泳池，此前记录有误。",
                        "2023-04-21T07:05:11+00:00", scope="园艺修剪", pred="更正",
                        source="assistant")
        self.a_wrong = rec("c-a-wrong", "谭景行园艺修剪一般去折柳自行车行。",
                           "2023-04-16T09:00:00+00:00", scope="园艺修剪",
                           pred="活动地点", source="assistant")
        self.a_agree = rec("c-a-agree", "谭景行通常在听涛泳池园艺修剪。",
                           "2023-04-16T15:00:00+00:00", scope="园艺修剪",
                           pred="活动地点", source="assistant")
        self.b_wrong = rec("c-b-wrong", "范思远园艺修剪一般去汀兰棋社。",
                           "2023-04-16T09:00:00+00:00", scope="园艺修剪",
                           pred="活动地点", source="assistant")

    def test_only_the_corrected_persons_conflict_removed(self):
        ids, rm = kept_ids([self.corr, self.a_wrong, self.a_agree,
                            self.b_wrong], "谭景行园艺修剪去哪儿？")
        self.assertEqual(ids, {"c-corr", "c-a-agree", "c-b-wrong"})
        self.assertEqual([r["id"] for r in rm], ["c-a-wrong"])

    def test_validity_record_is_not_a_correction_target(self):
        val = rec("c-val", "谭景行的园艺修剪通行证有效期至2023年12月31日。",
                  "2023-04-16T09:00:00+00:00", scope="园艺修剪",
                  pred="有效期", source="assistant")
        ids, rm = kept_ids([self.corr, self.a_wrong, val],
                           "谭景行园艺修剪去哪儿？")
        self.assertIn("c-val", ids)
        self.assertEqual([r["id"] for r in rm], ["c-a-wrong"])


class TestUnclearCorrectionScope(unittest.TestCase):
    def test_correction_without_asserted_fact_removes_nothing(self):
        # Flags an error but never states the corrected value: the
        # correction's target cannot be identified -> keep everything.
        corr = rec("c-corr", "更正：谭景行的园艺修剪记录有误。",
                   "2023-04-21T07:05:11+00:00", scope="园艺修剪", pred="更正",
                   source="assistant")
        target = rec("c-t", "谭景行园艺修剪一般去折柳自行车行。",
                     "2023-04-16T09:00:00+00:00", scope="园艺修剪",
                     pred="活动地点", source="assistant")
        ids, rm = kept_ids([corr, target], "谭景行园艺修剪去哪儿？")
        self.assertEqual(ids, {"c-corr", "c-t"})
        self.assertEqual(rm, [])

    def test_en_correction_without_asserted_fact_removes_nothing(self):
        corr = rec("c-corr", "Correction: the earlier cycling drill record of Bruno Keller was wrong.",
                   "2024-11-15T00:00:00+00:00", scope="cycling drill", pred="correction")
        target = rec("c-t", "Bruno Keller usually does cycling drill at Summit Climbing Gym.",
                     "2024-11-14T00:00:00+00:00", scope="cycling drill", pred="activity place")
        ids, rm = kept_ids([corr, target], "where does Bruno Keller go?")
        self.assertEqual(ids, {"c-corr", "c-t"})
        self.assertEqual(rm, [])


class TestENRoleCorrection(unittest.TestCase):
    """English corrections whose asserted value is a lowercase role noun
    ('serves as convener', 'is the photo editor of') -- assertion
    extraction must still find it (eval2 E0074/E0176/E0388 shape)."""

    def setUp(self):
        self.corr = rec("c-corr", "Correction: Ingrid Halvorsen serves as convener in Hazelwood Swim Club; the earlier record was wrong.",
                        "2024-11-15T00:00:00+00:00", scope="Hazelwood Swim Club", pred="correction")
        self.wrong = rec("c-wrong", "Ingrid Halvorsen serves as sparring partner in Hazelwood Swim Club.",
                         "2024-11-14T00:00:00+00:00", scope="Hazelwood Swim Club", pred="role")
        self.agree = rec("c-agree", "Ingrid Halvorsen is the convener of Hazelwood Swim Club.",
                         "2024-11-14T00:00:00+00:00", scope="Hazelwood Swim Club", pred="role")

    def test_role_conflict_removed_agreeing_kept(self):
        ids, rm = kept_ids([self.corr, self.wrong, self.agree],
                           "what does Ingrid Halvorsen do in Hazelwood Swim Club?")
        self.assertEqual(ids, {"c-corr", "c-agree"})
        self.assertEqual([r["id"] for r in rm], ["c-wrong"])


class TestStanceCarriedOver(unittest.TestCase):
    def test_historical_question_keeps_marked_expired(self):
        old = rec("c-old", "白翎的宝蓝保温壶收在栖梧体育馆。（注：此记录已过期）",
                  "2024-02-17T00:00:00+00:00", scope="宝蓝保温壶", pred="存放地点")
        new = rec("c-new", "白翎把宝蓝保温壶放在青藤茶馆。",
                  "2024-06-09T00:00:00+00:00", scope="宝蓝保温壶", pred="存放地点")
        ids, rm = kept_ids([old, new], "白翎当时把宝蓝保温壶放在哪儿了？")
        self.assertEqual(ids, {"c-old", "c-new"})
        self.assertEqual(rm, [])

    def test_correction_still_applies_to_historical(self):
        # A correction says the record was WRONG (not merely outdated), so
        # it also governs historical questions.
        corr = rec("c-corr", "更正：谭景行园艺修剪去听涛泳池，此前记录有误。",
                   "2023-04-21T07:05:11+00:00", scope="园艺修剪", pred="更正",
                   source="assistant")
        wrong = rec("c-wrong", "谭景行园艺修剪一般去折柳自行车行。",
                    "2023-04-16T09:00:00+00:00", scope="园艺修剪",
                    pred="活动地点", source="assistant")
        ids, rm = kept_ids([corr, wrong], "谭景行当时园艺修剪去哪儿？")
        self.assertEqual(ids, {"c-corr"})
        self.assertEqual([r["id"] for r in rm], ["c-wrong"])


if __name__ == "__main__":
    unittest.main()
