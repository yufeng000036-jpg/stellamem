"""Gate D4/D5 regression tests. Synthetic placeholder data only.

D4 (audit): blocked_weak used to count every blocked item, and the
empty-result note called every blocked item "weak", even when the item
was blocked for low signal without a weak flag.
D5 (audit): an injected item without a text field raised KeyError in
decide(), both on the allow_weak labelling path and on the final join.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))

import gate as G


def strong():
    return {"text": "占位强信号", "overlap": 0.9, "semantic": 0.8, "score": 0.9}


def weak_flagged():
    return {"text": "占位弱标记", "overlap": 0.9, "semantic": 0.8, "weak": True}


def low_signal():
    return {"text": "占位低信号", "overlap": 0, "semantic": 0.2, "weak": False}


class D4CountTests(unittest.TestCase):
    def test_blocked_weak_counts_only_weak_flagged(self):
        out = G.InjectionGate().decide(
            {"items": [strong(), weak_flagged(), low_signal()]}, query="占位查询")
        self.assertEqual(out["decision"], "inject_partial")
        self.assertEqual(len(out["blocked"]), 2)
        # D4 fix: blocked_weak is 1, not 2 (it used to count all blocked).
        self.assertEqual(out["blocked_weak"], 1)
        self.assertEqual(out["blocked_low_signal"], 1)
        self.assertEqual(out["blocked_total"], 2)
        # Legacy alias: blocked_low_score still counts non-weak blocks,
        # kept so existing callers/tests do not break; new code should
        # use blocked_low_signal.
        self.assertEqual(out["blocked_low_score"], 1)

    def test_blocked_weak_only_weak(self):
        out = G.InjectionGate().decide({"items": [weak_flagged()]}, query="占位查询")
        self.assertEqual(out["blocked_weak"], 1)
        self.assertEqual(out["blocked_low_signal"], 0)
        self.assertEqual(out["blocked_total"], 1)

    def test_allow_weak_blocks_nothing(self):
        out = G.InjectionGate().decide(
            {"items": [weak_flagged(), low_signal()]}, query="占位查询", allow_weak=True)
        self.assertEqual(out["blocked_weak"], 0)
        self.assertEqual(out["blocked_low_signal"], 0)
        self.assertEqual(out["blocked_total"], 0)
        self.assertEqual(len(out["inject"]), 2)


class D4NoteTests(unittest.TestCase):
    def test_weak_only_note_says_weak_flagged(self):
        out = G.InjectionGate().decide({"items": [weak_flagged()]}, query="占位查询")
        self.assertIn("已标记弱信号", out["note"])
        self.assertNotIn("低信号", out["note"])

    def test_low_signal_only_note_does_not_call_it_weak(self):
        out = G.InjectionGate().decide({"items": [low_signal()]}, query="占位查询")
        self.assertIn("低信号", out["note"])
        self.assertIn("未标记弱信号", out["note"])

    def test_mixed_note_names_both_kinds(self):
        out = G.InjectionGate().decide(
            {"items": [weak_flagged(), low_signal()]}, query="占位查询")
        self.assertIn("1 项已标记弱信号", out["note"])
        self.assertIn("1 项低信号", out["note"])


class D5MissingTextTests(unittest.TestCase):
    def test_strong_item_without_text_does_not_raise(self):
        out = G.InjectionGate().decide(
            {"items": [{"overlap": 0.9, "semantic": 0.8}]}, query="占位查询")
        self.assertEqual(out["decision"], "inject")
        self.assertEqual(out["injected"], 1)
        self.assertEqual(out["text"], "")

    def test_blocked_item_without_text_allow_weak_does_not_raise(self):
        out = G.InjectionGate().decide(
            {"items": [{"overlap": 0, "semantic": 0.2}]}, query="占位查询", allow_weak=True)
        self.assertEqual(out["injected"], 1)
        self.assertEqual(out["inject"][0]["text"], G.WEAK_LABEL)

    def test_blocked_item_without_text_not_allow_does_not_raise(self):
        out = G.InjectionGate().decide(
            {"items": [{"overlap": 0, "semantic": 0.2}]}, query="占位查询")
        self.assertEqual(out["decision"], "empty")
        self.assertEqual(out["text"], "")
        self.assertEqual(len(out["blocked"]), 1)

    def test_none_and_numeric_text_do_not_raise(self):
        out = G.InjectionGate().decide(
            {"items": [{"text": None, "overlap": 0.9, "semantic": 0.8},
                       {"text": 123, "overlap": 0.9, "semantic": 0.8}]},
            query="占位查询")
        self.assertEqual(out["decision"], "inject")
        self.assertEqual(out["text"], "\n123")


class D4LogEntryTests(unittest.TestCase):
    def test_log_entry_counts_match_return(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "gate_log.jsonl"
            g = G.InjectionGate(log_path=log)
            out = g.decide({"items": [strong(), weak_flagged(), low_signal()]},
                           query="占位查询")
            entry = g.recent(1)[0]
            self.assertEqual(entry["blocked_weak"], out["blocked_weak"])
            self.assertEqual(entry["blocked_low_signal"], out["blocked_low_signal"])
            self.assertEqual(entry["blocked_total"], out["blocked_total"])


if __name__ == "__main__":
    unittest.main()
