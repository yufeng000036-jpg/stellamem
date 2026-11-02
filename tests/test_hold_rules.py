"""Step 2 tests: namespace tagging hold rules (dry-run only).

Synthetic placeholder data only. No real records, no writes.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

import hold_rules as HR
import legacy_tags as LT


def _rec(cid, subject="测试主题", **extra):
    row = {"claim_id": cid, "subject": subject, "value": "占位内容", "status": "active"}
    row.update(extra)
    return row


class HoldRuleTests(unittest.TestCase):
    def test_role_system_held(self):
        rec = _rec("c1", role="assistant")
        self.assertEqual(HR.hold_reasons(rec, "user"), ["role_system"])

    def test_role_system_case_insensitive(self):
        rec = _rec("c1", role="System")
        self.assertIn("role_system", HR.hold_reasons(rec, "user"))

    def test_plain_user_record_not_held(self):
        rec = _rec("c1", "测试偏好")
        self.assertEqual(HR.hold_reasons(rec, "user"), [])

    def test_persona_wording_held_when_planned_user(self):
        rec = _rec("c1", "测试人格B的偏好")
        self.assertIn("persona_viewer_wording", HR.hold_reasons(rec, "user"))

    def test_persona_wording_not_held_when_planned_persona(self):
        rec = _rec("c1", "测试人格B的偏好")
        self.assertEqual(HR.hold_reasons(rec, "persona"), [])

    def test_viewer_wording_in_value_held(self):
        rec = _rec("c1", value="观众A常来")
        self.assertIn("persona_viewer_wording", HR.hold_reasons(rec, "user"))

    def test_both_reasons(self):
        rec = _rec("c1", "直播相关", role="system")
        self.assertEqual(HR.hold_reasons(rec, "user"), ["role_system", "persona_viewer_wording"])

    def test_hold_report_counts_and_sample(self):
        records = [
            _rec("c2", role="assistant"),
            _rec("c1", "测试人格B的偏好"),
            _rec("c3", "普通偏好"),
        ]
        plan = LT.plan_legacy_tags(records)
        report = HR.hold_report(records, plan, sample_size=1)
        self.assertEqual(report["hold_count"], 2)
        self.assertEqual(report["by_reason"]["role_system"], 1)
        self.assertEqual(report["by_reason"]["persona_viewer_wording"], 1)
        self.assertEqual(report["sample_size"], 1)
        self.assertEqual(report["sample"][0]["claim_id"], "c1")  # sorted sample
        self.assertFalse(report["changed_anything"])
        self.assertFalse(report["wrote_anything"])
        self.assertFalse(report["tagging_write_implemented"])

    def test_hold_report_does_not_mutate(self):
        records = [_rec("c1", role="assistant")]
        before = [dict(r) for r in records]
        HR.hold_report(records, LT.plan_legacy_tags(records))
        self.assertEqual(records, before)

    def test_empty_input(self):
        report = HR.hold_report([], LT.plan_legacy_tags([]))
        self.assertEqual(report["hold_count"], 0)
        self.assertEqual(report["total_records"], 0)

    def test_negative_sample_size_rejected(self):
        with self.assertRaises(ValueError):
            HR.hold_report([_rec("c1")], LT.plan_legacy_tags([_rec("c1")]), sample_size=-1)


if __name__ == "__main__":
    unittest.main()
