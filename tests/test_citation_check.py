"""Citation verification (compiler/citation_check.py) + the reading
contract it verifies (compiler/reading.py): forged ids, empty
citations, misattributed citations, polarity flips, and the scaffold
text parity between the Python and JS copies.
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "compiler"))

import citation_check  # noqa: E402
import reading  # noqa: E402

CLAIM_A = {
    "claim_id": "c-aaaaaaaaaaaaaaaaaaaaaaaa",
    "text": "The user owns 3 signed baseballs.",
}
CLAIM_B = {
    "claim_id": "c-bbbbbbbbbbbbbbbbbbbbbbbb",
    "text": "The user collected stamps as a child.",
}
EVIDENCE = [CLAIM_A, CLAIM_B]


class VerifyAnswerTests(unittest.TestCase):
    def test_supported_numeric_citation(self):
        out = citation_check.verify_answer(
            "The user owns 3 signed baseballs [c-aaaaaaaaaaaaaaaaaaaaaaaa].", EVIDENCE
        )
        self.assertEqual(out["cited_n"], 1)
        self.assertEqual(out["known_n"], 1)
        self.assertEqual(out["supported_n"], 1)
        self.assertEqual(out["verifiable_rate"], 1.0)
        self.assertEqual(out["unknown_ids"], [])
        self.assertEqual(out["unsupported"], [])

    def test_forged_id_is_unknown(self):
        out = citation_check.verify_answer(
            "The user owns 3 signed baseballs [c-ffffffffffffffffffffffff].", EVIDENCE
        )
        self.assertEqual(out["cited_n"], 1)
        self.assertEqual(out["known_n"], 0)
        self.assertEqual(out["unknown_ids"], ["c-ffffffffffffffffffffffff"])
        self.assertEqual(out["verifiable_rate"], 0.0)

    def test_no_citations(self):
        out = citation_check.verify_answer("The user owns 3 signed baseballs.", EVIDENCE)
        self.assertEqual(out["cited_n"], 0)
        self.assertIsNone(out["verifiable_rate"])
        # The uncited factual sentence is still counted.
        self.assertEqual(out["uncited_assertions"], 1)

    def test_misattributed_citation_flags_number(self):
        # The number comes from claim A, but the answer cites claim B.
        out = citation_check.verify_answer(
            "The user owns 3 signed baseballs [c-bbbbbbbbbbbbbbbbbbbbbbbb].", EVIDENCE
        )
        self.assertEqual(out["supported_n"], 0)
        self.assertEqual(len(out["unsupported"]), 1)
        self.assertEqual(out["unsupported"][0]["claim_id"], "c-bbbbbbbbbbbbbbbbbbbbbbbb")
        self.assertTrue(out["unsupported"][0]["flags"])

    def test_wrong_number_against_right_claim_flags(self):
        out = citation_check.verify_answer(
            "The user owns 15 signed baseballs [c-aaaaaaaaaaaaaaaaaaaaaaaa].", EVIDENCE
        )
        self.assertEqual(out["supported_n"], 0)
        self.assertIn("numeric_mismatch", out["unsupported"][0]["flags"])

    def test_polarity_flip_flags(self):
        out = citation_check.verify_answer(
            "The user does not own 3 signed baseballs [c-aaaaaaaaaaaaaaaaaaaaaaaa].",
            EVIDENCE,
        )
        self.assertEqual(out["supported_n"], 0)
        self.assertIn("polarity_flip", out["unsupported"][0]["flags"])

    def test_bare_citation_is_known_but_unchecked(self):
        out = citation_check.verify_answer("[c-aaaaaaaaaaaaaaaaaaaaaaaa]", EVIDENCE)
        self.assertEqual(out["known_n"], 1)
        self.assertEqual(out["supported_n"], 0)
        self.assertIsNone(out["citations"][0]["supported"])
        self.assertEqual(out["verifiable_rate"], 0.0)

    def test_build_evidence_skips_id_less_extras(self):
        items = [
            {"id": "c-cccccccccccccccccccccccc", "kind": "fact", "value": "v", "occurred_at": "2024-01-01"},
            {"kind": "preference_summary", "value": "summary", "_usage_id": "pref:user"},
        ]
        ev = reading.build_evidence(items)
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["claim_id"], "c-cccccccccccccccccccccccc")
        self.assertEqual(ev[0]["note"], "")


class ScaffoldParityTests(unittest.TestCase):
    def test_python_and_js_scaffold_text_match(self):
        js = (ROOT / "adapters" / "openclaw" / "index.js").read_text(encoding="utf-8")
        m = re.search(
            r'READING_SCAFFOLD =\s*\n?\s*"([^"]*)"\s*\+\s*\n?\s*"([^"]*)"\s*\+\s*\n?\s*"([^"]*)";',
            js,
        )
        self.assertIsNotNone(m, "could not extract JS READING_SCAFFOLD")
        self.assertEqual("".join(m.groups()), reading.READING_SCAFFOLD)


if __name__ == "__main__":
    unittest.main()


class RecallReadingContractTests(unittest.TestCase):
    def test_recall_returns_structured_evidence_and_scaffold(self):
        import os
        import sqlite3
        import tempfile
        from unittest import mock

        sys.path.insert(0, str(ROOT))
        from indexer import Index  # noqa: E402
        from search import recall  # noqa: E402
        from storage import render_records  # noqa: E402

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"STELLAMEM_SEMANTIC": "0"}
        ):
            base = Path(tmp)
            mem_root = base / "memories"
            row = {
                "schema_version": 2,
                "claim_id": "c-dddddddddddddddddddddddd",
                "revision_id": "r-c-dddd",
                "event_id": "e-c-dddd",
                "kind": "fact",
                "scope": "global",
                "subject": "user",
                "predicate": "travel",
                "value": "User visited Paris in spring.",
                "evidence": "I visited Paris in spring.",
                "source": "user",
                "confidence": 0.9,
                "importance": 0.5,
                "sensitivity": "PRIVATE",
                "write_policy": "allow",
                "injection_policy": "recall",
                "evidence_verified": True,
                "status": "active",
                "relation": "NEW",
                "auto_write": True,
                "observed_at": "2024-04-01T00:00:00Z",
                "provenance": {"timestamp": "2024-04-01T00:00:00Z", "record_id": "rm-x"},
            }
            target = mem_root / "semantic" / "facts.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(render_records("# facts\n", [row]), encoding="utf-8")
            db_path = base / "index.db"
            idx = Index(db_path)
            try:
                idx.build(mem_root)
            finally:
                idx.close()
            con = sqlite3.connect(str(db_path))
            con.row_factory = sqlite3.Row
            try:
                res = recall(con, "Where did the user travel in spring?", subject="user")
            finally:
                con.close()
            self.assertEqual(res["reading_scaffold"], reading.READING_SCAFFOLD)
            self.assertEqual(len(res["evidence"]), 1)
            entry = res["evidence"][0]
            self.assertEqual(entry["claim_id"], "c-dddddddddddddddddddddddd")
            self.assertEqual(entry["value"], "User visited Paris in spring.")
            self.assertEqual(entry["note"], "")
