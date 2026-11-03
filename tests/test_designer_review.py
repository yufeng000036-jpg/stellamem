"""Tests for compiler/designer_review.py.

Red-line focus: the Designer may propose but never apply on its own.
Synthetic data only; nothing touches real user memory.
"""

import copy
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

from designer_review import (
    CONFIRM_PHRASE,
    apply,
    approve,
    collect_hard_cases,
    propose,
    reject,
)
from skills import SkillBank, SkillGovernanceError

CASES = [
    {
        "id": "hc-1",
        "kind": "recall_failure",
        "query": "q1",
        "expected": "memory about project X",
        "got": "nothing",
        "notes": "",
    },
    {
        "id": "hc-2",
        "kind": "wrong_memory",
        "query": "q2",
        "expected": "fact B",
        "got": "fact A (wrong)",
        "notes": "",
    },
]


class CollectTests(unittest.TestCase):
    def test_accepts_valid_kinds(self):
        cases = collect_hard_cases(copy.deepcopy(CASES))
        self.assertEqual(len(cases), 2)

    def test_rejects_unknown_kind(self):
        bad = [{"id": "x", "kind": "nope", "query": "", "expected": "", "got": ""}]
        with self.assertRaises(ValueError):
            collect_hard_cases(bad)

    def test_input_not_mutated(self):
        original = copy.deepcopy(CASES)
        snapshot = copy.deepcopy(CASES)
        collect_hard_cases(original)
        self.assertEqual(original, snapshot)

    def test_empty_rejected(self):
        with self.assertRaises(ValueError):
            collect_hard_cases([])


class ProposeTests(unittest.TestCase):
    def test_proposals_are_pending_and_versioned(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "proposals.json"
            made = propose(collect_hard_cases(CASES), path)
            self.assertEqual(len(made), 2)  # one per kind
            for p in made:
                self.assertEqual(p["status"], "pending")
                self.assertEqual(p["version"], 1)
                self.assertIsNone(p["approved_by"])
                self.assertTrue(p["evidence_case_ids"])

    def test_propose_does_not_touch_bank(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "proposals.json"
            bank = SkillBank()
            before = sorted(s.name for s in bank.list())
            propose(collect_hard_cases(CASES), path)
            after = sorted(s.name for s in bank.list())
            self.assertEqual(before, after)


class ApproveTests(unittest.TestCase):
    def _proposals(self, tmp):
        path = Path(tmp) / "proposals.json"
        made = propose(collect_hard_cases(CASES), path)
        return path, made[0]["id"]

    def test_approve_needs_named_human(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, pid = self._proposals(tmp)
            with self.assertRaises(SkillGovernanceError):
                approve(path, pid, "", CONFIRM_PHRASE)

    def test_approve_needs_confirm_phrase(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, pid = self._proposals(tmp)
            with self.assertRaises(SkillGovernanceError):
                approve(path, pid, "宇枫", "yes")
            p = approve(path, pid, "宇枫", CONFIRM_PHRASE)
            self.assertEqual(p["status"], "approved")
            self.assertEqual(p["approved_by"], "宇枫")

    def test_reject(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, pid = self._proposals(tmp)
            p = reject(path, pid, "宇枫", reason="not useful")
            self.assertEqual(p["status"], "rejected")


class ApplyRedLineTests(unittest.TestCase):
    def _approved(self, tmp, human="宇枫"):
        path = Path(tmp) / "proposals.json"
        made = propose(collect_hard_cases(CASES), path)
        pid = made[0]["id"]
        approve(path, pid, human, CONFIRM_PHRASE)
        return path, pid

    def test_apply_pending_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "proposals.json"
            made = propose(collect_hard_cases(CASES), path)
            bank = SkillBank()
            with self.assertRaises(SkillGovernanceError):
                apply(path, made[0]["id"], bank, "宇枫")
            # bank unchanged
            with self.assertRaises(KeyError):
                bank.get(made[0]["skill"]["name"])

    def test_apply_by_different_human_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, pid = self._approved(tmp, human="宇枫")
            bank = SkillBank()
            with self.assertRaises(SkillGovernanceError):
                apply(path, pid, bank, "别人")

    def test_apply_by_same_human_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            overlay = Path(tmp) / "skills.json"
            path, pid = self._approved(tmp, human="宇枫")
            bank = SkillBank(overlay_path=overlay)
            p = apply(path, pid, bank, "宇枫")
            self.assertEqual(p["status"], "applied")
            skill = bank.get(p["skill"]["name"])
            self.assertTrue(skill.source.startswith("designer:"))

    def test_apply_twice_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, pid = self._approved(tmp, human="宇枫")
            bank = SkillBank()
            apply(path, pid, bank, "宇枫")
            with self.assertRaises(SkillGovernanceError):
                apply(path, pid, bank, "宇枫")


if __name__ == "__main__":
    unittest.main()
