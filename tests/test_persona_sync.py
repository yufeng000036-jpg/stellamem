"""Step 3 tests: persona sync preparation (no user-library writes).

Synthetic placeholder data only.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

import persona_sync as SY


def _ev(eid, status="reviewed", **extra):
    row = {
        "event_id": eid,
        "subject": "persona",
        "text": "占位已复核事件",
        "status": status,
        "reviewed_by": "测试复核人",
        "reviewed_at": "2026-11-02T00:00:00+00:00",
        "source": "测试平台",
    }
    row.update(extra)
    return row


class PersonaSyncTests(unittest.TestCase):
    def test_reviewed_event_approved(self):
        pack = SY.prepare_sync_pack([_ev("e1")], approver="测试批准人", confirmed_full_text=True)
        self.assertEqual(pack["status"], "approved")
        self.assertEqual(pack["candidate_count"], 1)
        self.assertFalse(pack["changed_anything"])
        self.assertFalse(pack["wrote_anything"])
        self.assertFalse(pack["user_library_write_implemented"])

    def test_pending_refused(self):
        with self.assertRaises(ValueError):
            SY.prepare_sync_pack(
                [_ev("e1", status="pending_confirmation")], approver="x", confirmed_full_text=True
            )

    def test_confirmed_only_refused(self):
        with self.assertRaises(ValueError):
            SY.prepare_sync_pack(
                [_ev("e1", status="confirmed")], approver="x", confirmed_full_text=True
            )

    def test_missing_reviewer_refused(self):
        with self.assertRaises(ValueError):
            SY.prepare_sync_pack(
                [_ev("e1", reviewed_by="")], approver="x", confirmed_full_text=True
            )

    def test_user_subject_refused(self):
        with self.assertRaises(ValueError):
            SY.prepare_sync_pack(
                [_ev("e1", subject="user")], approver="x", confirmed_full_text=True
            )

    def test_empty_text_refused(self):
        with self.assertRaises(ValueError):
            SY.prepare_sync_pack([_ev("e1", text="  ")], approver="x", confirmed_full_text=True)

    def test_full_text_confirmation_required(self):
        with self.assertRaises(ValueError):
            SY.prepare_sync_pack([_ev("e1")], approver="x", confirmed_full_text=False)

    def test_approver_required(self):
        with self.assertRaises(ValueError):
            SY.prepare_sync_pack([_ev("e1")], approver=" ", confirmed_full_text=True)

    def test_duplicate_id_aborts_batch(self):
        with self.assertRaises(ValueError):
            SY.prepare_sync_pack([_ev("e1"), _ev("e1")], approver="x", confirmed_full_text=True)

    def test_one_bad_aborts_all(self):
        with self.assertRaises(ValueError):
            SY.prepare_sync_pack(
                [_ev("e1"), _ev("e2", status="pending_confirmation")],
                approver="x",
                confirmed_full_text=True,
            )

    def test_dry_run_counts(self):
        report = SY.dry_run_report(
            [_ev("e1"), _ev("e2", status="confirmed"), _ev("e3", status="pending_confirmation")]
        )
        self.assertEqual(report["total_events"], 3)
        self.assertEqual(report["reviewed_and_valid"], 1)
        self.assertFalse(report["changed_anything"])

    def test_empty_pack_approved(self):
        pack = SY.prepare_sync_pack([], approver="x", confirmed_full_text=True)
        self.assertEqual(pack["candidate_count"], 0)


if __name__ == "__main__":
    unittest.main()
