"""Guardrail receipts: every gate decision leaves a checkable ticket.

皮皮虾's triple (EigenFlux, 2026-11-07): guardrail id, decision class,
rule-summary digest. Synthetic placeholder data only.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

import gate as G
import guardrail_receipt as R


def strong():
    return {"text": "占位强信号", "overlap": 0.9, "semantic": 0.8, "score": 0.9}


def weak_flagged():
    return {"text": "占位弱标记", "overlap": 0.9, "semantic": 0.8, "weak": True}


def low_signal():
    return {"text": "占位低信号", "overlap": 0, "semantic": 0.2, "weak": False}


class ReceiptShapeTests(unittest.TestCase):
    def test_receipt_has_the_three_parts(self):
        receipt = R.make_receipt(
            "injection-gate",
            "allow",
            "rule summary",
            {"rule_version": 1, "LOW_SCORE": 0.10},
        )
        self.assertEqual(receipt["guardrail_id"], "injection-gate")
        self.assertEqual(receipt["decision"], "allow")
        self.assertTrue(receipt["rule_digest"])
        self.assertTrue(receipt["receipt_id"].startswith("gr-"))
        self.assertEqual(receipt["rule_summary"], "rule summary")
        self.assertEqual(receipt["rule_inputs"]["LOW_SCORE"], 0.10)
        self.assertEqual(receipt["rule_version"], 1)

    def test_verify_passes_on_a_fresh_receipt(self):
        receipt = R.make_receipt("injection-gate", "block", "rule summary", {"rule_version": 1})
        self.assertTrue(R.verify_receipt(receipt))

    def test_verify_fails_when_tampered(self):
        base = R.make_receipt("injection-gate", "block", "rule summary", {"rule_version": 1})
        for mutate in (
            lambda r: r.update(decision="allow"),
            lambda r: r.update(rule_summary="different summary"),
            lambda r: r["rule_inputs"].update(LOW_SCORE=0.99),
            lambda r: r.update(rule_digest="0" * 64),
            lambda r: r.update(rule_version=2),
            lambda r: r.update(guardrail_id="other-gate"),
        ):
            tampered = json.loads(json.dumps(base))
            mutate(tampered)
            self.assertFalse(R.verify_receipt(tampered), f"tamper not caught: {tampered}")

    def test_verify_rejects_garbage(self):
        self.assertFalse(R.verify_receipt({}))
        self.assertFalse(R.verify_receipt({"decision": "allow"}))
        self.assertFalse(R.verify_receipt(None))
        self.assertFalse(R.verify_receipt("not a dict"))

    def test_unknown_decision_class_rejected(self):
        with self.assertRaises(ValueError):
            R.make_receipt("injection-gate", "maybe", "rule summary", {"rule_version": 1})

    def test_rule_version_bump_changes_digest(self):
        a = R.make_receipt("injection-gate", "allow", "same summary", {"rule_version": 1})
        b = R.make_receipt("injection-gate", "allow", "same summary", {"rule_version": 2})
        self.assertNotEqual(a["rule_digest"], b["rule_digest"])
        self.assertTrue(R.verify_receipt(a))
        self.assertTrue(R.verify_receipt(b))


class GateReceiptTests(unittest.TestCase):
    def test_decide_returns_receipt_with_decision_class(self):
        cases = [
            ([strong()], "inject", "allow"),
            ([weak_flagged()], "empty", "block"),
            ([strong(), weak_flagged()], "inject_partial", "partial"),
        ]
        for items, decision, decision_class in cases:
            out = G.InjectionGate().decide({"items": items}, query="占位查询")
            self.assertEqual(out["decision"], decision)
            receipt = out["receipt"]
            self.assertEqual(receipt["guardrail_id"], "injection-gate")
            self.assertEqual(receipt["decision"], decision_class)
            self.assertTrue(R.verify_receipt(receipt))

    def test_receipt_in_log_entry_and_audit_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "gate_log.jsonl"
            audit = Path(tmp) / "audit.md"
            out = G.InjectionGate(log_path=log, audit_path=audit).decide(
                {"items": [strong(), weak_flagged()]}, query="占位查询"
            )
            entry = json.loads(log.read_text(encoding="utf-8").strip().splitlines()[-1])
            self.assertEqual(entry["receipt"]["receipt_id"], out["receipt"]["receipt_id"])
            self.assertTrue(R.verify_receipt(entry["receipt"]))
            line = audit.read_text(encoding="utf-8")
            self.assertIn(out["receipt"]["receipt_id"], line)
            self.assertIn("class=partial", line)

    def test_receipt_survives_allow_weak(self):
        out = G.InjectionGate().decide(
            {"items": [weak_flagged(), low_signal()]}, query="占位查询", allow_weak=True
        )
        self.assertEqual(out["decision"], "inject")
        receipt = out["receipt"]
        self.assertEqual(receipt["decision"], "allow")
        self.assertTrue(receipt["detail"]["allow_weak"])
        self.assertTrue(R.verify_receipt(receipt))


if __name__ == "__main__":
    unittest.main()
