"""Tombstone erasure completeness (MemCut L2 findings D1/D2, 2026-11-21).

The MemCut layered-forgetting validation found two code defects:

- D1: ``_tombstone_record`` replaced ``value`` and cleared ``evidence``
  but left ``predicate`` in plaintext — and Index.build copied that
  plaintext into the records / records_fts predicate columns, so a
  revoked secret survived verbatim in the live file and the index.
- D2: a tombstoned record keeps its row in ``records``, so the orphan
  sweep in ``Index.sync_vectors`` (which only drops vectors whose
  record id vanished) never removed its embedding: the pre-revocation
  vector survived byte-for-byte and stayed semantically retrievable.

Synthetic fixture roots only; every string below is fictional.
"""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

import semantic
from deletion_chain import TOMBSTONE_VALUE, forget_record
from indexer import Index
from storage import load_records, render_records

# The secret lives in the predicate, as in the MemCut probes (the
# predicate field carried the full secret text). The value is generic
# so a plaintext hit can only come from the predicate.
SECRET = "虚构暗号蓝鲸7749号"
SECRET_PREDICATE = f"口头交代的接头暗号是{SECRET}"
SECRET_VALUE = "虚构：已按暗号办理相关手续"
KEEP_PREDICATE = "喜欢的饮品"
KEEP_VALUE = "虚构：喜欢喝冰美式"

TARGET_RID = "c-secret:r-c-secret"
KEEP_RID = "c-keep:r-c-keep"


def _claim(cid, predicate, value, status="active"):
    return {
        "schema_version": 2,
        "claim_id": cid,
        "revision_id": "r-" + cid,
        "status": status,
        "kind": "fact",
        "subject": "user",
        "scope": "global",
        "predicate": predicate,
        "value": value,
        "evidence": value,
        "evidence_verified": True,
        "source": "user",
        "sensitivity": "PRIVATE",
        "write_policy": "allow",
        "injection_policy": "recall",
    }


def _fake_vector(con, rid):
    vec = [1.0] + [0.0] * (semantic.DIM - 1)
    con.execute(
        "INSERT OR REPLACE INTO vectors (id, dim, vec, model) VALUES (?,?,?,?)",
        (rid, semantic.DIM, semantic._pack(vec), semantic.MODEL),
    )
    con.commit()


class TombstoneErasureMixin(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "memories"
        facts = self.root / "semantic" / "facts.md"
        facts.parent.mkdir(parents=True)
        facts.write_text(
            render_records(
                "# 事实\n",
                [
                    _claim("c-secret", SECRET_PREDICATE, SECRET_VALUE),
                    _claim("c-keep", KEEP_PREDICATE, KEEP_VALUE),
                ],
            ),
            encoding="utf-8",
        )
        self.db = self.root / "index.db"
        self._index = Index(self.db)
        self.addCleanup(self._index.close)
        self._index.build(self.root)
        # Both records were embedded before any revocation (as bge-m3
        # would have done); the vectors live in the same index.db.
        _fake_vector(self._index.con, TARGET_RID)
        _fake_vector(self._index.con, KEEP_RID)

    def _predicates(self, claim_id):
        con = sqlite3.connect(str(self.db))
        try:
            rec = con.execute(
                "SELECT predicate FROM records WHERE claim_id=?", (claim_id,)
            ).fetchall()
            fts = con.execute("SELECT predicate FROM records_fts").fetchall()
            vec = con.execute(
                "SELECT COUNT(*) FROM vectors WHERE id=?",
                (f"{claim_id}:r-{claim_id}",),
            ).fetchone()[0]
        finally:
            con.close()
        return [r[0] for r in rec], [r[0] for r in fts], vec


class ForgetErasureTests(TombstoneErasureMixin):
    def test_tombstone_erases_predicate_in_live_file(self):
        forget_record(self.root, "c-secret", mode="tombstone", index_db=self.db)
        facts_path = self.root / "semantic" / "facts.md"
        text = facts_path.read_text(encoding="utf-8")
        self.assertNotIn(SECRET, text)
        self.assertIn(KEEP_VALUE, text)
        _prefix, records = load_records(facts_path)
        target = [r for r in records if r["claim_id"] == "c-secret"][0]
        self.assertEqual(target["status"], "tombstoned")
        self.assertEqual(target["predicate"], TOMBSTONE_VALUE)
        keep = [r for r in records if r["claim_id"] == "c-keep"][0]
        self.assertEqual(keep["predicate"], KEEP_PREDICATE)

    def test_tombstone_erases_predicate_in_index(self):
        forget_record(self.root, "c-secret", mode="tombstone", index_db=self.db)
        rec_preds, fts_preds, _vec = self._predicates("c-secret")
        self.assertEqual(rec_preds, [TOMBSTONE_VALUE])
        self.assertTrue(all(SECRET not in (p or "") for p in fts_preds))
        self.assertIn(KEEP_PREDICATE, fts_preds)

    def test_tombstone_removes_vector(self):
        forget_record(self.root, "c-secret", mode="tombstone", index_db=self.db)
        _rec, _fts, target_vec = self._predicates("c-secret")
        self.assertEqual(target_vec, 0)
        _rec, _fts, keep_vec = self._predicates("c-keep")
        self.assertEqual(keep_vec, 1)


class LegacyTombstoneTests(unittest.TestCase):
    """Pre-fix tombstone files still carry a plaintext predicate.

    Rebuilding the index from such a file must not resurrect the
    plaintext into the index, and vector maintenance must drop the
    stale embedding even though the record row still exists.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "memories"
        facts = self.root / "semantic" / "facts.md"
        facts.parent.mkdir(parents=True)
        legacy = _claim("c-secret", SECRET_PREDICATE, TOMBSTONE_VALUE, status="tombstoned")
        legacy["evidence"] = ""
        facts.write_text(
            render_records(
                "# 事实\n",
                [legacy, _claim("c-keep", KEEP_PREDICATE, KEEP_VALUE)],
            ),
            encoding="utf-8",
        )
        self.db = self.root / "index.db"
        self._index = Index(self.db)
        self.addCleanup(self._index.close)

    def test_rebuild_erases_legacy_predicate_and_drops_vector(self):
        self._index.build(self.root)
        _fake_vector(self._index.con, TARGET_RID)
        _fake_vector(self._index.con, KEEP_RID)
        # A later maintenance rebuild must clean both layers.
        self._index.build(self.root)
        con = sqlite3.connect(str(self.db))
        try:
            pred = con.execute(
                "SELECT predicate FROM records WHERE claim_id='c-secret'"
            ).fetchone()[0]
            fts_preds = [r[0] for r in con.execute("SELECT predicate FROM records_fts")]
            target_vec = con.execute(
                "SELECT COUNT(*) FROM vectors WHERE id=?", (TARGET_RID,)
            ).fetchone()[0]
            keep_vec = con.execute(
                "SELECT COUNT(*) FROM vectors WHERE id=?", (KEEP_RID,)
            ).fetchone()[0]
        finally:
            con.close()
        self.assertEqual(pred, TOMBSTONE_VALUE)
        self.assertTrue(all(SECRET not in (p or "") for p in fts_preds))
        self.assertEqual(target_vec, 0)
        self.assertEqual(keep_vec, 1)


class BuildVectorsTests(TombstoneErasureMixin):
    def test_build_vectors_skips_tombstoned(self):
        # Tombstone first (forget rebuilds the index), then wipe the
        # vectors table and force a full rebuild with a stub embedder:
        # the active record is embedded, the tombstoned one is not.
        forget_record(self.root, "c-secret", mode="tombstone", index_db=self.db)
        con = self._index.con
        con.execute("DELETE FROM vectors")
        con.commit()
        original = semantic.embed_texts
        semantic.embed_texts = lambda texts, **kw: [
            [1.0] + [0.0] * (semantic.DIM - 1) for _ in texts
        ]
        try:
            result = semantic.build_vectors(con, force=True)
        finally:
            semantic.embed_texts = original
        ids = {r[0] for r in con.execute("SELECT id FROM vectors")}
        self.assertIn(KEEP_RID, ids)
        self.assertNotIn(TARGET_RID, ids)
        self.assertEqual(result["failed"], 0)


if __name__ == "__main__":
    unittest.main()
