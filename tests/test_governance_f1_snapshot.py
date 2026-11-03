"""F1 测试：写前自动快照 + 显式回滚。合成数据，不碰真实记忆。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

import snapshots


def _make_root():
    tmp = Path(tempfile.mkdtemp(prefix="stellamem-f1-"))
    (tmp / "stellamem-data").mkdir(parents=True)
    (tmp / "stellamem-data" / "index.db").write_bytes(b"fake-index-bytes-v1")
    (tmp / "config.json").write_text('{"token_budget": 30000}', encoding="utf-8")
    return tmp


class AutoSnapshotTests(unittest.TestCase):
    def test_auto_snapshot_captures_files_with_sha256(self):
        root = _make_root()
        dest = snapshots.auto_snapshot(root, "unit-test")
        self.assertTrue(dest.is_dir())
        mf = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(mf["label"], "unit-test")
        paths = {e["path"] for e in mf["files"]}
        self.assertIn("stellamem-data/index.db", paths)
        self.assertIn("config.json", paths)
        for e in mf["files"]:
            self.assertEqual(len(e["sha256"]), 64)
        self.assertEqual(len(mf["digest"]), 64)

    def test_auto_snapshot_skips_missing_files(self):
        root = Path(tempfile.mkdtemp(prefix="stellamem-f1-empty-"))
        dest = snapshots.auto_snapshot(root, "empty")
        mf = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(mf["files"], [])

    def test_auto_snapshot_writes_audit(self):
        root = _make_root()
        snapshots.auto_snapshot(root, "audit-check")
        audit = (root / "system" / "audit-log.jsonl").read_text(encoding="utf-8")
        self.assertIn("snapshot_auto", audit)
        self.assertIn("audit-check", audit)

    def test_prune_keeps_latest(self):
        root = _make_root()
        for i in range(3):
            snapshots.auto_snapshot(root, f"s{i}")
        res = snapshots.prune_snapshots(root, keep=2)
        self.assertEqual(len(res["removed"]), 1)
        self.assertEqual(len(snapshots.list_snapshots(root)), 2)


class RestoreTests(unittest.TestCase):
    def test_restore_requires_confirmation(self):
        root = _make_root()
        snapshots.auto_snapshot(root, "x")
        name = snapshots.list_snapshots(root)[0]["name"]
        with self.assertRaises(PermissionError):
            snapshots.restore_snapshot(root, name, confirm="")
        with self.assertRaises(PermissionError):
            snapshots.restore_snapshot(root, name, confirm="yes-please")

    def test_restore_roundtrip(self):
        root = _make_root()
        db = root / "stellamem-data" / "index.db"
        snapshots.auto_snapshot(root, "before")
        name = snapshots.list_snapshots(root)[0]["name"]
        db.write_bytes(b"corrupted-by-test")
        res = snapshots.restore_snapshot(root, name, confirm="RESTORE")
        self.assertTrue(res["ok"])
        self.assertEqual(db.read_bytes(), b"fake-index-bytes-v1")
        audit = (root / "system" / "audit-log.jsonl").read_text(encoding="utf-8")
        self.assertIn("snapshot_restore", audit)

    def test_restore_refuses_corrupted_snapshot(self):
        root = _make_root()
        snapshots.auto_snapshot(root, "bad")
        name = snapshots.list_snapshots(root)[0]["name"]
        # 篡改快照内文件，模拟损坏
        (Path(root) / "system" / "snapshots" / name / "config.json").write_text(
            "tampered", encoding="utf-8"
        )
        with self.assertRaises(ValueError):
            snapshots.restore_snapshot(root, name, confirm="RESTORE")

    def test_restore_missing_snapshot(self):
        root = _make_root()
        with self.assertRaises(FileNotFoundError):
            snapshots.restore_snapshot(root, "no-such-snap", confirm="RESTORE")


if __name__ == "__main__":
    unittest.main()
