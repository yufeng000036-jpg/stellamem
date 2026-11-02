"""Improved round P0 tests. Synthetic placeholder data only."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

import gate as G
import reliability as R
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
            "schema_version": 2, "claim_id": f"c-test-{i:03d}", "revision_id": f"r-test-{i:03d}",
            "event_id": f"e-test-{i:03d}", "kind": "fact", "scope": "global",
            "subject": "测试用户A", "predicate": "偏好", "value": f"测试偏好内容{i:03d}",
            "evidence": f"测试偏好内容{i:03d}", "source": "user", "confidence": 0.9,
            "importance": 0.5, "sensitivity": "PRIVATE", "write_policy": "allow",
            "injection_policy": "recall", "evidence_verified": True, "status": "active",
            "relation": "NEW", "auto_write": True, "observed_at": ts,
            "provenance": {"timestamp": ts, "record_id": f"rm-test-{i:03d}"},
        })
    target = root / "semantic" / "facts.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_records("# 测试事实\n", rows), encoding="utf-8")


class AsOfTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "memories"
        _seed_many(self.root, 150)
        self.idx = Index(base / "index.db")
        self.idx.build(self.root)

    def tearDown(self):
        self.idx.close()
        self.tmp.cleanup()

    def test_default_does_not_truncate(self):
        # Old default limit=100 would return exactly 100 of 150.
        out = S.as_of(self.idx.con, "2027-01-01T00:00:00+00:00")
        self.assertEqual(len(out), 150)

    def test_explicit_limit_still_works(self):
        out = S.as_of(self.idx.con, "2027-01-01T00:00:00+00:00", limit=10)
        self.assertEqual(len(out), 10)

    def test_paged_reports_truncation(self):
        page = S.as_of_paged(self.idx.con, "2027-01-01T00:00:00+00:00", limit=100)
        self.assertEqual(page["total"], 150)
        self.assertEqual(page["returned"], 100)
        self.assertTrue(page["truncated"])
        page2 = S.as_of_paged(self.idx.con, "2027-01-01T00:00:00+00:00", limit=100, offset=100)
        self.assertEqual(page2["returned"], 50)
        self.assertFalse(page2["truncated"])
        full = S.as_of_paged(self.idx.con, "2027-01-01T00:00:00+00:00", limit=None)
        self.assertEqual(full["total"], 150)
        self.assertFalse(full["truncated"])


class GateLowScoreTests(unittest.TestCase):
    def test_blocks_low_score_non_weak(self):
        g = G.InjectionGate()
        bundle = {"items": [
            {"text": "测试强信号", "overlap": 0.8, "semantic": 0.7, "score": 0.9, "weak": False},
            {"text": "测试无信号", "overlap": 0, "semantic": 0.2, "score": 0.05, "weak": False},
        ]}
        out = g.decide(bundle, query="测试查询")
        self.assertEqual(out["decision"], "inject_partial")
        self.assertEqual(len(out["inject"]), 1)
        self.assertEqual(len(out["blocked"]), 1)
        self.assertEqual(out["blocked_low_score"], 1)

    def test_blocks_overlap_zero_semantic_low(self):
        g = G.InjectionGate()
        bundle = {"items": [{"text": "测试无信号", "overlap": 0, "semantic": 0.3, "weak": False}]}
        out = g.decide(bundle, query="测试查询")
        self.assertEqual(out["decision"], "empty")
        self.assertEqual(out["text"], "")

    def test_allow_weak_marks_low_confidence(self):
        g = G.InjectionGate()
        bundle = {"items": [{"text": "测试无信号", "overlap": 0, "semantic": 0.2, "weak": False}]}
        out = g.decide(bundle, query="测试查询", allow_weak=True)
        self.assertEqual(len(out["inject"]), 1)
        self.assertTrue(out["inject"][0]["low_confidence"])
        self.assertIn("弱信号", out["inject"][0]["text"])

    def test_strong_still_injects(self):
        g = G.InjectionGate()
        bundle = {"items": [{"text": "测试强信号", "overlap": 0.9, "semantic": 0.8, "score": 0.9}]}
        out = g.decide(bundle, query="测试查询")
        self.assertEqual(out["decision"], "inject")


class GateRotationTests(unittest.TestCase):
    def test_rotation_on_size(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "gate_log.jsonl"
            log.write_text("x\n" * 2000 + "y" * 1_100_000, encoding="utf-8")
            g = G.InjectionGate(log_path=log)
            out = g.decide({"items": [{"text": "测试强信号", "overlap": 1.0, "semantic": 1.0}]},
                           query="测试查询")
            self.assertEqual(out["decision"], "inject")
            self.assertTrue((Path(str(log) + ".1")).exists())
            # Current write is never lost: new log holds only the new entry.
            lines = log.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)

    def test_rotation_keeps_at_most_three(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "gate_log.jsonl"
            for i in (1, 2, 3):
                Path(f"{log}.{i}").write_text(f"old{i}\n", encoding="utf-8")
            log.write_text("z" * 2000000, encoding="utf-8")
            g = G.InjectionGate(log_path=log)
            g.decide({"items": [{"text": "测试强信号", "overlap": 1.0, "semantic": 1.0}]},
                     query="测试查询")
            self.assertFalse(Path(f"{log}.4").exists())
            self.assertTrue(Path(f"{log}.3").exists())
            self.assertTrue(Path(f"{log}.1").exists())

    def test_rotation_on_line_count(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "gate_log.jsonl"
            log.write_text("line\n" * 10000, encoding="utf-8")
            g = G.InjectionGate(log_path=log)
            self.assertTrue(g.rotate_if_needed())


class SnapshotDuplicateTests(unittest.TestCase):
    def test_helper_detects_duplicates(self):
        with self.assertRaises(ValueError):
            R._check_duplicate_names(["a.txt", "b.txt", "a.txt"])
        self.assertEqual(R._check_duplicate_names(["a.txt", "b.txt"]), ["a.txt", "b.txt"])

    def test_snapshot_fails_on_duplicate_names(self):
        # rglob cannot produce a duplicate rel on a normal filesystem,
        # so the duplicate path is exercised by patching rglob to yield
        # the same file twice -- the detection code under test is the
        # same code snapshot() runs before writing any zip.
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "mem"
            root.mkdir()
            f = root / "a.txt"
            f.write_text("测试内容", encoding="utf-8")
            dest = Path(td) / "snap.zip"
            with patch.object(Path, "rglob", lambda self, pattern="*": [f, f]):
                with self.assertRaises(ValueError):
                    R.snapshot(root, dest)
            self.assertFalse(dest.exists())

    def test_snapshot_normal_still_works(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "mem"
            (root / "core").mkdir(parents=True)
            (root / "core" / "a.md").write_text("测试内容", encoding="utf-8")
            dest = Path(td) / "snap.zip"
            manifest = R.snapshot(root, dest)
            self.assertEqual(len(manifest["files"]), 1)
            self.assertTrue(dest.exists())


if __name__ == "__main__":
    unittest.main()
