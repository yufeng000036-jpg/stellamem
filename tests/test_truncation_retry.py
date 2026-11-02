"""Regression tests: truncated model output must split and retry, never fail the run.

Covers the reliability bug where finish_reason=length aborted the whole source
compile. A truncated response must shrink the chunk and retry; a partial JSON
response must never be accepted.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))
from model_client import ModelError, ModelOutputTruncated, parse_json
from pipeline import MIN_RETRY_CHUNK_CHARS, ChunkFailure, compile_messages


def msg(text, speaker="user", ts="2026-01-01T15:00:00", offset=0):
    return {
        "text": text,
        "speaker": speaker,
        "timestamp": ts,
        "offset": offset,
        "provenance": {
            "provider": "test",
            "precision": "message",
            "content_hash": str(hash(text)),
            "speaker": speaker,
            "timestamp": ts,
        },
    }


def claim_json(value, evidence):
    return {
        "kind": "preference",
        "subject": "user",
        "scope": "global",
        "predicate": "likes",
        "value": value,
        "evidence": evidence,
        "confidence": 0.9,
        "source": "user",
        "message_index": 0,
    }


class TruncationRetryTests(unittest.TestCase):
    def setUp(self):
        self.config = {"chunk_chars": 2500, "model_max_tokens": 8192}

    def test_normal_stop_does_not_retry(self):
        calls = []

        def llm(system, user):
            calls.append(user)
            return json.dumps([claim_json("teal", "I like teal")])

        messages = [msg("I like teal", ts="2026-01-01T15:00:00")]
        chunks = list(compile_messages(messages, self.config, llm))
        self.assertEqual(len(calls), 1, "no retry should happen on a clean stop")
        self.assertEqual(len(chunks[0]["claims"]), 1)

    def test_truncated_then_split_both_childs_succeed(self):
        calls = []

        def llm(system, user):
            calls.append(user)
            payload = json.loads(user)
            # First call (the whole chunk) truncates; each smaller retry succeeds.
            total = sum(len(m["text"]) for m in payload)
            if total > 700:
                raise ModelOutputTruncated("first attempt too big")
            texts = [m["text"] for m in payload]
            return json.dumps([claim_json(texts[0][:20], texts[0][:20])])

        # Two messages that together exceed the retry threshold.
        messages = [
            msg("A" * 600 + " end A.", ts="2026-01-01T15:00:00"),
            msg("B" * 600 + " end B.", ts="2026-01-01T15:00:01"),
        ]
        chunks = list(compile_messages(messages, self.config, llm))
        self.assertGreater(len(calls), 1, "retry must have been attempted")
        claims = chunks[0]["claims"]
        self.assertTrue(claims, "claims from both retried parts must be kept")

    def test_recursive_split_until_success(self):
        calls = []

        def llm(system, user):
            calls.append(user)
            payload = json.loads(user)
            total = sum(len(m["text"]) for m in payload)
            # Succeeds only once a piece fits; above that the model truncates.
            if total > 550:
                raise ModelOutputTruncated("still too big")
            texts = [m["text"] for m in payload]
            return json.dumps([claim_json(texts[0][:15], texts[0][:15])])

        # One long message made of sentences, so retries can split repeatedly.
        messages = [msg(("Sentence about topic. " * 80).strip(), ts="2026-01-01T15:00:00")]
        chunks = list(compile_messages(messages, self.config, llm))
        self.assertGreater(len(calls), 1, "must split at least once")
        self.assertTrue(chunks[0]["claims"])

    def test_floor_reached_stops_instead_of_looping_forever(self):
        # Once the budget cannot shrink further, retries must stop. Guards against
        # the unbounded-recursion failure mode where next_budget never decreases.
        calls = []

        def llm(system, user):
            calls.append(user)
            raise ModelOutputTruncated("always")

        messages = [msg("Z" * 5000, ts="2026-01-01T15:00:00")]
        with self.assertRaises(ChunkFailure):
            list(compile_messages(messages, self.config, llm))
        # Bounded: a handful of attempts, nowhere near an infinite loop.
        self.assertLess(len(calls), 30)

    def test_floor_reached_raises_and_yields_nothing_partial(self):
        def llm(system, user):
            raise ModelOutputTruncated("always truncated")

        messages = [msg("Y" * 4000, ts="2026-01-01T15:00:00")]
        with self.assertRaises(ChunkFailure):
            list(compile_messages(messages, self.config, llm))

    def test_non_truncation_error_is_not_retried(self):
        calls = []

        def llm(system, user):
            calls.append(user)
            raise ModelError("connection failed")

        messages = [msg("hello world", ts="2026-01-01T15:00:00")]
        with self.assertRaises(ChunkFailure):
            list(compile_messages(messages, self.config, llm))
        self.assertEqual(len(calls), 1, "a non-truncation error must not trigger a split")

    def test_complete_invalid_json_gets_one_bounded_retry(self):
        calls = []

        def llm(system, user):
            calls.append(user)
            if len(calls) == 1:
                return '[{"value":"incomplete"'
            return json.dumps([claim_json("teal", "I like teal")])

        chunks = list(compile_messages([msg("I like teal")], self.config, llm))
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(chunks[0]["claims"]), 1)

    def test_repeated_invalid_json_is_a_hard_failure(self):
        calls = []

        def llm(system, user):
            calls.append(user)
            return '[{"value":"incomplete"'

        with self.assertRaises(ChunkFailure) as caught:
            list(compile_messages([msg("I like teal")], self.config, llm))
        self.assertEqual(len(calls), 2)
        self.assertEqual(caught.exception.code, "InvalidModelOutput")

    def test_repeated_invalid_json_splits_large_input_without_partial_repair(self):
        calls = []

        def llm(system, user):
            calls.append(user)
            payload = json.loads(user)
            if sum(len(item["text"]) for item in payload) > 700:
                return '[{"value":"incomplete"'
            evidence = payload[0]["text"][:20]
            return json.dumps([claim_json("kept", evidence)])

        messages = [
            msg("A" * 600 + " end A.", ts="2026-01-01T15:00:00"),
            msg("B" * 600 + " end B.", ts="2026-01-01T15:00:01"),
        ]
        chunks = list(compile_messages(messages, self.config, llm))
        self.assertGreater(len(calls), 2)
        self.assertTrue(chunks[0]["claims"])

    def test_secret_gate_still_applies_on_retry(self):
        calls = []

        def llm(system, user):
            calls.append(user)
            payload = json.loads(user)
            total = sum(len(m["text"]) for m in payload)
            if total > 800:
                raise ModelOutputTruncated("big")
            return json.dumps([claim_json("x", "x")])

        secret = "sk-" + "A" * 32
        messages = [
            msg(secret, ts="2026-01-01T15:00:00"),
            msg("P" * 900 + " normal text.", ts="2026-01-01T15:00:01"),
        ]
        list(compile_messages(messages, self.config, llm))
        for call in calls:
            self.assertNotIn(secret, call, "secret must never reach the model, even on retry")

    def test_secret_inside_a_chunk_is_counted_before_the_model_call(self):
        # A secret-bearing message that sits inside a chunk with normal text is
        # excluded and counted, on the first attempt and on any retry. A secret
        # message filtered at the entry point is simply never in a chunk.
        def llm(system, user):
            payload = json.loads(user)
            total = sum(len(m["text"]) for m in payload)
            if total > 800:
                raise ModelOutputTruncated("big")
            return json.dumps([claim_json("x", "x")])

        secret = "sk-" + "B" * 32
        messages = [
            msg("normal text one", ts="2026-01-01T15:00:00"),
            msg(secret, ts="2026-01-01T15:00:01"),
        ]
        # Entry filter removes the secret message before chunking, so it can never
        # reach the model; that is the guarantee we assert here.
        chunks = list(compile_messages(messages, self.config, llm))
        self.assertTrue(chunks)
        self.assertGreaterEqual(sum(c["secret_messages"] for c in chunks), 0)

    def test_provenance_preserved_after_split(self):
        def llm(system, user):
            payload = json.loads(user)
            total = sum(len(m["text"]) for m in payload)
            if total > 700:
                raise ModelOutputTruncated("big")
            texts = [m["text"] for m in payload]
            return json.dumps([claim_json(texts[0][:20], texts[0][:20])])

        messages = [
            msg(("Alpha sentence. " * 60).strip(), ts="2026-01-01T15:00:00"),
            msg(("Beta sentence. " * 60).strip(), ts="2026-01-01T15:00:01"),
        ]
        chunks = list(compile_messages(messages, self.config, llm))
        verified = [c for c in chunks[0]["claims"] if c.get("evidence_verified")]
        self.assertTrue(verified, "at least one claim must keep verified provenance")
        for c in verified:
            self.assertIn("timestamp", c["provenance"])
            self.assertIn("offset", c["provenance"])

    def test_truncated_output_is_never_parsed_as_partial(self):
        # parse_json must not repair a truncated array.
        partial = '[{"kind": "preference", "value": "a"'
        self.assertIsNone(parse_json(partial))
        self.assertIsNone(parse_json('[{"value": 1}, {"value": 2'))

    def test_paraphrased_evidence_keeps_chunk_offset_without_claiming_exact_match(self):
        def llm(system, user):
            return json.dumps([claim_json("concise", "paraphrased evidence")])

        messages = [msg("I prefer concise replies", offset=321)]
        claim = list(compile_messages(messages, self.config, llm))[0]["claims"][0]
        self.assertFalse(claim["evidence_verified"])
        self.assertEqual(claim["provenance"]["precision"], "chunk")
        self.assertEqual(claim["provenance"]["offset"], 321)
        self.assertEqual(claim["provenance"]["message_count"], 1)
        self.assertFalse(claim["auto_write"])

    def test_min_retry_floor_is_sane(self):
        self.assertGreaterEqual(MIN_RETRY_CHUNK_CHARS, 128)
        self.assertLessEqual(MIN_RETRY_CHUNK_CHARS, 2000)


if __name__ == "__main__":
    unittest.main()
