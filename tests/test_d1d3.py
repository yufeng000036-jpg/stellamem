"""D1 (as_of_paged offset) and D3 (gate rotation thresholds) regressions.

Synthetic placeholder data only. No production data, no real paths.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

import gate as G
import search as S
from indexer import Index
from storage import render_records


def _seed_many(root: Path, n: int):
    rows = []
    for i in range(n):
        day = (i % 28) + 1
        month = 1 + (i // 28) % 11
        ts = f"2026-{month:02d}-{day:02d}T00:00:00+00:00"
        rows.append({
            "schema_version": 2, "claim_id": f"c-d1d3-{i:03d}",
            "revision_id": f"r-d1d3-{i:03d}", "event_id": f"e-d1d3-{i:03d}",
            "kind": "fact", "scope": "global", "subject": "测试用户A",
            "predicate": "偏好", "value": f"测试偏好内容{i:03d}",
            "evidence": f"测试偏好内容{i:03d}", "source": "user",
            "confidence": 0.9, "importance": 0.5, "sensitivity": "PRIVATE",
            "write_policy": "allow", "injection_policy": "recall",
            "evidence_verified": True, "status": "active", "relation": "NEW",
            "auto_write": True, "observed_at": ts,
            "provenance": {"timestamp": ts, "record_id": f"rm-d1d3-{i:03d}"},
        })
    target = root / "semantic" / "facts.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_records("# 测试事实\n", rows), encoding="utf-8")


class D1PagedOffsetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "memories"
        _seed_many(self.root, 150)
        self.idx = Index(base / "index.db")
        self.idx.build(self.root)
        self.moment = "2027-01-01T00:00:00+00:00"

    def tearDown(self):
        self.idx.close()
        self.tmp.cleanup()

    def test_limit_none_offset_applies(self):
        # D1: previously limit=None ignored offset entirely.
        full = S.as_of(self.idx.con, self.moment)
        self.assertEqual(len(full), 150)
        page = S.as_of_paged(self.idx.con, self.moment, limit=None, offset=50)
        self.assertEqual(page["total"], 150)
        self.assertEqual(page["returned"], 100)
        self.assertEqual(page["offset"], 50)
        self.assertIsNone(page["limit"])
        self.assertFalse(page["truncated"])
        self.assertEqual([r["claim_id"] for r in page["items"]],
                         [r["claim_id"] for r in full[50:]])

    def test_limit_none_offset_zero_is_full(self):
        page = S.as_of_paged(self.idx.con, self.moment, limit=None, offset=0)
        self.assertEqual(page["total"], 150)
        self.assertEqual(page["returned"], 150)
        self.assertFalse(page["truncated"])

    def test_limit_none_offset_at_and_beyond_total(self):
        page = S.as_of_paged(self.idx.con, self.moment, limit=None, offset=150)
        self.assertEqual(page["total"], 150)
        self.assertEqual(page["returned"], 0)
        self.assertFalse(page["truncated"])
        page = S.as_of_paged(self.idx.con, self.moment, limit=None, offset=200)
        self.assertEqual(page["total"], 150)
        self.assertEqual(page["returned"], 0)
        self.assertFalse(page["truncated"])

    def test_limit_with_offset_still_works(self):
        page = S.as_of_paged(self.idx.con, self.moment, limit=100, offset=100)
        self.assertEqual(page["returned"], 50)
        self.assertFalse(page["truncated"])
        page = S.as_of_paged(self.idx.con, self.moment, limit=100, offset=0)
        self.assertEqual(page["returned"], 100)
        self.assertTrue(page["truncated"])


class D3RotationThresholdTests(unittest.TestCase):
    def test_size_exactly_at_limit_rotates(self):
        # D3: thresholds are 'reached' (>=) for both size and lines.
        # Previously size used > while lines used >=, and docs said
        # 'exceeds', describing neither exactly.
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "gate_log.jsonl"
            log.write_bytes(b"x" * 1_048_576)
            self.assertTrue(G.InjectionGate(log_path=log).rotate_if_needed())

    def test_size_one_byte_below_limit_does_not_rotate(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "gate_log.jsonl"
            log.write_bytes(b"x" * 1_048_575)
            self.assertFalse(G.InjectionGate(log_path=log).rotate_if_needed())

    def test_lines_exactly_at_limit_rotates(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "gate_log.jsonl"
            log.write_text("line\n" * 10_000, encoding="utf-8")
            self.assertTrue(G.InjectionGate(log_path=log).rotate_if_needed())

    def test_lines_one_below_limit_does_not_rotate(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "gate_log.jsonl"
            log.write_text("line\n" * 9_999, encoding="utf-8")
            self.assertFalse(G.InjectionGate(log_path=log).rotate_if_needed())

    def test_rotation_still_keeps_at_most_three(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "gate_log.jsonl"
            for i in (1, 2, 3):
                Path(f"{log}.{i}").write_text(f"old{i}\n", encoding="utf-8")
            log.write_bytes(b"z" * 1_048_576)
            self.assertTrue(G.InjectionGate(log_path=log).rotate_if_needed())
            self.assertFalse(Path(f"{log}.4").exists())
            self.assertTrue(Path(f"{log}.3").exists())
            self.assertTrue(Path(f"{log}.1").exists())


if __name__ == "__main__":
    unittest.main()
