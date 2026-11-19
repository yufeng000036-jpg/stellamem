import json
import unittest
from unittest.mock import patch

from benchmark.jarvis_semantic import support_public as exp


class SupportPilotTest(unittest.TestCase):
    def test_measured_denominators(self):
        groups = json.loads(exp.CASES.read_text("utf-8"))
        supported = {(g["questions"][i], g["sources"][i // 2]) for g in groups for i in (0, 3)}
        singles = []

        def call(port, model, prompt, payload, budget):
            if prompt == exp.OLD:
                line = payload["材料"][0]
                return "[" + line.rsplit(" [", 1)[1].split("]", 1)[0] + "]", 0.1
            self.assertEqual(prompt, exp.CHECK)
            self.assertEqual(set(payload), {"问题", "原文"})
            key = payload["问题"], payload["原文"]
            singles.append(key)
            return ("YES" if key in supported else "NO"), 0.2

        logs = []
        with patch.object(exp, "chat", call):
            exp.run(groups, 8081, "fixture", logs.append)
        m = logs[-1]
        self.assertEqual(len(singles), len(set(singles)))
        self.assertEqual((m["selector_calls"], m["diagnostic_checker_calls"]), (24, 48))
        for role in m["forced"]:
            self.assertEqual(m["forced"][role]["n"], 12)
        self.assertEqual(m["actual"]["before_after"]["after"]["positive_correct"], 6)
        self.assertEqual(len(m["actual"]["cross_errors"]["explicit_blocks"]), 12)
        self.assertEqual(m["actual"]["before_after"]["after"]["cross_errors"], 0)
        self.assertEqual(m["actual"]["correct_selections"]["explicit_blocks"], [])

    def test_format_failures_are_not_explicit_blocks(self):
        case = json.loads(exp.CASES.read_text("utf-8"))[:1]

        def call(port, model, prompt, payload, budget):
            if prompt == exp.OLD:
                return "NONE", 0.1
            return "YES, probably", 0.1

        logs = []
        with patch.object(exp, "chat", call):
            exp.run(case, 8081, "fixture", logs.append)
        m = logs[-1]
        self.assertEqual(m["actual"]["selected"], 0)
        self.assertIsNone(m["actual"]["median_extra_seconds_per_selected"])
        self.assertTrue(
            all(
                t["invalid_block"] == t["n"] and t["explicit_block"] == 0
                for t in m["forced"].values()
            )
        )
