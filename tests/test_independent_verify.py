"""Independent bystander verification (tools/independent_verify.py).

The verifier must agree with a healthy index -- and must catch each
planted divergence: a row silently deleted from the index, a tampered
value, a phantom row with no source record. These are the failure
shapes a self-check cannot see (the 2026-11-21 production incident
was a derived layer silently at 0 while records said 707).
"""

from __future__ import annotations

import importlib.util
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "compiler"))

from indexer import Index  # noqa: E402
from storage import render_records  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "independent_verify", REPO / "tools" / "independent_verify.py"
)
iv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(iv)


def make_record(claim_id, value, observed="2026-11-05T09:00:00"):
    return {
        "schema_version": 2,
        "claim_id": claim_id,
        "revision_id": f"r-{claim_id}",
        "event_id": f"e-{claim_id}",
        "kind": "fact",
        "scope": "global",
        "subject": "user",
        "predicate": "p",
        "value": value,
        "evidence": value,
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
        "observed_at": observed,
        "provenance": {"timestamp": observed, "record_id": "rm-x"},
    }


class IndependentVerifyTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        base = Path(self._td.name)
        self.mem_root = base / "memories"
        rows = [
            make_record("c-iv-1", "虚构：独立校验一号"),
            make_record("c-iv-2", "虚构：独立校验二号"),
            make_record("c-iv-3", "虚构：独立校验三号"),
        ]
        target = self.mem_root / "semantic" / "facts.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_records("# 事实\n", rows), encoding="utf-8")
        self.db_path = base / "index.db"
        idx = Index(self.db_path)
        try:
            idx.build(self.mem_root)
        finally:
            idx.close()

    def tearDown(self):
        self._td.cleanup()

    def tamper(self, sql, params=()):
        con = sqlite3.connect(str(self.db_path))
        try:
            con.execute(sql, params)
            con.commit()
        finally:
            con.close()

    def test_healthy_index_agrees(self):
        report = iv.verify(self.mem_root, self.db_path)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["source_total"], 3)
        self.assertEqual(report["index_total"], 3)
        self.assertEqual(report["missing_in_index"], [])
        self.assertEqual(report["phantom_in_index"], [])

    def test_deleted_row_is_caught(self):
        self.tamper("DELETE FROM records WHERE claim_id = 'c-iv-2'")
        report = iv.verify(self.mem_root, self.db_path)
        self.assertFalse(report["ok"])
        self.assertEqual(report["missing_in_index"], ["c-iv-2:r-c-iv-2"])

    def test_tampered_value_is_caught(self):
        self.tamper(
            "UPDATE records SET value = '虚构：被改过的二号' WHERE claim_id = 'c-iv-2'"
        )
        report = iv.verify(self.mem_root, self.db_path)
        self.assertFalse(report["ok"])
        self.assertEqual(report["value_mismatch"], ["c-iv-2:r-c-iv-2"])

    def test_phantom_row_is_caught(self):
        self.tamper(
            "INSERT INTO records (id, claim_id, revision_id, layer, status, value, content_hash)"
            " VALUES ('c-ghost:r-ghost', 'c-ghost', 'r-ghost', 'semantic', 'active', '虚构：幽灵', 'x')"
        )
        report = iv.verify(self.mem_root, self.db_path)
        self.assertFalse(report["ok"])
        self.assertEqual(report["phantom_in_index"], ["c-ghost:r-ghost"])

    def test_cli_exit_codes(self):
        script = str(REPO / "tools" / "independent_verify.py")
        args = [sys.executable, script, str(self.mem_root), str(self.db_path)]
        clean = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)
        self.tamper("DELETE FROM records WHERE claim_id = 'c-iv-1'")
        dirty = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(dirty.returncode, 1, dirty.stdout + dirty.stderr)


if __name__ == "__main__":
    unittest.main()
