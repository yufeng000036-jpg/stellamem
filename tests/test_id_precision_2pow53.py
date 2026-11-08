"""2^53 id 精度检查（3c）：查清 CLI 与评测的写路径有没有把 18 位 id
当 JSON number 处理。

结论（实测，2026-11-08）：没发现问题。

证据：
- JS 侧：adapters、benchmark 里 node 脚本的 JSON.parse/stringify 只经过
  字符串 id（claim_id "c-"+hex、revision_id "r-"+hex、corpus id "F00-00"、
  question_id 十六进制串、session id 串）；流经 JSON 的数字只有
  Date.now()（约 1.7e12）、latency_ms、计数，全部远小于 2^53。
- Python 侧：json.dump/load 对 int 任意精度，写盘路径里的 id 全部由
  字符串生产者产生（claim_normalizer 的 "c-"/"r-"+hex、uuid hex、
  designer_review 的 "sp-YYYYMMDD-nnn"）；唯一外部数字 id 入口是
  raw_archive.make_record，入口处已 str() 强制成字符串。
- 复现证明（/tmp/wave3-repro-2pow53.py，跑过）：9007199254740993（2^53+1）
  走 Python render/load 回读精确；同一份 JSON 文本过 node JSON.parse
  读回 9007199254740992（末位被静默舍入，连 JS 内 `===` 同字面量比较
  都看不出来，因为字面量本身也被舍入）；raw_archive 把它存成字符串
  "9007199254740993"。
- 因此不改生产代码：没有实测到的问题，不虚构修法。本文件把上述三点
  钉成回归测试——以后任何写路径敢产数字 id，测试先红。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "compiler"))

from claim_normalizer import claim_id, claim_revision_id  # noqa: E402
from designer_review import _new_id  # noqa: E402
from raw_archive import make_record  # noqa: E402
from storage import load_records, render_records  # noqa: E402

BIG = 9007199254740993  # 2^53 + 1


class IdPrecisionTests(unittest.TestCase):
    def test_python_write_path_keeps_big_int_exact(self):
        """Python 侧 json 写盘对 int 任意精度：超 2^53 的数字回读精确。"""
        record = {"schema_version": 2, "claim_id": BIG, "revision_id": "r-x", "status": "active"}
        tmp = Path(tempfile.mkdtemp()) / "facts.md"
        tmp.write_text(render_records("# t\n", [record]), encoding="utf-8")
        _, back = load_records(tmp)
        self.assertEqual(back[0]["claim_id"], BIG)

    def test_js_parse_silently_rounds_big_int(self):
        """同一份 JSON 文本过 node JSON.parse 会被静默舍入——这就是
        id 必须走字符串的理由；JS 内连 `===` 同字面量比较都看不出来，
        因为字面量本身也被舍入。"""
        out = subprocess.run(
            [
                "node",
                "-e",
                "console.log(JSON.stringify(JSON.parse(process.argv[1])))",
                json.dumps({"claim_id": BIG}),
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(out.returncode, 0)
        parsed = json.loads(out.stdout)["claim_id"]
        self.assertEqual(parsed, 2**53)
        self.assertNotEqual(parsed, BIG)

    def test_raw_archive_stringifies_big_numeric_message_id(self):
        """唯一外部数字 id 入口：raw_archive 入口处已 str() 强制成字符串。"""
        entry = {"message": {"id": BIG}, "timestamp": "2026-11-01T09:00:00+00:00"}
        made = make_record("s.jsonl", "0" * 64, 0, entry, "user", 8)
        self.assertEqual(made["message_id"], str(BIG))
        self.assertIsInstance(made["message_id"], str)
        self.assertIsInstance(made["record_id"], str)
        self.assertTrue(made["record_id"].startswith("rm-"))

    def test_repo_id_producers_emit_strings(self):
        """仓库内 id 生产者全部产字符串，没有数字 id 的来源。"""
        claim = {
            "kind": "fact",
            "subject": "user",
            "predicate": "p",
            "value": "虚构",
            "evidence": "虚构",
            "source": "user",
        }
        self.assertIsInstance(claim_id(claim), str)
        self.assertTrue(claim_id(claim).startswith("c-"))
        self.assertIsInstance(claim_revision_id(claim), str)
        self.assertTrue(claim_revision_id(claim).startswith("r-"))
        self.assertIsInstance(_new_id([]), str)
        self.assertTrue(_new_id([]).startswith("sp-"))

    def test_eval_fixture_ids_are_strings(self):
        """评测夹具里流经 JS JSON.parse 的 id 全是字符串。"""
        corpus = ROOT / "benchmark" / "reliability" / "corpus.jsonl"
        for line in corpus.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            self.assertIsInstance(item["id"], str)
            for expected in item["expected_ids"]:
                self.assertIsInstance(expected, str)
            for record in item["records"]:
                self.assertIsInstance(record["claim_id"], str)
                self.assertIsInstance(record["revision_id"], str)
        negatives = json.loads(
            (ROOT / "benchmark" / "longmemeval" / "negative_samples.json").read_text(
                encoding="utf-8"
            )
        )
        for case in negatives:
            for record in case["records"]:
                self.assertIsInstance(record["claim_id"], str)
                self.assertIsInstance(record["revision_id"], str)
        manifest = json.loads(
            (ROOT / "benchmark" / "longmemeval" / "manifest.json").read_text(encoding="utf-8")
        )
        for case in manifest["cases"]:
            self.assertIsInstance(case["question_id"], str)


if __name__ == "__main__":
    unittest.main()
