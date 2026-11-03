"""回归测试：外部审查指出的 4 个逻辑问题 + ruff 修复（2026-11-03）。

全部使用合成占位数据（测试观众A/B），不碰真实记忆、不连生产。
"""

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

import snapshots


def _make_root_with_wal_db():
    """root 下放一个 WAL 模式的 index.db 并写入提交数据。"""
    tmp = Path(tempfile.mkdtemp(prefix="stellamem-wal-"))
    (tmp / "stellamem-data").mkdir(parents=True)
    db = tmp / "stellamem-data" / "index.db"
    con = sqlite3.connect(db)
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    con.execute("INSERT INTO t (v) VALUES ('wal-committed-row')")
    con.commit()
    con.close()
    (tmp / "config.json").write_text("{}", encoding="utf-8")
    return tmp


class WalSnapshotTests(unittest.TestCase):
    def test_snapshot_keeps_wal_committed_rows(self):
        # WAL 下提交后只拷主文件会丢数据；快照必须用在线备份。
        root = _make_root_with_wal_db()
        dest = snapshots.auto_snapshot(root, "wal-test")
        snap_db = dest / "stellamem-data" / "index.db"
        con = sqlite3.connect(snap_db)
        try:
            rows = con.execute("SELECT v FROM t").fetchall()
        finally:
            con.close()
        self.assertEqual(rows, [("wal-committed-row",)])

    def test_snapshot_manifest_records_backup_method(self):
        root = _make_root_with_wal_db()
        dest = snapshots.auto_snapshot(root, "wal-method")
        mf = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
        entry = [e for e in mf["files"] if e["path"] == "stellamem-data/index.db"][0]
        self.assertEqual(entry["method"], "sqlite-backup")

    def test_snapshot_still_skips_missing_files(self):
        root = Path(tempfile.mkdtemp(prefix="stellamem-wal-empty-"))
        dest = snapshots.auto_snapshot(root, "empty")
        mf = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(mf["files"], [])


def _empty_db():
    tmp = Path(tempfile.mkdtemp(prefix="stellamem-review-"))
    con = sqlite3.connect(tmp / "index.db")
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


class PrefSubjectFilterTests(unittest.TestCase):
    def _recall(self, **kw):
        import search as S

        con = _empty_db()
        try:
            cards = [
                _card("测试观众A", "测试观众A常在晚上出现"),
                _card("测试观众B", "测试观众B常在下午出现"),
            ]
            return S.recall(con, "测试观众A晚上", preference_cards=cards, **kw)
        finally:
            con.close()

    def test_pref_subject_filters_other_subjects(self):
        r = self._recall(pref_subject="测试观众A")
        subjects = {i.get("subject") for i in r["items"]}
        self.assertIn("测试观众A", subjects)
        self.assertNotIn("测试观众B", subjects)

    def test_no_subject_keeps_old_behavior(self):
        # 不传 pref_subject = 不过滤（保持旧行为，调用方自行负责）
        r = self._recall()
        subjects = {i.get("subject") for i in r["items"]}
        self.assertIn("测试观众A", subjects)
        self.assertIn("测试观众B", subjects)

    def test_match_pref_cards_direct_subject_filter(self):
        import search as S

        cards = [_card("测试观众A", "甲"), _card("测试观众B", "乙")]
        grams = S.bigrams("测试观众")
        out = S._match_pref_cards(grams, cards, 10, 0.0, subject="测试观众B")
        self.assertEqual([c["subject"] for c in out], ["测试观众B"])


class _FakeTracker:
    def __init__(self):
        self.hits = []

    def record_hit(self, usage_id, kind=""):
        self.hits.append((usage_id, kind))


class UsageCountAfterBudgetTests(unittest.TestCase):
    def _recall(self, budget, **kw):
        import search as S

        con = _empty_db()
        try:
            cards = [_card("测试观众A", "测试观众A常在晚上出现")]
            tracker = _FakeTracker()
            r = S.recall(
                con,
                "测试观众A晚上",
                budget_chars=budget,
                preference_cards=cards,
                usage_tracker=tracker,
                **kw,
            )
            return r, tracker
        finally:
            con.close()

    def test_zero_budget_records_zero_hits(self):
        # budget=0：没有任何记忆进入注入文本，计数必须为 0（旧代码会计 1 次）
        r, tracker = self._recall(budget=0)
        self.assertEqual(r["text"], "")
        self.assertEqual(tracker.hits, [])

    def test_injected_cards_are_counted(self):
        r, tracker = self._recall(budget=1200)
        self.assertIn("[偏好]", r["text"])
        self.assertEqual(len(tracker.hits), 1)
        self.assertTrue(tracker.hits[0][0].startswith("pref:"))


class SkillBeforeExtractTests(unittest.TestCase):
    def _run(self, **kw):
        import pipeline as P

        msgs = [
            {
                "text": "测试观众A说今晚来看直播",
                "provenance": "t1",
                "offset": 0,
                "speaker": "u",
                "timestamp": "2026-01-01",
            }
        ]
        captured = {}

        def fake_llm(system, user):
            captured["system"] = system
            return "[]"

        out = list(P.extract_chunks(msgs, config={"chunk_chars": 500}, llm=fake_llm, **kw))
        return out, captured, P

    def test_skill_instructions_enter_prompt_before_extraction(self):
        # 选 a)：select_skills 在提取之前，skill 指令拼入 system prompt
        out, captured, P = self._run()
        self.assertTrue(out[0]["skills_used"], "应当选出 skill")
        self.assertIn("记忆技能指引", captured["system"])
        # 指令正文进了 prompt（不只是名字）
        self.assertIn("evidence_verify", captured["system"])

    def test_no_skillbank_degrades_gracefully(self):
        import pipeline as P

        saved, P.SkillBank = P.SkillBank, None
        try:
            out, captured, _ = self._run()
        finally:
            P.SkillBank = saved
        self.assertEqual(out[0]["skills_used"], [])
        self.assertEqual(captured["system"], P.SYSTEM)


if __name__ == "__main__":
    unittest.main()
