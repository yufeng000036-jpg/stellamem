"""Unattended writes need a value the verified evidence actually supports.

A verbatim quote match proves provenance, not entailment: the extractor can
return a value that contradicts its own evidence and the quote still
verifies. The grounding gate (compiler/grounding.py) demotes such claims to
human review using deterministic signals only. This replaces PR #118's
byte-equality gate (``value == evidence``), which broke the auto-write
channel because values are normalised assertions, not quotes.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from grounding import support_flags
from pipeline import compile_messages
from source_reader import parse_messages
from store import MemoryStore
from test_memory_safety import verified


def _compile_one(user_text, model_claim):
    messages = parse_messages("[15:32:01] user: " + user_text + "\n", "2026-01-01.md")

    def model(system, payload):
        return json.dumps([model_claim])

    claims = [
        c
        for batch in compile_messages(messages, {"chunk_chars": 512}, model)
        for c in batch["claims"]
    ]
    assert len(claims) == 1, claims
    return claims[0]


def _ingest_counts(claim):
    with tempfile.TemporaryDirectory() as tmp:
        store = MemoryStore(Path(tmp))
        store.ingest(claim)
        return dict(store.counts)


class GroundingSignalTests(unittest.TestCase):
    def test_polarity_flip_english(self):
        self.assertIn("polarity_flip", support_flags("I dislike tea", "I like tea"))
        self.assertIn("polarity_flip", support_flags("likes tea", "I don't like tea"))

    def test_polarity_flip_chinese(self):
        self.assertIn(
            "polarity_flip",
            support_flags("决定换城市，因为想换工作", "我决定不换城市，因为现在的工作很好"),
        )

    def test_negation_elsewhere_is_not_a_flip(self):
        self.assertEqual(support_flags("喜欢茶", "我不抽烟，喜欢茶"), [])
        self.assertEqual(support_flags("likes tea", "I like tea, not coffee"), [])

    def test_faithful_negation_is_kept(self):
        self.assertEqual(support_flags("does not like coffee", "I don't like coffee"), [])
        self.assertEqual(support_flags("dislikes coffee", "I don't like coffee"), [])

    def test_numeric_signals(self):
        self.assertIn(
            "numeric_unanchored",
            support_flags("drinks 3 cups of tea every day", "I like tea"),
        )
        self.assertIn("numeric_mismatch", support_flags("每天喝5杯茶", "我每天喝两杯茶"))
        self.assertEqual(support_flags("5-10 times faster", "tiny 快 5-10 倍"), [])

    def test_unanchored_long_value(self):
        self.assertIn(
            "unanchored_long_value",
            support_flags(
                "the user migrated the production database to postgres last week",
                "Please keep replies concise.",
            ),
        )
        # Short category-style values are not judged on anchoring.
        self.assertEqual(support_flags("fast", "tiny 快 5-10 倍"), [])

    def test_cross_script_paraphrase_is_not_unanchored(self):
        # Production extraction renders Chinese values over English
        # evidence; token overlap is zero by construction, not by
        # fabrication, so the anchoring signal must not fire.
        self.assertEqual(
            support_flags(
                "结构化活动以促进团队协作", "plan structured activities to encourage teamwork"
            ),
            [],
        )
        self.assertEqual(
            support_flags("中等难度、有风景的步道", "a moderate trail with some scenic views"),
            [],
        )

    def test_measure_word_yi_is_not_a_quantity(self):
        self.assertEqual(support_flags("一台复古徕卡相机", "my vintage Leica camera"), [])
        self.assertEqual(support_flags("一枚错版币", "my error coin"), [])

    def test_action_verb_scope_is_not_propositional_negation(self):
        self.assertEqual(
            support_flags(
                "downtown and the highway", "We'd like to avoid downtown and the highway."
            ),
            [],
        )
        self.assertEqual(
            support_flags(
                "easy-to-read, non-dense non-fiction",
                "non-fiction books that are not too dense and easy to read",
            ),
            [],
        )

    def test_lone_cjk_numeral_chars_are_word_parts(self):
        self.assertEqual(support_flags("沙拉和三明治", "tomatoes in salads and sandwiches"), [])
        self.assertEqual(
            support_flags("明治神宫，但一直没机会", "meaning to visit the Meiji Shrine"), []
        )
        self.assertEqual(support_flags("已贴水贴，下一步喷漆", "Next, focus on the paint job"), [])
        self.assertEqual(
            support_flags("日本零式战斗机的旧化效果", "weathering on the Japanese Zero"), []
        )

    def test_english_number_words_anchor(self):
        self.assertEqual(support_flags("2 weeks of travel", "I spent two weeks traveling"), [])
        self.assertEqual(
            support_flags("五年行业经验", "working in the industry for five years"), []
        )

    def test_indefinite_article_anchors_one_time_unit(self):
        self.assertEqual(
            support_flags(
                "studio apartment in Harajuku, Tokyo (1 month)",
                "my new studio apartment in Harajuku - it's been a month now",
            ),
            [],
        )

    def test_compound_cjk_numerals_still_checked(self):
        self.assertIn(
            "numeric_unanchored",
            support_flags("预算三百元", "I like tea and coffee"),
        )
        self.assertIn(
            "numeric_mismatch",
            support_flags("喝了二十杯茶", "I drank three cups of tea"),
        )

    def test_faithful_paraphrase_has_no_flags(self):
        self.assertEqual(support_flags("concise", "Please keep replies concise."), [])
        self.assertEqual(support_flags("detailed", "I now prefer detailed replies"), [])


class UnattendedGroundingIngestTests(unittest.TestCase):
    def test_contradicted_value_with_verbatim_quote_stays_pending(self):
        claim = _compile_one(
            "I like tea",
            dict(
                kind="fact",
                subject="user",
                scope="global",
                predicate="tea",
                value="I dislike tea",
                evidence="I like tea",
                confidence=0.99,
                source="user",
                message_index=0,
            ),
        )
        self.assertTrue(claim["evidence_verified"])
        counts = _ingest_counts(claim)
        self.assertEqual(counts["active"], 0)
        self.assertEqual(counts["pending"], 1)

    def test_chinese_polarity_flip_stays_pending(self):
        claim = verified(
            value="决定换城市，因为想换工作",
            evidence="我决定不换城市，因为现在的工作很好",
        )
        counts = _ingest_counts(claim)
        self.assertEqual(counts["active"], 0)
        self.assertEqual(counts["pending"], 1)

    def test_invented_quantity_stays_pending(self):
        claim = verified(value="drinks 3 cups of tea every day", evidence="I like tea")
        counts = _ingest_counts(claim)
        self.assertEqual(counts["active"], 0)
        self.assertEqual(counts["pending"], 1)

    def test_exact_user_quote_still_auto_writes(self):
        claim = _compile_one(
            "I like tea",
            dict(
                kind="fact",
                subject="user",
                scope="global",
                predicate="tea",
                value="I like tea",
                evidence="I like tea",
                confidence=0.99,
                source="user",
                message_index=0,
            ),
        )
        counts = _ingest_counts(claim)
        self.assertEqual(counts["active"], 1)
        self.assertEqual(counts["pending"], 0)

    def test_faithful_paraphrases_still_auto_write(self):
        for kwargs in (
            dict(value="concise", evidence="Please keep replies concise."),
            dict(value="detailed", evidence="I now prefer detailed replies"),
            dict(value="does not like coffee", evidence="I don't like coffee"),
            dict(value="喜欢茶", evidence="我不抽烟，喜欢茶"),
            dict(value="likes tea", evidence="I like tea, not coffee"),
        ):
            counts = _ingest_counts(verified(**kwargs))
            self.assertEqual(counts["active"], 1, kwargs)
            self.assertEqual(counts["pending"], 0, kwargs)


if __name__ == "__main__":
    unittest.main()
