"""H1 regression tests: assistant-provided content as speaker-tagged history.

Round-4 claim tracking found all 51 single-session-assistant gold turns
produced no active claim. Two extraction-layer causes, both pinned here:

1. The extractor could yield a claim from an assistant turn with the
   evidence quote verified verbatim, but enforce_attribution then stripped
   evidence_verified for every non-user source except "quoted" — the claim
   became indistinguishable from an unverified model guess and died at
   every downstream gate. Verification is a property of the quote match,
   not of the speaker; attribution stays in source / provenance.speaker.
2. apply_policy labelled such claims "unverified user speaker" although
   they never claimed to be user speech. They are history records that
   require human review (write_policy stays "confirm"); the reason now
   says so.

Governance invariants that must NOT move: assistant claims never become
user facts (is_user_fact stays False), never auto-write, and stay
recall-only at the attribution layer.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))
import reliability as rel
from claim_normalizer import normalize_claim
from pipeline import SYSTEM, compile_messages
from security import apply_policy


def msg(text, speaker, mid, ts="2026-01-01T15:00:00", offset=0):
    return {
        "text": text,
        "speaker": speaker,
        "timestamp": ts,
        "offset": offset,
        "provenance": {
            "provider": "test",
            "precision": "message",
            "message_id": mid,
            "speaker": speaker,
            "timestamp": ts,
        },
    }


class AttributionVerificationTests(unittest.TestCase):
    def base(self, **kw):
        rec = {
            "source": "user",
            "kind": "fact",
            "evidence_verified": True,
            "write_policy": "allow",
            "injection_policy": "always",
            "auto_write": True,
        }
        rec.update(kw)
        return rec

    def test_assistant_verified_evidence_survives_attribution(self):
        out = rel.enforce_attribution(self.base(source="user"), speaker="assistant")
        self.assertEqual(out["source"], "assistant")
        self.assertTrue(out["evidence_verified"])
        # Still not a user fact, still never auto-written, recall only.
        self.assertFalse(rel.is_user_fact(out))
        self.assertFalse(out["auto_write"])
        self.assertEqual(out["injection_policy"], "recall")

    def test_assistant_unverified_evidence_stays_unverified(self):
        out = rel.enforce_attribution(
            self.base(source="assistant", evidence_verified=False), speaker="assistant"
        )
        self.assertEqual(out["source"], "assistant")
        self.assertFalse(out["evidence_verified"])

    def test_user_verified_unchanged(self):
        out = rel.enforce_attribution(self.base(source="assistant"), speaker="user")
        self.assertEqual(out["source"], "user")
        self.assertTrue(out["evidence_verified"])
        self.assertTrue(rel.is_user_fact(out))


class PolicyReasonTests(unittest.TestCase):
    def claim(self, source, verified):
        c = normalize_claim(
            {
                "kind": "fact",
                "subject": "user",
                "scope": "global",
                "predicate": "recommends",
                "value": "light-bodied beer",
                "evidence": "use a light-bodied beer",
                "confidence": 0.9,
                "source": source,
            }
        )
        c["source"] = source
        c["evidence_verified"] = verified
        return c

    def test_verified_assistant_claim_reason_is_review_not_unverified(self):
        out = apply_policy(self.claim("assistant", True))
        self.assertEqual(out["write_policy"], "confirm")
        self.assertIn("non-user speaker requires review", out["policy_reasons"])
        self.assertNotIn("unverified user speaker", out["policy_reasons"])

    def test_unverified_assistant_claim_keeps_unverified_reason(self):
        out = apply_policy(self.claim("assistant", False))
        self.assertEqual(out["write_policy"], "confirm")
        self.assertIn("unverified user speaker", out["policy_reasons"])


ASSISTANT_TEXT = (
    "I don't have personal preferences, but in general, it's recommended "
    "to use a light or medium-bodied beer for this recipe, such as a "
    "Pilsner or Lager."
)
ASSISTANT_EVIDENCE = "it's recommended to use a light or medium-bodied beer for this recipe"


class PipelineAssistantExtractionTests(unittest.TestCase):
    def test_system_prompt_covers_assistant_content(self):
        self.assertIn("Assistant-provided content", SYSTEM)

    def test_assistant_claim_extracted_verified_and_tagged(self):
        def llm(system, user):
            return json.dumps(
                [
                    {
                        "kind": "fact",
                        "subject": "user",
                        "scope": "global",
                        "predicate": "beer recommended for seco de cordero",
                        "value": "light or medium-bodied beer",
                        "evidence": ASSISTANT_EVIDENCE,
                        "confidence": 0.9,
                        "source": "assistant",
                        "message_index": 1,
                    },
                    {
                        "kind": "preference",
                        "subject": "user",
                        "scope": "global",
                        "predicate": "wants to try",
                        "value": "seco de cordero this weekend",
                        "evidence": "I want to try seco de cordero this weekend",
                        "confidence": 0.9,
                        "source": "user",
                        "message_index": 0,
                    },
                ]
            )

        messages = [
            msg("I want to try seco de cordero this weekend. What beer fits it?", "user", "s:0"),
            msg(ASSISTANT_TEXT, "assistant", "s:1"),
        ]
        claims = [
            c
            for batch in compile_messages(messages, {"chunk_chars": 2500}, llm)
            for c in batch["claims"]
        ]
        by_mid = {(c.get("provenance") or {}).get("message_id"): c for c in claims}
        self.assertIn("s:1", by_mid)
        a = by_mid["s:1"]
        self.assertEqual(a["source"], "assistant")
        self.assertTrue(a["evidence_verified"])
        self.assertEqual(a["provenance"]["speaker"], "assistant")
        # Human review still required; never auto-written.
        self.assertEqual(a["write_policy"], "confirm")
        self.assertFalse(a["auto_write"])
        self.assertIn("non-user speaker requires review", a["policy_reasons"])
        # The user claim in the same chunk is untouched.
        u = by_mid["s:0"]
        self.assertEqual(u["source"], "user")
        self.assertTrue(u["evidence_verified"])


if __name__ == "__main__":
    unittest.main()
