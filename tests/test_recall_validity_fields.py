"""召回载荷必须把有效期带给读者（compiler/search.py + compiler/reading.py）。

索引里的记录带着 valid_to，但读者实际看到的注入文本行和结构化证据
此前都不带它：一条 status=active 而 valid_to 已过的记录会被当成
现行事实读走（拒答夹具 R2 的形状）。修后：文本行带「有效至」后缀，
evidence 条目带 valid_to 与 status；无 valid_to 的记录保持原样。
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "compiler"))

from indexer import Index  # noqa: E402
from storage import render_records  # noqa: E402
from search import recall  # noqa: E402


def make_record(claim_id, value, valid_to=None, observed="2026-11-05T09:00:00"):
    rec = {
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
    if valid_to is not None:
        rec["valid_to"] = valid_to
    return rec


def seed(mem_root: Path) -> None:
    rows = [
        make_record("c-v-past", "虚构：海盐咖啡馆的五折券", valid_to="2025-01-01"),
        make_record("c-v-future", "虚构：海盐咖啡馆的会员卡", valid_to="2099-12-31"),
        make_record("c-v-none", "虚构：海盐咖啡馆的营业时间"),
    ]
    target = mem_root / "semantic" / "facts.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_records("# 事实\n", rows), encoding="utf-8")


class RecallValidityFieldsTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        base = Path(self._td.name)
        self.mem_root = base / "memories"
        seed(self.mem_root)
        self.db_path = base / "index.db"
        idx = Index(self.db_path)
        try:
            idx.build(self.mem_root)
        finally:
            idx.close()
        self.con = sqlite3.connect(str(self.db_path))
        self.con.row_factory = sqlite3.Row
        self._env = mock.patch.dict(os.environ, {"STELLAMEM_SEMANTIC": "0"})
        self._env.start()
        self.result = recall(self.con, "海盐咖啡馆", subject="user")

    def tearDown(self):
        self._env.stop()
        self.con.close()
        self._td.cleanup()

    def test_all_three_records_recalled(self):
        ids = {it["claim_id"] for it in self.result["items"]}
        self.assertEqual(ids, {"c-v-past", "c-v-future", "c-v-none"})

    def test_text_lines_carry_valid_to(self):
        text = self.result["text"]
        self.assertIn("有效至 2025-01-01", text)
        self.assertIn("有效至 2099-12-31", text)

    def test_line_without_valid_to_has_no_suffix(self):
        for line in self.result["text"].splitlines():
            if "营业时间" in line:
                self.assertNotIn("有效至", line)
                break
        else:
            self.fail("营业时间 line missing from recall text")

    def test_evidence_carries_valid_to_and_status(self):
        ev = {e["claim_id"]: e for e in self.result["evidence"]}
        self.assertEqual(ev["c-v-past"]["valid_to"], "2025-01-01")
        self.assertEqual(ev["c-v-past"]["status"], "active")
        self.assertEqual(ev["c-v-future"]["valid_to"], "2099-12-31")
        self.assertIsNone(ev["c-v-none"]["valid_to"])
        self.assertEqual(ev["c-v-none"]["status"], "active")


if __name__ == "__main__":
    unittest.main()
