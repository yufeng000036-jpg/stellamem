import copy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from benchmark.jarvis_semantic import support_v4 as exp


class SupportV4Test(unittest.TestCase):
    def test_public_three_arms(self):
        data = json.loads((exp.HERE / "reader_public.json").read_text("utf-8"))
        args = SimpleNamespace(
            blind=False, embed_model="fixture", reader_model="fixture", reader_port=8081
        )
        wires = []

        def selector(port, path, body):
            if path.endswith("/embeddings"):
                return {
                    "data": [
                        dict(index=i, embedding=[1.0] + [0.0] * 1023)
                        for i in range(len(body["input"]))
                    ]
                }
            self.assertEqual(body["messages"][0]["content"], exp.pilot.OLD)
            payload = json.loads(body["messages"][1]["content"])
            reply = (
                "NONE"
                if payload["问题"] == data["questions"][0]["text"]
                else "[" + payload["材料"][0].rsplit(" [", 1)[1].split("]", 1)[0] + "]"
            )
            wires.append((payload, reply))
            return {"choices": [{"message": {"content": reply}}]}

        checks = []

        def checker(port, model, prompt, payload, budget):
            self.assertEqual(set(payload), {"问题", "原文"})
            self.assertEqual(prompt, exp.pilot.CHECK)
            checks.append(payload)
            return "NO", 0.25

        logs = []
        with (
            patch.object(exp.scale.frozen, "post", selector),
            patch.object(exp.pilot, "chat", checker),
        ):
            exp.study(data, args, logs.append)
        self.assertEqual((logs[-1]["triples"], logs[-1]["rows"]), (18, 54))
        self.assertIsNone(logs[-1]["passed"])
        rows = [r for r in logs if r["type"] == "question"]
        self.assertEqual(sum(r["reader_calls"] for r in rows), len(wires))
        b = [r for r in rows if r["arm"] == "B0"]
        c = [r for r in rows if r["arm"] == "B1"]
        self.assertEqual(len(b), 18)
        self.assertEqual(sum(bool(r["selected"]) for r in b), len(checks))
        self.assertTrue(0 < len(checks) < 18)
        self.assertEqual(sum(r["checker_calls"] for r in c), len(checks))
        for up, down in zip(b, c):
            self.assertEqual(up["raw_output"], down["raw_output"])
            self.assertEqual(up["selected"], down["upstream_selected"])
            self.assertEqual(up["materials_hash"], down["materials_hash"])
            self.assertEqual(down["checker_verdict"], "NO" if up["selected"] else "SKIP")
            self.assertIsNone(down["selected"])
            if up["cross_negative"]:
                for parent in up["parents"].values():
                    self.assertEqual(
                        parent["delivered"], parent["claim_id"] in up["reader_candidates"]
                    )
            if up["selection_form"] == "bracket_delivered":
                self.assertIsNone(up["strict_selected"])
                self.assertIn(up["selected"], up["reader_candidates"])
        self.assertEqual(
            exp.PROTOCOL["checker_sha256"],
            "d6f74392bc778f899d91efa32e4e3bdac1182c2e1971c604a4dc7ef5fb56cba3",
        )

    def test_gates(self):
        def row(i, decision, positive=False):
            return {
                "B1": dict(
                    id=str(i),
                    cross_negative=not positive,
                    checker_calls=1,
                    checker_verdict=decision,
                    checker_seconds=i + 1,
                    upstream_parent_role="entity_parent" if not positive else None,
                    upstream_selected="c",
                    gold="c" if positive else None,
                )
            }

        rows = [row(i, d) for i, d in enumerate(("NO", "INVALID", "NO", "YES"))] + [
            row(4, "INVALID", True)
        ]
        result = exp.summarize(rows)
        self.assertEqual(
            (result["contribution"], result["positive_loss"], result["p95_seconds"]), ("pass", 1, 5)
        )
        self.assertEqual(result["cross_errors_by_parent"]["entity_parent"]["invalid"], ["1"])
        self.assertEqual(exp.summarize(rows[1:])["contribution"], exp.UNKNOWN)
        self.assertEqual(exp.summarize(rows + [row(5, "NO", True)])["contribution"], "fail")
        self.assertEqual(exp.summarize(rows + [row(5, "YES")])["contribution"], "fail")
        self.assertEqual(exp.combined(["pass", exp.UNKNOWN, "fail"]), exp.UNKNOWN)
        self.assertEqual(exp.combined(["pass", "fail", "pass"]), "fail")
        self.assertEqual(exp.summarize([])["median_seconds"], None)

    def test_parent_annotation(self):
        data = json.loads((exp.HERE / "reader_public.json").read_text("utf-8"))
        exp.validate(data, False)
        for pair in ([], ["s01", "s01"], ["s01", "missing"]):
            bad = copy.deepcopy(data)
            next(q for q in bad["questions"] if q.get("cross_negative"))["cross_sources"] = pair
            with self.assertRaises(ValueError):
                exp.validate(bad, False)
