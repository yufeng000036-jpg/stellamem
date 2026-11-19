"""Public contract examples. No inference performance is implied by fixture replies."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from benchmark.jarvis_semantic import reader_v3 as exp


class ReaderStudyTest(unittest.TestCase):
    def test_public_three_arms(self):
        data = json.loads((exp.HERE / "reader_public.json").read_text("utf-8"))
        args = SimpleNamespace(
            blind=False, embed_model="fixture", reader_model="fixture", reader_port=1234
        )
        wires = []

        def endpoint(port, path, body):
            if path.endswith("/embeddings"):
                return {
                    "data": [
                        dict(index=i, embedding=[1.0] + [0.0] * 1023)
                        for i in range(len(body["input"]))
                    ]
                }
            text = body["messages"][1]["content"]
            materials = json.loads(text)["材料"]
            wires.append((body["messages"][0]["content"], text, body["model"], body["seed"]))
            reply = "NONE"
            if body["messages"][0]["content"] == exp.NEW:
                reply = materials[0].rsplit(" [", 1)[1].split("]", 1)[0]
            return {"choices": [{"message": {"content": reply}}]}

        logs = []
        with (
            patch.object(exp.scale.frozen, "post", endpoint),
            patch.object(exp.scale, "project", wraps=exp.scale.project) as proj,
        ):
            exp.study(data, args, logs.append)
        nq = len(data["questions"])
        self.assertEqual(len(proj.call_args_list), 3 * (2 * nq + 1))
        self.assertEqual(logs[-1]["rows"], 9 * nq)
        self.assertIsNone(logs[-1]["passed"])
        self.assertTrue(all(x[3] == 7 for x in wires))
        self.assertTrue(any(x[0] == exp.NEW for x in wires))
        rows = [r for r in logs if r["type"] == "question"]
        self.assertEqual(len(rows), 9 * nq)
        for tier in (1, 2, 3):
            for q in data["questions"]:
                p = {r["arm"]: r for r in rows if r["tier"] == tier and r["id"] == q["id"]}
                self.assertEqual(set(p), {"R0", "R1", "A"})
                for field in ("reader_candidates", "materials_hash", "payload_hash", "top5"):
                    self.assertEqual(p["R0"][field], p["R1"][field])
                for arm, prompt in (("A", exp.OLD), ("R0", exp.OLD), ("R1", exp.NEW)):
                    self.assertEqual(p[arm]["prompt_hash"], exp.digest(prompt))
                    if p[arm]["reader_calls"]:
                        text = next(
                            x[1]
                            for x in wires
                            if x[0] == prompt and exp.digest(x[1]) == p[arm]["payload_hash"]
                        )
                        self.assertEqual(
                            p[arm]["materials_hash"], exp.fingerprint(json.loads(text)["材料"])
                        )
        self.assertEqual(sum(r["reader_calls"] for r in rows), len(wires))

    def test_transitions_and_invalid_pairs(self):
        states = ("correct", "refusal", "wrong")

        def row(i, s):
            return dict(
                id=str(i),
                gold="id",
                correct=s == "correct",
                selected=None if s == "refusal" else "id",
                materials_hash="ordered",
                payload_hash="q",
                reader_candidates=["id"],
            )

        triples = []
        for a in states:
            for b in states:
                i = len(triples)
                triples.append(dict(R0=row(i, a), R1=row(i, b)))
        triples[-1]["R1"]["materials_hash"] = "other"
        metrics = exp.prompt_metrics(triples)
        self.assertEqual(metrics["net_positive"], 0)
        self.assertEqual(metrics["invalid_pairs"], ["8"])
        self.assertFalse(metrics["gates"]["identical"])
        self.assertEqual({len(v) for v in metrics["transitions"].values()}, {1})
        self.assertEqual(metrics["all_refusal_to_wrong"], 1)
        self.assertTrue(metrics["gates"]["refusal_to_wrong"])
        extra = dict(R0=row(9, "refusal"), R1=row(9, "correct"))
        passing = exp.prompt_metrics([extra, dict(R0=row(10, "wrong"), R1=row(10, "correct"))])
        self.assertTrue(all(passing["gates"].values()))
        self.assertFalse(exp.prompt_metrics([extra])["gates"]["net"])
