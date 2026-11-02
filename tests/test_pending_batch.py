"""Step 6 tests: pending batch plan + approval (no activation).

Synthetic placeholder data only.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

import pending_batch as PB


def _rec(cid, status="pending"):
    return {"claim_id": cid, "kind": "fact", "value": "占位内容", "status": status}


class PendingBatchTests(unittest.TestCase):
    def test_planned_batch_counts(self):
        plan = PB.plan_batch([_rec("c1"), _rec("c2")], {"c1": "keep", "c2": "drop"})
        self.assertEqual(plan["status"], "planned")
        self.assertEqual(plan["by_action"], {"keep": 1, "task": 0, "drop": 1})
        self.assertFalse(plan["changed_anything"])
        self.assertFalse(plan["activated_anything"])
        self.assertFalse(plan["apply_implemented"])

    def test_approved_batch(self):
        plan = PB.plan_batch([_rec("c1")], {"c1": "task"}, approver="测试批准人", confirmed=True, approve=True)
        self.assertEqual(plan["status"], "approved")
        self.assertEqual(plan["approver"], "测试批准人")

    def test_approve_requires_approver(self):
        with self.assertRaises(ValueError):
            PB.plan_batch([_rec("c1")], {"c1": "keep"}, approver=" ", confirmed=True, approve=True)

    def test_approve_requires_confirmed(self):
        with self.assertRaises(ValueError):
            PB.plan_batch([_rec("c1")], {"c1": "keep"}, approver="x", confirmed=False, approve=True)

    def test_non_pending_refused(self):
        with self.assertRaises(ValueError):
            PB.plan_batch([_rec("c1", status="active")], {"c1": "keep"})

    def test_unknown_id_refused(self):
        with self.assertRaises(ValueError):
            PB.plan_batch([_rec("c1")], {"c9": "keep"})

    def test_bad_action_refused(self):
        with self.assertRaises(ValueError):
            PB.plan_batch([_rec("c1")], {"c1": "delete"})

    def test_empty_batch(self):
        plan = PB.plan_batch([], {})
        self.assertEqual(plan["item_count"], 0)
        self.assertEqual(plan["by_action"], {"keep": 0, "task": 0, "drop": 0})

    def test_render_summary(self):
        plan = PB.plan_batch([_rec("c1")], {"c1": "keep"})
        self.assertIn("activated_anything: False", PB.render_summary(plan))


if __name__ == "__main__":
    unittest.main()
