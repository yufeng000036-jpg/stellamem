"""Review round 3 regressions. Synthetic data only.

1. Preference-card history must survive a new store on the same path.
2. Record search, the local HTTP API, and the OpenClaw adapter must
   fail closed on a missing or unknown subject and must not return
   another subject's records.
3. Snapshot backup/restore killed with SIGKILL (TerminateProcess on
   Windows) must not publish a partial snapshot or leave a half-restored
   database. A case that cannot run on one OS is skipped with a reason.
"""

import json
import os
import sqlite3
import unittest.mock
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

import api
import search as S
import snapshots
from indexer import Index
from preference_summary import PreferenceCardStore
from storage import render_records

GOOD_EVIDENCE = [
    {"statement": "送小心心 x235", "evidence": "2026-11-03 直播日志礼物行"},
    {"statement": "常来直播间", "evidence": "2026-11-03 77 条事件记录"},
]
SUBJECT = "钟丽潭"
SHARED = "共享哨兵"


def _hard_kill(proc: subprocess.Popen) -> None:
    """SIGKILL on POSIX. On Windows, Process.kill is TerminateProcess."""
    proc.kill()


def _preference_state(store, subject):
    return {
        "current": store.get(subject),
        "history": store.history(subject),
        "active": store.list_active(),
    }


def _record(claim_id, namespace, value):
    return {
        "schema_version": 2,
        "claim_id": claim_id,
        "revision_id": claim_id + "-r",
        "event_id": claim_id + "-e",
        "kind": "fact",
        "scope": "global",
        "subject": namespace,
        "namespace": namespace,
        "predicate": "sentinel",
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
        "observed_at": "2026-11-01T00:00:00+00:00",
        "provenance": {"timestamp": "2026-11-01T09:00:00", "record_id": claim_id + "-raw"},
    }


def _build_index(root: Path, db_path: Path):
    (root / "semantic").mkdir(parents=True, exist_ok=True)
    records = [
        _record("c-user", "user", f"{SHARED}用户侧"),
        _record("c-persona", "persona", f"{SHARED}人格侧"),
    ]
    (root / "semantic" / "facts.md").write_text(
        render_records("# 事实\n", records), encoding="utf-8"
    )
    index = Index(db_path)
    index.build(root, include_daily=False)
    return index


def _project(root: Path, config: dict) -> str:
    adapter = Path(__file__).resolve().parents[1] / "adapters" / "openclaw" / "index.js"
    script = (
        "import { project } from "
        + json.dumps(adapter.as_uri())
        + ";\n"
        + "const out = await project(process.argv[1], 'sentinel preference', "
        + "JSON.parse(process.argv[2]));\n"
        + "process.stdout.write(out);\n"
    )
    proc = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            script,
            str(root),
            json.dumps(config),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout)
    return proc.stdout


def _openclaw_root() -> Path:
    root = Path(tempfile.mkdtemp(prefix="stellamem-r3-oc-"))
    core = root / "core"
    core.mkdir()
    data = [
        {
            "schema_version": 2,
            "claim_id": "c-user",
            "revision_id": "r-user",
            "status": "active",
            "kind": "preference",
            "subject": "user",
            "namespace": "user",
            "scope": "global",
            "predicate": "sentinel",
            "value": "user sentinel",
            "evidence": "user sentinel",
            "evidence_verified": True,
            "source": "user",
            "sensitivity": "PRIVATE",
            "write_policy": "allow",
            "injection_policy": "recall",
        },
        {
            "schema_version": 2,
            "claim_id": "c-persona",
            "revision_id": "r-persona",
            "status": "active",
            "kind": "preference",
            "subject": "persona",
            "namespace": "persona",
            "scope": "global",
            "predicate": "sentinel",
            "value": "persona sentinel",
            "evidence": "persona sentinel",
            "evidence_verified": True,
            "source": "user",
            "sensitivity": "PRIVATE",
            "write_policy": "allow",
            "injection_policy": "recall",
        },
    ]
    body = "<!-- stellamem:v2 -->\n" + "".join(
        "```stellamem\n" + json.dumps(item, ensure_ascii=False) + "\n```\n" for item in data
    )
    (core / "preferences.md").write_text(body, encoding="utf-8")
    return root


def _wal_db(
    root: Path,
    rows: list[tuple[str, str]],
    extra: tuple[str, str] | None = None,
    preserve_wal: bool = False,
):
    data = root / "stellamem-data"
    data.mkdir(parents=True, exist_ok=True)
    db = data / "index.db"
    (root / "config.json").write_text('{"round": 3}', encoding="utf-8")
    if preserve_wal:
        # os._exit skips sqlite's close-time checkpoint, so -wal/-shm stay on disk.
        script = r"""
import json, os, sqlite3, sys
from pathlib import Path
root = Path(sys.argv[1])
spec = json.loads(sys.argv[2])
db = root / "stellamem-data" / "index.db"
con = sqlite3.connect(db)
con.execute("PRAGMA journal_mode=WAL")
con.execute("PRAGMA wal_autocheckpoint=0")
con.execute(
    "CREATE TABLE records (id TEXT PRIMARY KEY, value TEXT, occurred_at TEXT, "
    "observed_at TEXT, blob BLOB)"
)
blob = os.urandom(512 * 1024)
for ident, value in spec["rows"]:
    con.execute(
        "INSERT INTO records VALUES (?,?,?,?,?)",
        (ident, value, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", blob),
    )
con.commit()
con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
extra = spec.get("extra")
if extra:
    con.execute(
        "INSERT INTO records VALUES (?,?,?,?,?)",
        (extra[0], extra[1], "2026-03-03T00:00:00Z", "2026-03-03T00:00:00Z", blob),
    )
    con.commit()
os._exit(0)
"""
        subprocess.run(
            [sys.executable, "-c", script, str(root), json.dumps({"rows": rows, "extra": extra})],
            check=True,
        )
        return db
    con = sqlite3.connect(db)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA wal_autocheckpoint=0")
    con.execute(
        "CREATE TABLE records (id TEXT PRIMARY KEY, value TEXT, occurred_at TEXT, "
        "observed_at TEXT, blob BLOB)"
    )
    blob = os.urandom(512 * 1024)
    for ident, value in rows:
        con.execute(
            "INSERT INTO records VALUES (?,?,?,?,?)",
            (ident, value, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", blob),
        )
    con.commit()
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    if extra is not None:
        con.execute(
            "INSERT INTO records VALUES (?,?,?,?,?)",
            (extra[0], extra[1], "2026-03-03T00:00:00Z", "2026-03-03T00:00:00Z", blob),
        )
        con.commit()
    con.close()
    return db


def _logical_rows(db: Path):
    if not db.exists():
        return None, "missing"
    con = sqlite3.connect(db)
    try:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        try:
            rows = [tuple(r) for r in con.execute("SELECT id, value FROM records ORDER BY id")]
        except sqlite3.OperationalError as exc:
            rows = [("error", str(exc))]
        return rows, integrity
    finally:
        con.close()


def _spawn(mode: str, root: Path, ready: Path, snapshot_name: str | None = None):
    env = os.environ.copy()
    env["STELLAMEM_SNAPSHOT_FAULT"] = mode
    env["STELLAMEM_SNAPSHOT_FAULT_READY"] = str(ready)
    cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--fault-child",
        mode,
        str(root),
    ]
    if snapshot_name:
        cmd.append(snapshot_name)
    log_path = root / f"{mode}.log"
    log = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT)
    return proc, log


def _wait_kill(proc, ready: Path, observe) -> str:
    deadline = time.time() + 60
    while time.time() < deadline:
        if ready.is_file():
            _hard_kill(proc)
            proc.wait(timeout=30)
            return "ready"
        if observe():
            _hard_kill(proc)
            proc.wait(timeout=30)
            return "observed"
        if proc.poll() is not None:
            return "exited"
        time.sleep(0.001)
    _hard_kill(proc)
    proc.wait(timeout=30)
    return "timeout"


def _fault_child(argv: list[str]) -> None:
    mode, root = argv[0], Path(argv[1])
    if mode == "backup-before-publish":
        snapshots.auto_snapshot(root, "fault-backup")
        return
    if mode == "restore-before-replace":
        snapshots.restore_snapshot(root, argv[2], confirm="RESTORE", prune_after=False)
        return
    raise SystemExit(f"unknown fault mode {mode}")


class PreferenceDurabilityTests(unittest.TestCase):
    def _store(self):
        tmp = tempfile.mkdtemp(prefix="stellamem-r3-pref-")
        path = os.path.join(tmp, "audit.jsonl")
        return PreferenceCardStore(audit_path=path), path

    def _fill(self, store):
        store.create(SUBJECT, "常客", GOOD_EVIDENCE)
        store.approve(SUBJECT, by="宇")
        store.update(SUBJECT, text="铁杆常客")
        store.rollback(SUBJECT, version=1, by="宇")

    def test_restart_reloads_versions_status_and_approvals(self):
        store, path = self._store()
        self._fill(store)
        before = _preference_state(store, SUBJECT)
        restarted = PreferenceCardStore(audit_path=path)
        self.assertEqual(_preference_state(restarted, SUBJECT), before)
        with self.assertRaises(ValueError):
            restarted.approve(SUBJECT, by="  ")

    def test_truncated_last_line_skips_fail_closed_and_keeps_earlier(self):
        store, path = self._store()
        self._fill(store)
        raw = Path(path).read_bytes()
        last_nl = raw.rstrip(b"\n").rfind(b"\n")
        self.assertGreater(last_nl, 0)
        torn = raw[: last_nl + 1] + raw[last_nl + 1 : last_nl + 11]
        Path(path).write_bytes(torn)
        with self.assertLogs("stellamem.preference_summary", level="WARNING") as logs:
            restarted = PreferenceCardStore(audit_path=path)
        self.assertTrue(any("fail-closed" in line for line in logs.output))
        texts = [item["text"] for item in restarted.history(SUBJECT)]
        self.assertEqual(texts, ["常客", "常客", "铁杆常客"])
        self.assertEqual(Path(path).read_bytes(), torn)
        self.assertEqual(restarted.get(SUBJECT)["status"], "pending")
        self.assertEqual(restarted.get(SUBJECT)["text"], "铁杆常客")

    def test_append_does_not_rewrite_earlier_bytes_and_fsyncs(self):
        store, path = self._store()
        store.create(SUBJECT, "常客", GOOD_EVIDENCE)
        before = Path(path).read_bytes()
        calls = []
        real = os.fsync

        def spy(fd):
            calls.append(fd)
            return real(fd)

        with unittest.mock.patch("os.fsync", spy):
            store.approve(SUBJECT, by="宇")
        after = Path(path).read_bytes()
        self.assertTrue(after.startswith(before), "history file was overwritten")
        self.assertTrue(calls, "append was not fsynced")


class SubjectIsolationTests(unittest.TestCase):
    def setUp(self):
        self._semantic = os.environ.get("STELLAMEM_SEMANTIC")
        os.environ["STELLAMEM_SEMANTIC"] = "0"
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "mem"
        self.index = _build_index(self.root, base / "index.db")
        self.con = self.index.con

    def tearDown(self):
        self.index.close()
        self.tmp.cleanup()
        if self._semantic is None:
            os.environ.pop("STELLAMEM_SEMANTIC", None)
        else:
            os.environ["STELLAMEM_SEMANTIC"] = self._semantic

    def test_search_missing_or_unknown_subject_returns_nothing(self):
        missing = S.search(self.con, SHARED, min_overlap=0.0)
        self.assertEqual(
            [hit["subject"] for hit in missing],
            [],
            f"missing subject returned {[hit['subject'] for hit in missing]}",
        )
        unknown = S.search(self.con, SHARED, namespace="not-a-subject", min_overlap=0.0)
        self.assertEqual(unknown, [])

    def test_search_cross_subject_hides_other_records_same_subject_works(self):
        hits = S.search(self.con, SHARED, namespace="user", min_overlap=0.0)
        subjects = {hit["subject"] for hit in hits}
        self.assertIn("user", subjects)
        self.assertNotIn("persona", subjects)
        persona = S.search(self.con, SHARED, namespace="persona", min_overlap=0.0)
        persona_subjects = {hit["subject"] for hit in persona}
        self.assertEqual(persona_subjects, {"persona"})

    def test_recall_record_channel_is_subject_scoped(self):
        missing = S.recall(self.con, SHARED, namespace=None, min_score=0.0)
        self.assertEqual(missing["items"], [])
        user = S.recall(self.con, SHARED, namespace="user", min_score=0.0)
        subjects = {item.get("subject") for item in user["items"]}
        self.assertIn("user", subjects)
        self.assertNotIn("persona", subjects)
        other = S.recall(self.con, SHARED, namespace="viewer", min_score=0.0)
        self.assertEqual(other["items"], [])


class ApiSubjectIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._semantic = os.environ.get("STELLAMEM_SEMANTIC")
        os.environ["STELLAMEM_SEMANTIC"] = "0"
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        cls.root = base / "mem"
        cls.index = _build_index(cls.root, base / "index.db")
        cls.httpd, cls.svc = api.make_server(cls.root, base / "index.db", port=0)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.index.close()
        cls.svc.index.close()
        cls.tmp.cleanup()
        if cls._semantic is None:
            os.environ.pop("STELLAMEM_SEMANTIC", None)
        else:
            os.environ["STELLAMEM_SEMANTIC"] = cls._semantic

    def call(self, method, path, body=None, headers=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Host", f"127.0.0.1:{self.port}")
        if data:
            req.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read()
                ctype = resp.headers.get("Content-Type", "")
                if ctype.startswith("application/x-ndjson"):
                    return resp.status, raw.decode("utf-8")
                return resp.status, json.loads(raw or b"{}")
        except urllib.error.HTTPError as exc:
            payload = exc.read() or b"{}"
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                parsed = {"raw": payload.decode("utf-8", "replace")}
            return exc.code, parsed

    def test_http_missing_or_unknown_subject_rejected(self):
        query = urllib.parse.quote(SHARED)
        cases = [
            ("GET", f"/v1/search?q={query}", None),
            ("GET", "/v1/timeline", None),
            ("GET", "/v1/memory/c-user", None),
            ("GET", "/v1/history/c-user", None),
            ("GET", "/v1/related/c-user:c-user-r", None),
            ("GET", "/v1/evidence/c-user-raw", None),
            ("GET", "/v1/export?ns=personal", None),
            ("POST", "/v1/recall", {"prompt": SHARED}),
            ("GET", f"/v1/search?q={query}&subject=not-a-subject", None),
        ]
        for method, path, body in cases:
            with self.subTest(path=path):
                status, payload = self.call(method, path, body)
                self.assertIn(status, (400, 403), payload)
                blob = (
                    payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
                )
                self.assertNotIn(f"{SHARED}用户侧", blob)
                self.assertNotIn(f"{SHARED}人格侧", blob)

    def test_http_cross_subject_hides_other_records(self):
        query = urllib.parse.quote(SHARED)
        status, data = self.call("GET", f"/v1/search?q={query}&subject=persona")
        self.assertEqual(status, 200, data)
        subjects = {item["subject"] for item in data["items"]}
        self.assertEqual(subjects, {"persona"})
        status, data = self.call(
            "POST",
            "/v1/recall",
            {"prompt": SHARED, "subject": "user"},
        )
        self.assertEqual(status, 200, data)
        subjects = {item.get("subject") for item in data["items"]}
        self.assertIn("user", subjects)
        self.assertNotIn("persona", subjects)
        status, data = self.call("GET", "/v1/timeline?subject=user")
        self.assertEqual(status, 200, data)
        self.assertEqual({item["subject"] for item in data["items"]}, {"user"})
        status, data = self.call("GET", "/v1/memory/c-user?subject=persona")
        self.assertEqual(status, 404, data)
        self.assertNotIn(f"{SHARED}用户侧", json.dumps(data, ensure_ascii=False))
        status, data = self.call("GET", "/v1/memory/c-user?subject=user")
        self.assertEqual(status, 200, data)
        self.assertEqual(data["claim_id"], "c-user")
        status, data = self.call("GET", "/v1/history/c-user?subject=persona")
        self.assertTrue(status in (200, 404), data)
        items = data.get("items") if isinstance(data, dict) else None
        self.assertFalse(items)
        status, data = self.call("GET", "/v1/history/c-user?subject=user")
        self.assertEqual(status, 200, data)
        self.assertGreaterEqual(len(data["items"]), 1)
        status, data = self.call("GET", "/v1/related/c-user:c-user-r?subject=user")
        self.assertEqual(status, 200, data)
        self.assertNotIn("persona", {item.get("subject") for item in data["items"]})
        status, body = self.call("GET", "/v1/export?ns=personal&subject=user")
        self.assertEqual(status, 200, body)
        exported = [json.loads(line) for line in body.splitlines() if line.strip()]
        self.assertTrue(exported)
        self.assertTrue(all(row.get("namespace") == "user" for row in exported))
        self.assertTrue(
            all("人格侧" not in json.dumps(row, ensure_ascii=False) for row in exported)
        )
        status, body = self.call("GET", "/v1/export?ns=persona&subject=persona")
        self.assertIn(status, (200, 404), body)
        if status == 200:
            self.assertNotIn(f"{SHARED}用户侧", body)

    def test_http_subject_header_and_query_must_agree(self):
        query = urllib.parse.quote(SHARED)
        status, data = self.call(
            "GET",
            f"/v1/search?q={query}&subject=user",
            headers={"X-Stellamem-Subject": "persona"},
        )
        self.assertIn(status, (400, 403), data)
        blob = json.dumps(data, ensure_ascii=False)
        self.assertNotIn(f"{SHARED}用户侧", blob)
        self.assertNotIn(f"{SHARED}人格侧", blob)


class OpenClawSubjectIsolationTests(unittest.TestCase):
    def test_missing_or_unknown_subject_returns_nothing(self):
        root = _openclaw_root()
        self.assertEqual(_project(root, {}), "")
        self.assertEqual(_project(root, {"requesterSubject": "not-a-subject"}), "")

    def test_cross_subject_hidden_same_subject_returned(self):
        root = _openclaw_root()
        user = _project(root, {"requesterSubject": "user"})
        self.assertIn("user sentinel", user)
        self.assertNotIn("persona sentinel", user)
        persona = _project(root, {"requesterSubject": "persona"})
        self.assertIn("persona sentinel", persona)
        self.assertNotIn("user sentinel", persona)


class SnapshotFaultTests(unittest.TestCase):
    def _root(self):
        return Path(tempfile.mkdtemp(prefix="stellamem-r3-snap-"))

    def _finish(self, proc, log):
        if proc.poll() is None:
            _hard_kill(proc)
            proc.wait(timeout=30)
        log.close()

    def test_kill_mid_backup_publishes_nothing_and_keeps_live_db(self):
        root = self._root()
        db = _wal_db(
            root, [(str(i), f"live-{i}") for i in range(24)], extra=("wal-only", "only-wal")
        )
        before, before_integrity = _logical_rows(db)
        self.assertEqual(before_integrity, "ok")
        self.assertIn(("wal-only", "only-wal"), before)
        ready = root / "ready-backup"
        proc, log = _spawn("backup-before-publish", root, ready)
        try:
            snap_dir = root / "system" / "snapshots"

            def observe():
                if not snap_dir.is_dir():
                    return False
                return any(not child.name.startswith(".") for child in snap_dir.iterdir())

            how = _wait_kill(proc, ready, observe)
            self.assertIn(how, {"ready", "observed"}, f"backup was not killed ({how})")
        finally:
            self._finish(proc, log)
        public = []
        if (root / "system" / "snapshots").is_dir():
            public = [
                child.name
                for child in (root / "system" / "snapshots").iterdir()
                if not child.name.startswith(".")
            ]
        self.assertEqual(public, [], f"published partial snapshot: {public}")
        self.assertEqual(snapshots.list_snapshots(root), [])
        rows, integrity = _logical_rows(db)
        self.assertEqual(integrity, "ok")
        self.assertEqual(rows, before)

    def test_kill_mid_restore_is_pre_or_post_and_integrity_ok(self):
        root = self._root()
        db = _wal_db(root, [(str(i), f"snap-{i}") for i in range(24)])
        dest = snapshots.auto_snapshot(root, "before-restore")
        con = sqlite3.connect(db)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA wal_autocheckpoint=0")
        con.execute(
            "INSERT INTO records VALUES (?,?,?,?,?)",
            ("after", "after-snap", "2026-04-04T00:00:00Z", "2026-04-04T00:00:00Z", os.urandom(32)),
        )
        con.commit()
        con.close()
        pre_rows, pre_integrity = _logical_rows(db)
        self.assertEqual(pre_integrity, "ok")
        self.assertIn(("after", "after-snap"), pre_rows)
        ready = root / "ready-restore"
        proc, log = _spawn("restore-before-replace", root, ready, dest.name)
        try:

            def observe():
                return not db.exists()

            how = _wait_kill(proc, ready, observe)
            self.assertIn(how, {"ready", "observed"}, f"restore was not killed ({how})")
        finally:
            self._finish(proc, log)
        self.assertTrue(db.exists(), "live DB missing after kill mid-restore")
        rows, integrity = _logical_rows(db)
        self.assertEqual(integrity, "ok", rows)
        snap_rows = [row for row in pre_rows if row[0] != "after"]
        self.assertIn(rows, (pre_rows, snap_rows), f"half-restored rows: {rows}")

    def test_reopen_after_killed_restore_handles_wal_sidecars(self):
        root = self._root()
        db = _wal_db(
            root,
            [(str(i), f"base-{i}") for i in range(16)],
            extra=("wal-only", "only-wal"),
            preserve_wal=True,
        )
        self.assertTrue(Path(str(db) + "-wal").exists(), "fixture did not leave a WAL sidecar")
        dest = snapshots.auto_snapshot(root, "sidecar")
        pre_rows, _integrity = _logical_rows(db)
        ready = root / "ready-reopen"
        proc, log = _spawn("restore-before-replace", root, ready, dest.name)
        try:

            def observe():
                return not db.exists()

            how = _wait_kill(proc, ready, observe)
            self.assertIn(how, {"ready", "observed"}, f"restore was not killed ({how})")
        finally:
            self._finish(proc, log)
        self.assertTrue(db.exists(), "live DB missing after kill; cannot reopen")
        first, first_integrity = _logical_rows(db)
        second, second_integrity = _logical_rows(db)
        self.assertEqual(first_integrity, "ok")
        self.assertEqual(second_integrity, "ok")
        self.assertEqual(first, second)
        self.assertEqual(first, pre_rows, f"reopen mixed WAL state: {first}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--fault-child":
        _fault_child(sys.argv[2:])
    else:
        unittest.main()
