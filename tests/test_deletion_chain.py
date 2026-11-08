"""Deletion derivation-chain: trace and forget a record everywhere.

栖流's paper note (EigenFlux, 2026-11-08): deleting only the original
leaves derived summaries able to recover the content in ~20% of cases.
Synthetic fixture roots only; no production memory touched.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

import raw_archive
from deletion_chain import forget_record, trace
from derived_cache import DerivedMarkdownCache
from indexer import Index
from storage import render_records

DEL_VALUE = "虚构：每周三去海边露营"
KEEP_VALUE = "虚构：喜欢喝冰美式"


def _claim(cid, value, provenance=None):
    rec = {
        "schema_version": 2,
        "claim_id": cid,
        "revision_id": "r-" + cid,
        "status": "active",
        "kind": "fact",
        "subject": "user",
        "scope": "global",
        "predicate": "likes",
        "value": value,
        "evidence": value,
        "evidence_verified": True,
        "source": "user",
        "sensitivity": "PRIVATE",
        "write_policy": "allow",
        "injection_policy": "recall",
    }
    if provenance:
        rec["provenance"] = provenance
    return rec


class FixtureMixin(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "memories"
        self.archive_root = Path(self.tmp.name) / "archive"
        # Live file with two claims.
        facts = self.root / "semantic" / "facts.md"
        facts.parent.mkdir(parents=True)
        facts.write_text(
            render_records(
                "# 事实\n",
                [
                    _claim("c-del", DEL_VALUE, {"record_id": "rm-del-1", "session_id": "s1"}),
                    _claim("c-keep", KEEP_VALUE),
                ],
            ),
            encoding="utf-8",
        )
        # Summary line quoting the deleted value + one quoting the kept value.
        lt = self.root / "long-term"
        lt.mkdir(parents=True)
        (lt / "self-changes-user.md").write_text(
            "# 我变了什么（user）\n\n"
            f"- {DEL_VALUE}（规则合成·来源：2026-11-06 日档“变”）\n"
            f"- {KEEP_VALUE}（规则合成·来源：2026-11-06 日档“变”）\n",
            encoding="utf-8",
        )
        # Diary line quoting the deleted value (trace-only: never modified).
        diary = self.root / "daily" / "user"
        diary.mkdir(parents=True)
        (diary / "2026-11-06.md").write_text(
            f"# 日档（user · 2026-11-06）\n\n## 变\n\n- {DEL_VALUE}\n",
            encoding="utf-8",
        )
        # Index built from the live files. Close it explicitly: the Index
        # ends up in a reference cycle, so refcounting does not free it and
        # only the cyclic GC closes the file — on Windows the still-open
        # index.db then blocks TemporaryDirectory cleanup (WinError 32).
        # addCleanup runs LIFO, so this close runs before tmp.cleanup.
        self.db = self.root / "index.db"
        self._index = Index(self.db)
        self.addCleanup(self._index.close)
        self._index.build(self.root)
        # Derived cache entry for the facts file.
        self.cache = DerivedMarkdownCache(self.root)
        self.cache.get_or_parse(facts, lambda text, path: {"parsed": True})
        self.cache.flush()
        # Raw archive holding the provenance record.
        stream_dir = self.archive_root / "sessions"
        stream_dir.mkdir(parents=True)
        raw_archive.gz_write(
            stream_dir / "s1.jsonl.gz",
            [
                {
                    "schema_version": 1,
                    "record_id": "rm-del-1",
                    "session_file": "s1.jsonl",
                    "session_id": "s1",
                    "text": DEL_VALUE,
                    "text_sha256": "x",
                },
                {
                    "schema_version": 1,
                    "record_id": "rm-other-1",
                    "session_file": "s1.jsonl",
                    "session_id": "s1",
                    "text": "unrelated",
                    "text_sha256": "y",
                },
            ],
        )
        raw_archive.rebuild_manifest(self.archive_root)
        raw_archive.write_json(
            self.archive_root / "ledger.json",
            {"schema_version": 1, "sources": {}, "totals": {}},
        )


class TraceTests(FixtureMixin):
    def test_trace_finds_all_layers(self):
        t = trace(self.root, "c-del", index_db=self.db, archive_root=self.archive_root)
        self.assertEqual(len(t["live"]), 1)
        self.assertEqual(t["live"][0]["file"], "semantic/facts.md")
        self.assertEqual(len(t["summary"]), 1)
        self.assertIn("self-changes-user.md", t["summary"][0]["file"])
        self.assertEqual(len(t["diary"]), 1)
        self.assertTrue(t["index"]["active_present"])
        self.assertEqual(t["cache"]["status"], "found")
        self.assertEqual(t["raw_archive"]["status"], "found")

    def test_trace_without_provenance(self):
        t = trace(self.root, "c-keep", index_db=self.db, archive_root=self.archive_root)
        self.assertEqual(t["raw_archive"]["status"], "no-provenance-link")
        self.assertEqual(len(t["summary"]), 1)  # its own synthesis line

    def test_trace_unknown_claim(self):
        t = trace(self.root, "c-nope", index_db=self.db, archive_root=self.archive_root)
        self.assertEqual(t["live"], [])
        self.assertEqual(t["summary"], [])
        self.assertFalse(t["index"]["active_present"])


class ForgetTests(FixtureMixin):
    def test_forget_tombstone(self):
        report = forget_record(
            self.root,
            "c-del",
            mode="tombstone",
            index_db=self.db,
            archive_root=self.archive_root,
        )
        self.assertEqual(report["layers"]["live"]["action"], "tombstoned")
        self.assertEqual(report["layers"]["summary"]["action"], "tombstoned")
        self.assertEqual(report["layers"]["summary"]["lines"], 1)
        self.assertFalse(report["layers"]["index"]["active_remaining"])
        self.assertEqual(report["layers"]["cache"]["entries"], 1)
        self.assertEqual(report["layers"]["raw_archive"]["action"], "removed")
        self.assertEqual(report["layers"]["raw_archive"]["records"], 1)
        self.assertEqual(report["layers"]["diary"]["action"], "traced-only")
        # The live block keeps its id but carries the tombstone, not content.
        facts = (self.root / "semantic" / "facts.md").read_text(encoding="utf-8")
        self.assertNotIn(DEL_VALUE, facts)
        self.assertIn('"status": "tombstoned"', facts)
        self.assertIn("[已删除]", facts)
        self.assertIn("c-del", facts)
        # The kept claim and its summary line survive untouched.
        self.assertIn(KEEP_VALUE, facts)
        lt = (self.root / "long-term" / "self-changes-user.md").read_text(encoding="utf-8")
        self.assertNotIn(DEL_VALUE, lt)
        self.assertIn(KEEP_VALUE, lt)
        self.assertIn("已删除：原文已删，摘要随删", lt)
        # Diary is never rewritten by the chain.
        diary = (self.root / "daily" / "user" / "2026-11-06.md").read_text(encoding="utf-8")
        self.assertIn(DEL_VALUE, diary)
        # Raw archive: the record is gone, the other one stays, ledger notes it.
        self.assertNotIn("rm-del-1", raw_archive.existing_ids(self.archive_root))
        self.assertIn("rm-other-1", raw_archive.existing_ids(self.archive_root))
        ledger = raw_archive.read_ledger(self.archive_root)
        self.assertEqual(ledger["deletions"][0]["claim_id"], "c-del")

    def test_forget_purge(self):
        report = forget_record(
            self.root,
            "c-del",
            mode="purge",
            index_db=self.db,
            archive_root=self.archive_root,
        )
        self.assertEqual(report["layers"]["live"]["action"], "purged")
        facts = (self.root / "semantic" / "facts.md").read_text(encoding="utf-8")
        self.assertNotIn("c-del", facts)
        self.assertNotIn(DEL_VALUE, facts)
        self.assertIn(KEEP_VALUE, facts)
        lt = (self.root / "long-term" / "self-changes-user.md").read_text(encoding="utf-8")
        self.assertNotIn(DEL_VALUE, lt)
        self.assertIn(KEEP_VALUE, lt)
        self.assertFalse(report["layers"]["index"]["active_remaining"])

    def test_forget_is_idempotent(self):
        forget_record(self.root, "c-del", index_db=self.db, archive_root=self.archive_root)
        second = forget_record(self.root, "c-del", index_db=self.db, archive_root=self.archive_root)
        self.assertEqual(second["layers"]["live"]["action"], "already-tombstoned")
        self.assertEqual(second["layers"]["summary"]["action"], "clean")
        self.assertFalse(second["layers"]["index"]["active_remaining"])
        self.assertEqual(second["layers"]["raw_archive"]["action"], "absent")

    def test_forget_unknown_claim(self):
        report = forget_record(self.root, "c-nope", index_db=self.db)
        self.assertEqual(report["layers"]["live"]["action"], "not-found")
        self.assertEqual(report["layers"]["summary"]["action"], "clean")

    def test_unknown_mode_rejected(self):
        with self.assertRaises(ValueError):
            forget_record(self.root, "c-del", mode="shred")

    def test_forget_closes_index_connection(self):
        # Regression: forget_record() used to build the index with a bare
        # `Index(db).build(root)` and never close it. The Index ends up in
        # a reference cycle, so only the cyclic GC closes the file — on
        # Windows the still-open index.db then blocks directory deletion
        # (WinError 32; CI windows-3.12 failed 8/8 deletion-chain tests).
        # This spy asserts close() is called, without relying on GC timing.
        import deletion_chain
        from unittest import mock

        closed = []

        class ClosingIndex(Index):
            def close(self):
                closed.append(str(self.path))
                super().close()

        with mock.patch.object(deletion_chain, "Index", ClosingIndex):
            forget_record(self.root, "c-del", index_db=self.db)
        self.assertIn(str(self.db), closed)


if __name__ == "__main__":
    unittest.main()
