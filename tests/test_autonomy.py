"""Autonomy levels L1-L4 + permission-boundary triple (LAAP, minimal).

Adapted from 太极's LAAP description (EigenFlux, 2026-11-07). The
positive/negative cases below ARE the fault-injection acceptance:
a level-up is only granted when both pass.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

import autonomy as A
import guardrail_receipt as R


def _boundary(**kw):
    base = {
        "capabilities": frozenset({"recall", "auto_approve", "demote"}),
        "budget": {"max_records_per_run": 100},
        "rollback": "inbox withdraw / status back to active",
        "revocation": "config pending_auto_approve_enabled=false",
    }
    base.update(kw)
    return A.Boundary(**base)


class LevelTableTests(unittest.TestCase):
    def test_reads_are_l1(self):
        for action in (
            "recall",
            "search",
            "read_record",
            "trace_deletion",
            "tracking_table",
            "nightly_settlement_report",
        ):
            self.assertEqual(A.required_level(action), 1)

    def test_proposals_are_l2(self):
        for action in ("propose_ingest", "propose_bridge", "mark_decayed", "draft_demotion"):
            self.assertEqual(A.required_level(action), 2)

    def test_bounded_execution_is_l3(self):
        for action in ("auto_approve", "demote", "nightly_settlement_write", "forget_record"):
            self.assertEqual(A.required_level(action), 3)

    def test_full_delegation_is_l4(self):
        for action in ("operate_account", "execute_brief"):
            self.assertEqual(A.required_level(action), 4)

    def test_unknown_action_rejected(self):
        with self.assertRaises(ValueError):
            A.required_level("fly_to_the_moon")


class CheckTests(unittest.TestCase):
    def test_higher_level_may_run_lower_actions(self):
        out = A.check(3, "recall")
        self.assertTrue(out["allowed"])
        self.assertTrue(R.verify_receipt(out["receipt"]))

    def test_lower_level_cannot_run_higher_action(self):
        # Negative case (fault injection): an L2 actor attempting an L3
        # action is denied, and the denial leaves a receipt.
        out = A.check(2, "auto_approve")
        self.assertFalse(out["allowed"])
        self.assertIn("requires L3", out["reason"])
        self.assertTrue(R.verify_receipt(out["receipt"]))
        self.assertEqual(out["receipt"]["decision"], "block")

    def test_action_outside_capability_set_denied(self):
        boundary = _boundary(capabilities=frozenset({"recall"}))
        out = A.check(3, "auto_approve", boundary)
        self.assertFalse(out["allowed"])
        self.assertIn("outside this grant's capability set", out["reason"])

    def test_l3_grant_without_rollback_or_revocation_denied(self):
        boundary = _boundary(rollback="", revocation="")
        out = A.check(3, "auto_approve", boundary)
        self.assertFalse(out["allowed"])
        self.assertIn("must state rollback and revocation", out["reason"])

    def test_complete_l3_grant_allows(self):
        out = A.check(3, "auto_approve", _boundary())
        self.assertTrue(out["allowed"])
        self.assertTrue(R.verify_receipt(out["receipt"]))

    def test_invalid_level_or_action_denied_not_crash(self):
        out = A.check(9, "recall")
        self.assertFalse(out["allowed"])
        out = A.check(1, "fly_to_the_moon")
        self.assertFalse(out["allowed"])


class LevelUpTests(unittest.TestCase):
    def test_level_up_needs_both_acceptances(self):
        # Positive only: refused. Negative only: refused. Both: granted.
        out = A.level_up(2, 3, positive_passed=True, negative_passed=False)
        self.assertFalse(out["granted"])
        self.assertIn("negative", out["reason"])
        out = A.level_up(2, 3, positive_passed=False, negative_passed=True)
        self.assertFalse(out["granted"])
        self.assertIn("positive", out["reason"])
        out = A.level_up(2, 3, positive_passed=True, negative_passed=True)
        self.assertTrue(out["granted"])
        self.assertTrue(R.verify_receipt(out["receipt"]))
        self.assertEqual(out["receipt"]["decision"], "allow")

    def test_level_up_is_not_a_downgrade(self):
        out = A.level_up(3, 3, positive_passed=True, negative_passed=True)
        self.assertFalse(out["granted"])
        out = A.level_up(3, 2, positive_passed=True, negative_passed=True)
        self.assertFalse(out["granted"])


if __name__ == "__main__":
    unittest.main()
