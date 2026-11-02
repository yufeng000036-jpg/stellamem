"""P1：原始档案层必须无损、可增量、可幂等、可校验、可删除、可导出。

这些测试只用合成会话，不碰真实档案。
"""

import gzip
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))
import raw_archive
import raw_source
from pipeline import compile_messages


def line(role, text, ts, message_id):
    return json.dumps(
        {"timestamp": ts, "message": {"role": role, "id": message_id, "content": text}},
        ensure_ascii=False,
    )


def write_session(path, rows):
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


class RawArchiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.sessions = self.tmp / "sessions"
        self.sessions.mkdir()
        self.root = self.tmp / "raw"
        write_session(
            self.sessions / "s1.jsonl",
            [
                line("user", "我想把记忆底座做成单人本地优先的。", "2026-09-15T02:00:00Z", "m1"),
                line("assistant", "明白，那就先做原始档案层。", "2026-09-15T02:00:30Z", "m2"),
                line("toolResult", "{\"exit\":0}", "2026-09-15T02:00:40Z", "m3"),
                line("user", "", "2026-09-15T02:01:00Z", "m4"),
            ],
        )
        write_session(
            self.sessions / "s2.jsonl",
            [line("user", "第二天又想到时间线要能回答第一次出现。", "2026-09-16T03:00:00Z", "n1")],
        )
        # 已删除的会话必须被跳过：删除意图优先于归档完整度
        write_session(
            self.sessions / "s3.deleted.jsonl",
            [line("user", "这句不该被归档。", "2026-09-17T04:00:00Z", "x1")],
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ingest(self, **kwargs):
        return raw_archive.ingest(self.root, self.sessions, ("user", "assistant"), 8, **kwargs)

    def test_ingest_keeps_text_lossless_and_skips_deleted(self):
        report = self.ingest()
        self.assertEqual(report["added"], 3)
        self.assertEqual(report["skipped_deleted"], 1)
        self.assertEqual(report["excluded_role"], 1)
        self.assertEqual(report["empty_text"], 1)
        days = raw_source.days(self.root)
        self.assertEqual(days, {"2026-09-15": 2, "2026-09-16": 1})
        messages = raw_source.messages_for_day(self.root, "2026-09-15", ("user",))
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["text"], "我想把记忆底座做成单人本地优先的。")
        self.assertEqual(messages[0]["speaker"], "user")
        self.assertEqual(messages[0]["provenance"]["provider"], "raw")
        self.assertTrue(messages[0]["provenance"]["record_id"].startswith("rm-"))

    def test_ingest_is_idempotent(self):
        self.ingest()
        again = self.ingest()
        self.assertEqual(again["added"], 0)
        self.assertEqual(again["skipped_unchanged"], 2)
        stat = raw_archive.stats(self.root)
        self.assertEqual(stat["records"], 3)

    def test_appended_source_is_picked_up_without_duplicating(self):
        self.ingest()
        with (self.sessions / "s2.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(line("user", "补充一句：删除要能传播到索引。", "2026-09-16T05:00:00Z", "n2") + "\n")
        report = self.ingest()
        self.assertEqual(report["added"], 1)
        self.assertEqual(raw_archive.stats(self.root)["records"], 4)

    def test_verify_detects_tampering(self):
        self.ingest()
        self.assertTrue(raw_archive.verify(self.root)["ok"])
        stream = self.root / "sessions" / "s1.jsonl.gz"
        records = list(raw_source._stream(self.root, "s1.jsonl"))
        records[0]["text"] = "被改过的内容"
        with gzip.open(stream, "wt", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        result = raw_archive.verify(self.root)
        self.assertFalse(result["ok"])
        self.assertEqual(result["text_hash_mismatch"], 1)

    def test_get_record_roundtrip(self):
        self.ingest()
        rid = raw_source.messages_for_day(self.root, "2026-09-16", ("user",))[0]["provenance"]["record_id"]
        record = raw_source.get_record(self.root, rid)
        self.assertIn("第一次出现", record["text"])
        self.assertEqual(record["role"], "user")

    def test_forget_removes_stream_and_manifest_entries(self):
        self.ingest()
        result = raw_archive.forget(self.root, "s1")
        self.assertTrue(result["removed"])
        self.assertEqual(result["records"], 2)
        self.assertEqual(raw_archive.stats(self.root)["records"], 1)
        self.assertFalse((self.root / "sessions" / "s1.jsonl.gz").exists())
        self.assertTrue(raw_archive.verify(self.root)["ok"])

    def test_export_is_reimportable(self):
        self.ingest()
        out = self.tmp / "export.jsonl"
        count = raw_archive.export(self.root, day="2026-09-15", out=str(out))
        self.assertEqual(count, 2)
        rows = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines() if x.strip()]
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["role"] for r in rows}, {"user", "assistant"})
        self.assertTrue(all(r["text"] for r in rows))


class AttributionTest(unittest.TestCase):
    """证据归属必须来自原文，不能由 approve 的默认值决定。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        sessions = self.tmp / "sessions"
        sessions.mkdir()
        write_session(
            sessions / "s1.jsonl",
            [
                line("user", "我决定以后只看本地模型，因为云端太慢。", "2026-09-15T02:00:00Z", "m1"),
                line("assistant", "我建议把索引层推迟到第二阶段。", "2026-09-15T02:01:00Z", "m2"),
            ],
        )
        self.root = self.tmp / "raw"
        raw_archive.ingest(self.root, sessions, ("user", "assistant"), 8, False)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def compile_with(self, evidence, speaker):
        messages = raw_source.messages_for_day(self.root, "2026-09-15", ("user", "assistant"))

        def stub(_system, _payload):
            return json.dumps(
                [
                    {
                        "kind": "decision",
                        "subject": "模型选择",
                        "predicate": "使用范围",
                        "value": "只看本地模型",
                        "evidence": evidence,
                        "confidence": 0.98,
                    }
                ],
                ensure_ascii=False,
            )

        claims = [c for chunk in compile_messages(messages, {"chunk_chars": 4000}, llm=stub)
                  for c in chunk["claims"]]
        self.assertEqual(len(claims), 1)
        return claims[0]

    def test_user_utterance_keeps_user_attribution(self):
        claim = self.compile_with("我决定以后只看本地模型，因为云端太慢。", "user")
        self.assertEqual(claim["source"], "user")
        self.assertEqual(claim["provenance"]["speaker"], "user")
        self.assertEqual(claim["provenance"]["provider"], "raw")
        self.assertTrue(claim["provenance"]["record_id"].startswith("rm-"))
        self.assertTrue(claim["evidence_verified"])

    def test_assistant_sentence_is_not_laundered_into_user(self):
        claim = self.compile_with("我建议把索引层推迟到第二阶段。", "assistant")
        self.assertEqual(claim["source"], "assistant")
        self.assertEqual(claim["provenance"]["speaker"], "assistant")


if __name__ == "__main__":
    unittest.main()
