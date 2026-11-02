"""第一阶段：可靠性与安全回归。fixtures 全部为合成数据。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

import quarantine_vault as vault
import reliability as rel
from storage import Transaction, load_records, render_records

SECRET = "这是一条绝密的个人经历描述XYZ"


def make_record(**kwargs):
    rec = {
        "schema_version": 2,
        "claim_id": "c-" + (kwargs.get("tag") or "0001") * 1,
        "revision_id": "r-" + (kwargs.get("tag") or "0001"),
        "event_id": "e-" + (kwargs.get("tag") or "0001"),
        "kind": "project",
        "scope": "global",
        "subject": "记忆工程",
        "predicate": "状态",
        "value": "第一阶段完成",
        "evidence": "第一阶段完成了",
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
    }
    tag = kwargs.pop("tag", None)
    rec.update(kwargs)
    if tag:
        rec["claim_id"] = f"c-{tag}"
        rec["revision_id"] = f"r-{tag}"
        rec["event_id"] = f"e-{tag}"
    return rec


def build_root(path: Path, records, rel_path="long-term/projects.md"):
    prefix = "# 项目\n\n人工前言不会被动。\n"
    target = path / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_records(prefix, records), encoding="utf-8")
    return target


class AttributionTests(unittest.TestCase):
    def test_assistant_cannot_become_user_fact(self):
        out = rel.enforce_attribution(make_record(source="user"), speaker="assistant")
        self.assertEqual(out["source"], "assistant")
        self.assertFalse(out["auto_write"])
        self.assertEqual(out["injection_policy"], "recall")

    def test_missing_speaker_downgrades_self_claimed_user(self):
        out = rel.enforce_attribution(make_record(source="user"), speaker=None)
        self.assertEqual(out["source"], "unknown")
        self.assertTrue(out["attribution_conflict"])

    def test_real_user_speaker_wins_over_model_label(self):
        out = rel.enforce_attribution(make_record(source="assistant"), speaker="user")
        self.assertEqual(out["source"], "user")

    def test_identity_from_non_user_is_demoted(self):
        out = rel.enforce_attribution(make_record(kind="identity", source="assistant"), "assistant")
        self.assertEqual(out["kind"], "fact")

    def test_quoted_keeps_evidence_but_never_user(self):
        out = rel.enforce_attribution(make_record(source="user"), speaker="quoted")
        self.assertEqual(out["source"], "quoted")
        self.assertFalse(out["auto_write"])

    def test_is_user_fact_requires_evidence(self):
        ok = make_record(source="user", evidence_verified=True)
        bad = make_record(source="user", evidence_verified=False)
        self.assertTrue(rel.is_user_fact(ok))
        self.assertFalse(rel.is_user_fact(bad))


class DedupeConflictTests(unittest.TestCase):
    def setUp(self):
        self.a = make_record(tag="a1")
        self.a_dup = make_record(tag="a2")  # 完全同内容
        self.b = make_record(tag="b1", value="第二阶段进行中")

    def test_exact_duplicate_group(self):
        groups = rel.duplicate_groups([self.a, self.a_dup])
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 2)

    def test_near_duplicate_uses_similarity(self):
        near = make_record(tag="a3", value="第一阶段完成了。")
        groups = rel.duplicate_groups([self.a, near])
        self.assertEqual(len(groups), 1)

    def test_conflict_when_same_slot_differs(self):
        groups = rel.conflict_groups([self.a, self.b])
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 2)

    def test_different_slot_is_not_conflict(self):
        other = make_record(tag="c1", predicate="负责人", value="本人")
        self.assertEqual(rel.conflict_groups([self.a, other]), [])

    def test_prepare_write_decisions(self):
        self.assertEqual(rel.prepare_write([self.a], self.a_dup)["action"], "duplicate")
        self.assertEqual(rel.prepare_write([self.a], self.b)["action"], "conflict")
        fresh = make_record(tag="d1", subject="新项目", value="刚立项")
        self.assertEqual(rel.prepare_write([self.a], fresh)["action"], "insert")

    def test_apply_dedupe_marks_superseded_and_keeps_one(self):
        records = [self.a, self.a_dup]
        plan = rel.plan_dedupe(records)
        self.assertEqual(len(plan), 1)
        records, touched = rel.apply_dedupe(records, plan)
        self.assertEqual(len(touched), 1)
        live = [r for r in records if r["status"] == "active"]
        self.assertEqual(len(live), 1)
        self.assertIn("superseded", [r["status"] for r in records])


class AuditUndoTests(unittest.TestCase):
    def test_undo_restores_previous_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "long-term" / "projects.md"
            target.parent.mkdir(parents=True)
            target.write_text("v1", encoding="utf-8")
            txid = Transaction(root).commit({"long-term/projects.md": "v2"})
            self.assertEqual(target.read_text(encoding="utf-8"), "v2")
            log = rel.AuditLog(root)
            log.append("approve", txid, claim_id="c-1")
            self.assertEqual(log.last_tx(), txid)
            rel.undo(root)
            self.assertEqual(target.read_text(encoding="utf-8"), "v1")
            self.assertEqual(log.read()[-1]["action"], "undo")

    def test_undo_without_tx_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                rel.undo(Path(tmp))


class SnapshotTests(unittest.TestCase):
    def _root(self, tmp):
        root = Path(tmp) / "memories"
        build_root(root, [make_record(tag="s1")])
        (root / "system").mkdir(parents=True, exist_ok=True)
        (root / "system" / "quarantine.json").write_text('{"claim_ids":[]}', encoding="utf-8")
        return root

    def test_snapshot_and_restore_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            arch = Path(tmp) / "snap.zip"
            manifest = rel.snapshot(root, arch)
            self.assertGreater(len(manifest["files"]), 1)
            check = rel.verify_snapshot(arch)
            self.assertTrue(check["ok"], check)
            out = Path(tmp) / "restored"
            rel.restore_snapshot(arch, out)
            self.assertEqual(
                (out / "long-term" / "projects.md").read_bytes(),
                (root / "long-term" / "projects.md").read_bytes(),
            )

    def test_snapshot_detects_tampering(self):
        import zipfile

        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            arch = Path(tmp) / "snap.zip"
            rel.snapshot(root, arch)
            with zipfile.ZipFile(arch, "a") as zf:
                zf.writestr("long-term/projects.md", "tampered")
            check = rel.verify_snapshot(arch)
            self.assertFalse(check["ok"])
            self.assertEqual(check["failures"][0]["reason"], "sha256_mismatch")

    def test_restore_refuses_non_empty_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            arch = Path(tmp) / "snap.zip"
            rel.snapshot(root, arch)
            busy = Path(tmp) / "busy"
            busy.mkdir()
            (busy / "keep.txt").write_text("x", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                rel.restore_snapshot(arch, busy)


class VaultTests(unittest.TestCase):
    def _root(self, tmp):
        root = Path(tmp) / "memories"
        build_root(
            root,
            [
                make_record(tag="q1", status="quarantined", value=SECRET, evidence=SECRET),
                make_record(tag="ok1"),
            ],
        )
        return root

    def test_seal_removes_plaintext_from_memory_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            vdir = Path(tmp) / "vault"
            self.assertIn(SECRET, (root / "long-term" / "projects.md").read_text(encoding="utf-8"))
            result = vault.seal(root, vdir, batch="t1")
            self.assertEqual(result["moved"], 1)
            after = (root / "long-term" / "projects.md").read_text(encoding="utf-8")
            self.assertNotIn(SECRET, after)
            self.assertIn("vault_ref", after)

            _, records = load_records(root / "long-term" / "projects.md")
            quarantined = [r for r in records if r["status"] == "quarantined"]
            self.assertEqual(len(quarantined), 1)
            self.assertTrue(quarantined[0]["redacted"])
            self.assertNotIn("value", quarantined[0])
            # 正常记录不受影响
            self.assertEqual(len([r for r in records if r["status"] == "active"]), 1)

            check = vault.verify(root, vdir)
            self.assertTrue(check["ok"], check)
            self.assertEqual(check["plaintext_remaining"], 0)

    def test_unseal_restores_identical_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            vdir = Path(tmp) / "vault"
            original = (root / "long-term" / "projects.md").read_text(encoding="utf-8")
            vault.seal(root, vdir, batch="t2")
            vault.unseal(root, vdir, "t2")
            self.assertEqual((root / "long-term" / "projects.md").read_text(encoding="utf-8"), original)

    def test_encrypted_vault_requires_correct_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            vdir = Path(tmp) / "vault"
            key = b"k" * 32
            res = vault.seal(root, vdir, batch="t3", encrypt_key=key)
            self.assertTrue(res["vault"].endswith(".json.enc"))
            with self.assertRaises(RuntimeError):
                vault.unseal(root, vdir, "t3")
            with self.assertRaises(RuntimeError):
                vault.unseal(root, vdir, "t3", encrypt_key=b"x" * 32)
            vault.unseal(root, vdir, "t3", encrypt_key=key)
            self.assertIn(SECRET, (root / "long-term" / "projects.md").read_text(encoding="utf-8"))

    def test_verify_flags_unsealed_plaintext(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            check = vault.verify(root, Path(tmp) / "vault")
            self.assertFalse(check["ok"])
            self.assertEqual(check["plaintext_remaining"], 1)


if __name__ == "__main__":
    unittest.main()
