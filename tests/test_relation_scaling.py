"""P0-D: the relation engine must not scale as new-claim x all-history LLM calls.

The design resolves relations only within one stable slot (kind+subject+scope+
predicate) and, when several historical revisions exist for that slot, only ever
judges the single active one. These tests lock that in and measure the model-call
count as history grows.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compiler"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from claim_normalizer import claim_slot
from store import MemoryStore
from test_memory_safety import verified


class CountingModel:
    def __init__(self, relation="NEW"):
        self.calls = 0
        self.relation = relation

    def __call__(self, system, user):
        self.calls += 1
        return '[{"relation":"' + self.relation + '","confidence":0.99}]'


class RelationScalingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _ingest_new_slot(self, store, index, model):
        c = verified(
            predicate="topic_" + str(index),
            value="value_" + str(index),
            evidence="value " + str(index),
        )
        c["provenance"] = dict(
            c["provenance"], timestamp="2026-01-01T00:00:%02d+00:00" % (index % 60)
        )
        store.ingest(c, llm=model)

    def test_distinct_slots_never_call_relation_judge(self):
        # New slots have no active predecessor, so no model call is needed at all.
        model = CountingModel()
        store = MemoryStore(self.root)
        for i in range(50):
            self._ingest_new_slot(store, i, model)
        store.commit()
        self.assertEqual(model.calls, 0)
        self.assertEqual(store.counts["active"], 50)

    def test_growth_is_linear_not_quadratic(self):
        # One stable slot, N successive revisions: exactly one judge call each.
        counts = {}
        for n in (5, 20, 80):
            root = self.root / ("n" + str(n))
            root.mkdir()
            model = CountingModel(relation="SUPERSEDES")
            store = MemoryStore(root)
            for i in range(n):
                c = verified(
                    value="v" + str(i),
                    evidence="I now prefer v" + str(i),
                )
                c["provenance"] = dict(
                    c["provenance"], timestamp="2026-01-0%dT00:00:00+00:00" % (i % 9 + 1)
                )
                store.ingest(c, llm=model)
            store.commit()
            counts[n] = model.calls
        # Judge calls equal the number of revisions after the first, never n*n.
        self.assertEqual(counts[5], 4)
        self.assertEqual(counts[20], 19)
        self.assertEqual(counts[80], 79)
        self.assertLess(counts[80], 80 * 80)

    def test_only_the_active_revision_is_compared(self):
        # After supersession there is exactly one active record per slot, so the
        # candidate set cannot grow with history length.
        model = CountingModel(relation="SUPERSEDES")
        store = MemoryStore(self.root)
        for i in range(10):
            c = verified(value="v" + str(i), evidence="I now prefer v" + str(i))
            c["provenance"] = dict(
                c["provenance"], timestamp="2026-01-0%dT00:00:00+00:00" % (i % 9 + 1)
            )
            store.ingest(c, llm=model)
        store.commit()
        reloaded = MemoryStore(self.root)
        active = [v for v in reloaded.active_by_id.values() if v]
        self.assertEqual(len(active), 1)
        slot = claim_slot(verified())
        only_records = next(iter(reloaded.active_by_id.values()))
        self.assertEqual(claim_slot(only_records[0][1]), slot)


if __name__ == "__main__":
    unittest.main()
