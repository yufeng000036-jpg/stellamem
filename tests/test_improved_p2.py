"""Improved round P2 test: read-only pending screening report.
Synthetic placeholder data only; the report must change nothing."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

from pending_report import render_text, screening_report


def _pending(cid, kind, when):
    return {"claim_id": cid, "revision_id": f"r-{cid}", "kind": kind,
            "status": "pending", "observed_at": when, "value": "测试待审内容"}


class PendingReportTests(unittest.TestCase):
    def test_counts_duplicates_and_range(self):
        records = [
            _pending("c-test-1", "fact", "2026-01-01T00:00:00+00:00"),
            _pending("c-test-1", "fact", "2026-01-02T00:00:00+00:00"),
            _pending("c-test-2", "preference", "2026-02-01T00:00:00+00:00"),
            {"claim_id": "c-test-3", "kind": "fact", "status": "active",
             "observed_at": "2026-03-01T00:00:00+00:00", "value": "测试已激活内容"},
        ]
        original = [dict(r) for r in records]
        report = screening_report(records)
        self.assertEqual(report["total_pending"], 3)
        self.assertEqual(report["by_kind"], {"fact": 2, "preference": 1})
        self.assertEqual(report["duplicate_id_group_count"], 1)
        self.assertEqual(report["duplicate_extra_rows"], 1)
        self.assertEqual(report["oldest"], "2026-01-01T00:00:00+00:00")
        self.assertEqual(report["newest"], "2026-02-01T00:00:00+00:00")
        self.assertFalse(report["changed_anything"])
        self.assertFalse(report["activated_anything"])
        # Read-only: input records are untouched.
        self.assertEqual(records, original)
        self.assertIn("pending: 3", render_text(report))

    def test_empty_input(self):
        report = screening_report([])
        self.assertEqual(report["total_pending"], 0)
        self.assertIsNone(report["oldest"])
        self.assertIsNone(report["newest"])


if __name__ == "__main__":
    unittest.main()
