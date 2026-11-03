"""Review round 2 regressions (2026-11-04). Synthetic data only.

Items covered here:
1. WAL snapshot fail-closed (no copy2 fallback) and backup() restore.
2. Subject isolation for preference cards and patterns.
3. Token dashboard namespace from a real indexer-built database.
4. selector-audit treats an unknown subject as a failed namespace stage.
"""

import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

import search as S
import selector_audit as SA
import snapshots
import token_dashboard as TD


def _wal_records_db():
    """WAL index.db whose committed rows live in the -wal file (no checkpoint)."""
    root = Path(tempfile.mkdtemp(prefix="stellamem-r2-wal-"))
    (root / "stellamem-data").mkdir(parents=True)
    db = root / "stellamem-data" / "index.db"
    con = sqlite3.connect(db)
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA wal_autocheckpoint=0;")
    con.execute(
        "CREATE TABLE records (id TEXT PRIMARY KEY, value TEXT, occurred_at TEXT, observed_at TEXT)"
    )
    con.execute(
        "INSERT INTO records VALUES (?,?,?,?)",
        ("1", "committed-before-snap", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    con.commit()
    (root / "config.json").write_text("{}", encoding="utf-8")
    return root, db, con


def _read_values(db):
    con = sqlite3.connect(db)
    try:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        try:
            rows = [r[0] for r in con.execute("SELECT value FROM records ORDER BY id")]
        except sqlite3.OperationalError as exc:
            rows = [f"error:{exc}"]
        return rows, integrity
    finally:
        con.close()


def _empty_db():
    con = sqlite3.connect(":memory:")
    con.execute(
        "CREATE TABLE records (id TEXT PRIMARY KEY, claim_id TEXT, revision_id TEXT,"
        " layer TEXT NOT NULL, kind TEXT, subject TEXT, predicate TEXT, value TEXT,"
        " evidence TEXT, status TEXT, source TEXT, occurred_at TEXT, observed_at TEXT,"
        " record_id TEXT, session_id TEXT, file TEXT, content_hash TEXT,"
        " redacted INTEGER DEFAULT 0, grams TEXT, valid_to TEXT, expired_at TEXT)"
    )
    con.commit()
    return con


def _card(subject, text):
    return {
        "kind": "preference_summary",
        "subject": subject,
        "text": text,
        "evidence": [{"statement": "s1", "evidence": "e1"}],
        "version": 1,
        "status": "active",
    }


def _pattern(subject, suffix):
    return {
        "id": "pat-" + subject,
        "kind": "pattern",
        "status": "active",
        "subject": subject,
        "predicate": "出现",
        "value": f"{subject}常在晚上出现{suffix}",
        "provenance": ["ep1", "ep2"],
        "support": 2,
    }


class WalSnapshotFailClosedTests(unittest.TestCase):
    def test_backup_api_failure_on_wal_is_fail_closed(self):
        root, db, con = _wal_records_db()
        try:
            witness = root / "witness.db"
            shutil.copy2(db, witness)
            wcon = sqlite3.connect(witness)
            with self.assertRaises(sqlite3.OperationalError) as hazard:
                wcon.execute("SELECT value FROM records")
            wcon.close()
            self.assertIn("no such table", str(hazard.exception))

            real_sqlite = snapshots.sqlite3

            class _FailBackup:
                def __init__(self, real):
                    self._real = real

                def backup(self, *args, **kwargs):
                    raise sqlite3.OperationalError("backup forced failure")

                def close(self):
                    self._real.close()

                def __getattr__(self, name):
                    return getattr(self._real, name)

            class _FailSqlite:
                def connect(self, *args, **kwargs):
                    return _FailBackup(real_sqlite.connect(*args, **kwargs))

                def __getattr__(self, name):
                    return getattr(real_sqlite, name)

            snapshots.sqlite3 = _FailSqlite()
            try:
                try:
                    dest = snapshots.auto_snapshot(root, "boom")
                except Exception as exc:
                    msg = str(exc).lower()
                    self.assertTrue(
                        "refus" in msg
                        or "fallback" in msg
                        or "file-copy" in msg
                        or "file copy" in msg,
                        msg,
                    )
                    snap_root = root / "system" / "snapshots"
                    leftover = list(snap_root.rglob("index.db")) if snap_root.exists() else []
                    self.assertEqual(leftover, [])
                    audit_path = root / "system" / "audit-log.jsonl"
                    self.assertTrue(audit_path.is_file())
                    audit = audit_path.read_text(encoding="utf-8")
                    self.assertIn("snapshot_auto_failed", audit)
                    return
                snap_db = dest / "stellamem-data" / "index.db"
                sconn = sqlite3.connect(snap_db)
                try:
                    sconn.execute("SELECT value FROM records")
                    self.fail("backup failure returned a snapshot instead of failing closed")
                except sqlite3.OperationalError as exc:
                    self.fail(
                        "backup failure fell back to file copy and published a snapshot "
                        f"missing tables ({exc})"
                    )
                finally:
                    sconn.close()
            finally:
                snapshots.sqlite3 = real_sqlite
        finally:
            con.close()

    def test_manifest_records_row_count_and_latest_timestamp(self):
        root, _db, con = _wal_records_db()
        try:
            con.close()
            con = None
            dest = snapshots.auto_snapshot(root, "manifest-stats")
            manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
            entry = [e for e in manifest["files"] if e["path"].endswith("index.db")][0]
            self.assertEqual(entry["row_count"], 1)
            self.assertEqual(entry["latest_record_timestamp"], "2026-01-01T00:00:00Z")
            self.assertIn("records", entry["tables"])
        finally:
            if con is not None:
                con.close()

    def test_restore_open_wal_connection_then_backup_without_stale_wal(self):
        root, db, con = _wal_records_db()
        try:
            dest = snapshots.auto_snapshot(root, "before-open")
            con.execute(
                "INSERT INTO records VALUES (?,?,?,?)",
                ("2", "after-snap", "2026-02-02T00:00:00Z", "2026-02-02T00:00:00Z"),
            )
            con.commit()
            name = dest.name
            refused = False
            try:
                snapshots.restore_snapshot(root, name, confirm="RESTORE")
            except Exception as exc:
                self.assertRegex(str(exc).lower(), r"active connection")
                refused = True
                held = [r[0] for r in con.execute("SELECT value FROM records ORDER BY id")]
                self.assertEqual(held, ["committed-before-snap", "after-snap"])
            if not refused:
                rows, integrity = _read_values(db)
                self.fail(
                    "restore used file copy while a connection was open; "
                    f"rows={rows} integrity={integrity} "
                    "(stale -wal applied if after-snap is present)"
                )
            con.close()
            con = None

            real_copy2 = shutil.copy2

            def guard_copy2(src, dst, *args, **kwargs):
                src_s, dst_s = str(src), str(dst)
                if src_s.endswith(".db") or dst_s.endswith(".db"):
                    raise AssertionError(f"copy2 used for database restore: {src} -> {dst}")
                return real_copy2(src, dst, *args, **kwargs)

            shutil.copy2 = guard_copy2
            try:
                result = snapshots.restore_snapshot(root, name, confirm="RESTORE")
            finally:
                shutil.copy2 = real_copy2
            self.assertTrue(result["ok"])
            rows, integrity = _read_values(db)
            self.assertEqual(rows, ["committed-before-snap"])
            self.assertEqual(integrity, "ok")
            self.assertFalse(Path(str(db) + "-wal").exists(), "stale -wal still present")
            self.assertFalse(Path(str(db) + "-shm").exists(), "stale -shm still present")
            rows_again, integrity_again = _read_values(db)
            self.assertEqual(rows_again, ["committed-before-snap"])
            self.assertEqual(integrity_again, "ok")
        finally:
            if con is not None:
                con.close()

    def test_restore_row_count_mismatch_keeps_pre_restore_state(self):
        root, db, con = _wal_records_db()
        try:
            dest = snapshots.auto_snapshot(root, "count-check")
            con.execute(
                "INSERT INTO records VALUES (?,?,?,?)",
                ("2", "after-snap", "2026-02-02T00:00:00Z", "2026-02-02T00:00:00Z"),
            )
            con.commit()
            con.close()
            con = None
            manifest_path = dest / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for entry in manifest["files"]:
                if entry["path"].endswith("index.db"):
                    entry["row_count"] = 99
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            try:
                snapshots.restore_snapshot(root, dest.name, confirm="RESTORE")
            except Exception as exc:
                self.assertRegex(str(exc).lower(), r"row_count")
            else:
                rows, integrity = _read_values(db)
                self.fail(
                    "restore ignored manifest row_count and did not keep the "
                    f"pre-restore state; rows={rows} integrity={integrity}"
                )
            rows, integrity = _read_values(db)
            self.assertEqual(rows, ["committed-before-snap", "after-snap"])
            self.assertEqual(integrity, "ok")
        finally:
            if con is not None:
                con.close()


class SubjectIsolationRecallTests(unittest.TestCase):
    def _cards(self):
        return [
            _card("测试观众A", "测试观众A常在晚上出现"),
            _card("测试观众B", "测试观众B常在晚上出现"),
        ]

    def _patterns(self):
        return [_pattern("测试观众A", "甲"), _pattern("测试观众B", "乙")]

    def test_same_subject_recall_still_works(self):
        con = _empty_db()
        try:
            result = S.recall(
                con,
                "测试观众A晚上",
                preference_cards=self._cards(),
                patterns=self._patterns(),
                pref_subject="测试观众A",
                pref_quota=2,
                pattern_quota=2,
                min_score=0.0,
            )
        finally:
            con.close()
        subjects = {item.get("subject") for item in result["items"]}
        kinds = {item.get("kind") for item in result["items"]}
        self.assertIn("测试观众A", subjects)
        self.assertNotIn("测试观众B", subjects)
        self.assertIn("preference_summary", kinds)
        self.assertIn("pattern", kinds)

    def test_cross_subject_cards_and_patterns_blocked(self):
        con = _empty_db()
        try:
            result = S.recall(
                con,
                "测试观众晚上",
                preference_cards=self._cards(),
                patterns=self._patterns(),
                pref_subject="测试观众A",
                pref_quota=4,
                pattern_quota=4,
                min_score=0.0,
            )
        finally:
            con.close()
        subjects = {item.get("subject") for item in result["items"]}
        self.assertIn("测试观众A", subjects)
        self.assertNotIn("测试观众B", subjects)

    def test_omitted_subject_blocks_cards_and_patterns(self):
        con = _empty_db()
        try:
            result = S.recall(
                con,
                "测试观众A晚上",
                preference_cards=self._cards(),
                patterns=self._patterns(),
                pref_quota=4,
                pattern_quota=4,
                min_score=0.0,
            )
        finally:
            con.close()
        kinds = {item.get("kind") for item in result["items"]}
        self.assertNotIn("preference_summary", kinds)
        self.assertNotIn("pattern", kinds)
        self.assertEqual(result["items"], [])

    def test_trusted_request_context_scopes_extras(self):
        con = _empty_db()
        try:
            with S.request_subject("测试观众A"):
                result = S.recall(
                    con,
                    "测试观众A晚上",
                    preference_cards=self._cards(),
                    patterns=self._patterns(),
                    pref_subject="测试观众B",
                    pref_quota=4,
                    pattern_quota=4,
                    min_score=0.0,
                )
        finally:
            con.close()
        subjects = {item.get("subject") for item in result["items"]}
        self.assertEqual(subjects, {"测试观众A"})


class IndexerDashboardNamespaceTests(unittest.TestCase):
    def test_dashboard_uses_canonical_namespace_from_indexer_db(self):
        from indexer import Index
        from storage import render_records

        tmp = Path(tempfile.mkdtemp(prefix="stellamem-r2-dash-"))
        mem = tmp / "mem"
        (mem / "semantic").mkdir(parents=True)
        records = [
            {
                "schema_version": 2,
                "claim_id": "c-viewer",
                "revision_id": "r1",
                "kind": "fact",
                "subject": "测试观众A",
                "namespace": "viewer",
                "predicate": "gift",
                "value": "小心心",
                "evidence": "合成日志",
                "status": "active",
                "provenance": {"timestamp": "2026-01-02T03:04:05Z"},
            },
            {
                "schema_version": 2,
                "claim_id": "c-persona",
                "revision_id": "r2",
                "kind": "fact",
                "subject": "测试主播",
                "namespace": "persona",
                "predicate": "style",
                "value": "慢热",
                "evidence": "合成日志",
                "status": "active",
                "provenance": {"timestamp": "2026-01-03T03:04:05Z"},
            },
        ]
        (mem / "semantic" / "facts.md").write_text(
            render_records("# 事实\n", records), encoding="utf-8"
        )
        db = tmp / "index.db"
        index = Index(db)
        try:
            index.build(mem, include_daily=False)
        finally:
            index.close()
        con = sqlite3.connect(db)
        columns = [row[1] for row in con.execute("PRAGMA table_info(records)")]
        con.close()
        self.assertIn("namespace", columns)
        board = TD.dashboard(db)
        self.assertEqual(board["by_namespace"].get("viewer"), 1)
        self.assertEqual(board["by_namespace"].get("persona"), 1)
        self.assertNotIn("(none)", board["by_namespace"])
        self.assertNotIn("(unclassified)", board["by_namespace"])
        labels = {row["namespace"] for row in board["top_consumers"]}
        self.assertEqual(labels, {"viewer", "persona"})


def _audit_db():
    tmp = Path(tempfile.mkdtemp(prefix="stellamem-r2-audit-"))
    db = tmp / "index.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE records (id TEXT PRIMARY KEY, claim_id TEXT, revision_id TEXT,"
        " layer TEXT NOT NULL, kind TEXT, subject TEXT, predicate TEXT, value TEXT,"
        " evidence TEXT, status TEXT, source TEXT, occurred_at TEXT, observed_at TEXT,"
        " record_id TEXT, session_id TEXT, file TEXT, content_hash TEXT,"
        " redacted INTEGER DEFAULT 0, grams TEXT, valid_to TEXT, expired_at TEXT)"
    )
    con.execute(
        "INSERT INTO records (id, claim_id, layer, kind, subject, value, status)"
        " VALUES (?,?,?,?,?,?,?)",
        ("c-free", "c-free", "memory", "fact", "测试观众A", "测试诊断关键词", "active"),
    )
    con.execute(
        "INSERT INTO records (id, claim_id, layer, kind, subject, value, status)"
        " VALUES (?,?,?,?,?,?,?)",
        ("c-user", "c-user", "memory", "fact", "user", "测试诊断关键词 unique", "active"),
    )
    con.commit()
    con.close()
    return db


class SelectorAuditNamespaceTests(unittest.TestCase):
    def test_unknown_subject_fails_namespace_stage(self):
        db = _audit_db()
        result = SA.audit(db, "c-free", "测试诊断关键词", min_score=0.0)
        stage = [item for item in result["stages"] if item["stage"] == "namespace"][0]
        self.assertFalse(stage["pass"])
        self.assertEqual(stage.get("namespace"), "unknown")
        self.assertEqual(result["verdict"], "blocked_by_namespace")

    def test_cli_namespace_and_subject_flags(self):
        db = _audit_db()
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            SA.main(
                [
                    "--db",
                    str(db),
                    "--id",
                    "c-user",
                    "--prompt",
                    "测试诊断关键词",
                    "--namespace",
                    "persona",
                    "--min-score",
                    "0",
                ]
            )
        blocked = json.loads(buf.getvalue())
        self.assertEqual(blocked["verdict"], "blocked_by_namespace")

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            SA.main(
                [
                    "--db",
                    str(db),
                    "--id",
                    "c-user",
                    "--query",
                    "测试诊断关键词 unique",
                    "--subject",
                    "user",
                    "--min-score",
                    "0",
                ]
            )
        allowed = json.loads(buf.getvalue())
        self.assertEqual(allowed["verdict"], "injected")


if __name__ == "__main__":
    unittest.main()
