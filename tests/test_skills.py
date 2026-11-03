"""Tests for compiler/skills.py (Skill Bank).

Synthetic data only; nothing touches real user memory.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

from skills import Skill, SkillBank, SkillGovernanceError


class RegistryTests(unittest.TestCase):
    def test_builtin_primitives_present(self):
        bank = SkillBank()
        for name in ("INSERT", "UPDATE", "DELETE", "SKIP"):
            self.assertEqual(bank.get(name).status, "active")

    def test_domain_skills_distilled_with_sources(self):
        bank = SkillBank()
        expected = {
            "extract_atomic_claims": "compiler/pipeline.py",
            "evidence_verify": "compiler/pipeline.py",
            "noise_filter": "compiler/claim_normalizer.py",
            "secret_gate": "compiler/security.py",
            "injection_screen": "compiler/security.py",
            "quarantine_not_delete": "compiler/quarantine.py",
            "chunk_budget_1200": "compiler/indexer.py",
        }
        for name, source_prefix in expected.items():
            skill = bank.get(name)
            self.assertTrue(skill.source.startswith(source_prefix), name)

    def test_all_skills_have_complete_fields(self):
        bank = SkillBank()
        for skill in bank.list():
            for attr in ("name", "version", "description", "when_to_use", "instructions"):
                self.assertTrue(getattr(skill, attr), "%s.%s" % (skill.name, attr))

    def test_get_unknown_raises(self):
        with self.assertRaises(KeyError):
            SkillBank().get("NOPE")

    def test_digest_stable(self):
        bank = SkillBank()
        self.assertEqual(bank.get("INSERT").digest(), bank.get("INSERT").digest())

    def test_roundtrip(self):
        s = SkillBank().get("SKIP")
        self.assertEqual(Skill.from_dict(s.to_dict()).digest(), s.digest())


class SelectionTests(unittest.TestCase):
    def test_extract_context_picks_extraction_skills(self):
        bank = SkillBank()
        picked = bank.select_skills(
            {"task": "extract", "tags": ["compile"], "text": "extract claims"}
        )
        names = [p["skill"] for p in picked]
        self.assertIn("extract_atomic_claims", names)

    def test_safety_net_always_on_for_extraction(self):
        bank = SkillBank()
        picked = bank.select_skills({"task": "extract daily notes", "tags": [], "text": ""})
        names = [p["skill"] for p in picked]
        for required in ("evidence_verify", "secret_gate", "injection_screen"):
            self.assertIn(required, names)

    def test_selection_audit_logged(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit = Path(tmp) / "audit.jsonl"
            bank = SkillBank(audit_path=audit)
            bank.select_skills({"task": "quarantine review", "tags": [], "text": ""})
            lines = audit.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            entry = json.loads(lines[0])
            self.assertEqual(entry["action"], "select")
            self.assertIn("quarantine_not_delete", entry["detail"]["picked"])


class GovernanceTests(unittest.TestCase):
    def _skill(self, name="test_skill_x"):
        return Skill(
            name=name,
            version="1.0.0",
            description="d",
            when_to_use="w",
            instructions="i",
        )

    def test_register_without_approver_refused(self):
        bank = SkillBank()
        with self.assertRaises(SkillGovernanceError):
            bank.register(self._skill())
        with self.assertRaises(SkillGovernanceError):
            bank.register(self._skill(), approved_by="   ")

    def test_register_with_approver_works_and_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            overlay = Path(tmp) / "skills.json"
            bank = SkillBank(overlay_path=overlay)
            bank.register(self._skill(), approved_by="宇枫")
            self.assertEqual(bank.get("test_skill_x").status, "active")
            # reload from overlay: still there
            bank2 = SkillBank(overlay_path=overlay)
            self.assertEqual(bank2.get("test_skill_x").status, "active")

    def test_reregister_keeps_history(self):
        bank = SkillBank()
        s1 = self._skill()
        bank.register(s1, approved_by="宇枫")
        s2 = self._skill()
        s2.version = "1.1.0"
        bank.register(s2, approved_by="宇枫")
        hist = bank.history("test_skill_x")
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["version"], "1.0.0")

    def test_deprecate_requires_approver(self):
        bank = SkillBank()
        with self.assertRaises(SkillGovernanceError):
            bank.deprecate("SKIP")
        bank.deprecate("SKIP", approved_by="宇枫")
        self.assertEqual(bank.get("SKIP").status, "deprecated")
        self.assertNotIn("SKIP", [s.name for s in bank.list("active")])

    def test_deprecated_skill_not_selected(self):
        bank = SkillBank()
        bank.deprecate("SKIP", approved_by="宇枫")
        picked = bank.select_skills({"task": "skip noise", "tags": [], "text": "skip this noise"})
        self.assertNotIn("SKIP", [p["skill"] for p in picked])


if __name__ == "__main__":
    unittest.main()
