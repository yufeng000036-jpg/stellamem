"""A namespace-shaped subject ("group:<id>") must not enter through the
topic field. Namespaces are authority, stamped by the review pipeline;
a claim that smuggles a classification into `subject` used to be stored
silently as an unclassified record -- counted in totals, invisible to
governed recall. The funnel now rejects it loudly.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))
from claim_normalizer import normalize_claim


def claim(**over):
    base = dict(
        kind="fact",
        subject="Fictional Habitat",
        predicate="color",
        value="blue",
        evidence="blue",
    )
    base.update(over)
    return base


class GroupSubjectGuardTests(unittest.TestCase):
    def test_group_prefixed_subject_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_claim(claim(subject="group:12345"))

    def test_group_prefix_is_case_and_space_insensitive(self):
        for subject in ("Group:12345", "GROUP:12345", " group:12345 "):
            with self.assertRaises(ValueError, msg=subject):
                normalize_claim(claim(subject=subject))

    def test_bare_group_prefix_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_claim(claim(subject="group:"))

    def test_ordinary_subjects_are_unaffected(self):
        for subject in ("Fictional Habitat", "张三", "宇枫", "项目：复盘", "Note: friday"):
            c = normalize_claim(claim(subject=subject))
            self.assertEqual(c["subject"], subject.strip())

    def test_group_word_without_colon_is_a_topic(self):
        c = normalize_claim(claim(subject="group project"))
        self.assertEqual(c["subject"], "group project")


if __name__ == "__main__":
    unittest.main()
