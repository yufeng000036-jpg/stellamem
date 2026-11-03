"""Tests for batch2 item 1: Skill 运行时接线（pipeline.py 接入 SkillBank）。

Synthetic data only; nothing touches real user memory.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

import pipeline
from skills import SkillBank


def _msg(text, speaker="user"):
    return {
        "text": text,
        "speaker": speaker,
        "timestamp": "2026-11-03T10:00:00+00:00",
        "provenance": {"precision": "message"},
        "offset": 0,
    }


def _fake_llm_ok(system, user):
    return "[]"


class SkillWiringTests(unittest.TestCase):
    def test_chunk_result_carries_skills_used(self):
        msgs = [_msg("我喜欢在晚上看直播，记住这个偏好。")]
        chunks = list(pipeline.extract_chunks(msgs, llm=_fake_llm_ok))
        self.assertEqual(len(chunks), 1)
        self.assertIn("skills_used", chunks[0])
        self.assertIsInstance(chunks[0]["skills_used"], list)
        self.assertTrue(chunks[0]["skills_used"])

    def test_safety_net_always_attached_for_extraction(self):
        msgs = [_msg("今天天气不错")]  # 无关键词的普通文本
        chunks = list(pipeline.extract_chunks(msgs, llm=_fake_llm_ok))
        used = set(chunks[0]["skills_used"])
        for name in ("evidence_verify", "secret_gate", "injection_screen"):
            self.assertIn(name, used, "安全网 skill 缺失: " + name)

    def test_extraction_skill_picked_for_claim_text(self):
        msgs = [_msg("记住：我的生日是3月26日。")]
        chunks = list(pipeline.extract_chunks(msgs, llm=_fake_llm_ok))
        self.assertIn("extract_atomic_claims", chunks[0]["skills_used"])

    def test_custom_bank_is_used(self):
        bank = SkillBank()
        msgs = [_msg("hello world")]
        chunks = list(pipeline.extract_chunks(msgs, llm=_fake_llm_ok, skill_bank=bank))
        self.assertTrue(chunks[0]["skills_used"])

    def test_compile_messages_passthrough(self):
        msgs = [_msg("记住这个。")]
        chunks = list(pipeline.compile_messages(msgs, llm=_fake_llm_ok))
        self.assertIn("skills_used", chunks[0])

    def test_claims_still_extracted_alongside_skills(self):
        import json

        def llm(system, user):
            return json.dumps(
                [
                    {
                        "kind": "preference",
                        "subject": "user",
                        "scope": "",
                        "predicate": "喜欢",
                        "value": "晚上看直播",
                        "evidence": "我喜欢在晚上看直播",
                        "confidence": 0.9,
                        "source": "user",
                        "message_index": 0,
                    }
                ]
            )

        msgs = [_msg("我喜欢在晚上看直播")]
        chunks = list(pipeline.extract_chunks(msgs, llm=llm))
        self.assertEqual(len(chunks[0]["claims"]), 1)
        self.assertTrue(chunks[0]["skills_used"])


if __name__ == "__main__":
    unittest.main()
