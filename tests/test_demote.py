"""90-day demotion: unhit records fall to the archive layer.

Hermes 4.2 (EigenFlux, 2026-11-07): 90 days unhit -> demoted to the
archive layer, no longer auto-injected. Synthetic data only.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

from memory_purge import demote_decayed, run_memory_purge

NOW = 1_800_000_000_000  # fixed "now" for the tests
DAY = 86_400_000


def _rec(rid, kind="fact", days_old=100, hits=0, status="active", pinned=False):
    return {
        "id": rid,
        "claim_id": rid,
        "kind": kind,
        "status": status,
        "updated_at_ms": NOW - days_old * DAY,
        "hit_count": hits,
        "pinned": pinned,
        "value": "fictional-" + rid,  # distinct values: identical values fold as duplicates
    }


class DemoteTests(unittest.TestCase):
    def test_demotes_90d_no_hit_record(self):
        recs = [_rec("c-old")]
        _, report = demote_decayed(recs, now_ms=NOW)
        self.assertEqual(report["demoted_count"], 1)
        self.assertEqual(report["demoted"], ["c-old"])
        self.assertEqual(recs[0]["status"], "archived")
        self.assertEqual(recs[0]["archived_reason"], "90d-no-hit")
        self.assertTrue(recs[0]["archived_at"])

    def test_recent_or_hit_records_stay_active(self):
        recs = [_rec("c-recent", days_old=10), _rec("c-hit", days_old=100, hits=5)]
        _, report = demote_decayed(recs, now_ms=NOW)
        self.assertEqual(report["demoted_count"], 0)
        self.assertEqual([r["status"] for r in recs], ["active", "active"])

    def test_pinned_and_core_kinds_never_demote(self):
        recs = [
            _rec("c-pinned", pinned=True),
            _rec("c-identity", kind="identity"),
            _rec("c-core", kind="user-core"),
        ]
        _, report = demote_decayed(recs, now_ms=NOW)
        self.assertEqual(report["demoted_count"], 0)
        self.assertEqual(report["skipped_pinned"], 1)
        self.assertEqual(report["skipped_core_kind"], 2)
        self.assertTrue(all(r["status"] == "active" for r in recs))

    def test_non_active_records_not_demoted(self):
        recs = [_rec("c-pending", status="pending")]
        _, report = demote_decayed(recs, now_ms=NOW)
        self.assertEqual(report["demoted_count"], 0)
        self.assertEqual(report["skipped_not_active"], 1)
        self.assertEqual(recs[0]["status"], "pending")

    def test_run_memory_purge_demote_flag(self):
        recs = [_rec("c-old"), _rec("c-recent", days_old=10)]
        retained, report = run_memory_purge(recs, now_ms=NOW, demote=True)
        self.assertEqual(report.demoted_count, 1)
        self.assertEqual(report.demoted_ids, ["c-old"])
        self.assertEqual(retained[0]["status"], "archived")

    def test_default_is_mark_only(self):
        recs = [_rec("c-old")]
        retained, report = run_memory_purge(recs, now_ms=NOW)
        self.assertEqual(report.decayed_archived_count, 1)
        # run_memory_purge works on copies: the mark lives on the retained record.
        self.assertTrue(retained[0].get("decayed_candidate"))
        self.assertEqual(report.demoted_count, 0)
        self.assertEqual(retained[0]["status"], "active")

    def test_demoted_is_a_subset_of_marked(self):
        # A pending old record is marked as a decay candidate but is
        # never demoted: demotion only applies to active records.
        recs = [_rec("c-old"), _rec("c-pending", status="pending")]
        _, report = run_memory_purge(recs, now_ms=NOW, demote=True)
        self.assertEqual(report.decayed_archived_count, 2)
        self.assertEqual(report.demoted_count, 1)
        self.assertEqual(report.demoted_ids, ["c-old"])


if __name__ == "__main__":
    unittest.main()
