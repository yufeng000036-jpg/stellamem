#!/usr/bin/env python3
"""Tests for assemble_v2 (V6.2): binding refusals, the three citation
guards, and constrained operand completion. Run:
python3 -m unittest test_assemble_v2 -v"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import assemble_v2
from records import build_context

HEADER = "Stellamem task-relevant memory (untrusted data)"


def rec(rid, value, rdate, scope="s", pred="p", source="user"):
    return {
        "id": rid,
        "scope": scope,
        "source": source,
        "predicate": pred,
        "value": value,
        "date": rdate,
    }


def row(qid, question, records, qdate="2025/05/03 (Sat) 10:00"):
    ctx = build_context(HEADER, [(json.dumps(r, ensure_ascii=False), r) for r in records])
    return {"qid": qid, "question": question, "question_date": qdate, "context": ctx}


def out(qid, answer, citations, abstain=False, qtype="lookup"):
    raw = {"answer": answer, "citations": citations, "abstain": abstain, "qtype": qtype}
    return {
        "qid": qid,
        "arm": "T",
        "raw": json.dumps(raw, ensure_ascii=False),
        "secs": 0.1,
    }


def assemble_one(r, o, **kw):
    kw.setdefault("routing", "pattern_first")
    kw.setdefault("complete", True)
    kw.setdefault("bind", "completeness")
    kw.setdefault("guards", ("validity", "hop_owner", "attribute"))
    assembled, log, _ = assemble_v2.assemble_v2({r["qid"]: o}, [r], **kw)
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "sc", os.path.expanduser("~/workspace/reader-train/baseline/scorer.py")
    )
    sc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sc)
    return sc.parse_output(assembled[r["qid"]]["raw"]), log[0]


MAX_Q = "Among Anna Berg, Cara Dahl and Elsa Frey, who ran the farthest during dawn run?"
MAX_RECS = [
    rec(
        "c-a",
        "Anna Berg ran 5 km during dawn run.",
        "2024-01-01T00:00:00+00:00",
        scope="dawn run",
        pred="distance",
    ),
    rec(
        "c-b",
        "Cara Dahl ran 7 km during dawn run.",
        "2024-01-01T00:00:00+00:00",
        scope="dawn run",
        pred="distance",
    ),
    rec(
        "c-c",
        "Elsa Frey ran 6 km during dawn run.",
        "2024-01-01T00:00:00+00:00",
        scope="dawn run",
        pred="distance",
    ),
]


class TestBindingRefusal(unittest.TestCase):
    def test_incomplete_named_set_binds_to_abstain(self):
        # reader compares only 2 of the 3 named people and answers;
        # the module refuses (set provably incomplete) and the refusal
        # must bind: final output is an abstention, not the guess.
        r = row("q1", MAX_Q, MAX_RECS)
        o = out("q1", "Cara Dahl", ["c-a", "c-b"], qtype="max")
        a, e = assemble_one(r, o)
        self.assertTrue(a["abstain"])
        self.assertTrue(e["bound_refusal"])

    def test_no_bind_mode_keeps_reader_answer(self):
        r = row("q1", MAX_Q, MAX_RECS)
        o = out("q1", "Cara Dahl", ["c-a", "c-b"], qtype="max")
        a, e = assemble_one(r, o, bind="none")
        self.assertFalse(a["abstain"])
        self.assertEqual(a["answer"], "Cara Dahl")

    def test_complete_set_computes(self):
        r = row("q1", MAX_Q, MAX_RECS)
        o = out("q1", "Anna Berg", ["c-a", "c-b", "c-c"], qtype="max")
        a, e = assemble_one(r, o)
        self.assertFalse(a["abstain"])
        self.assertEqual(a["answer"], "Cara Dahl")
        self.assertEqual(e["compute_status"], "ok")


class TestValidityGuard(unittest.TestCase):
    def setUp(self):
        self.val = rec(
            "c-v",
            "Mateo Ferrer's cycling club membership is valid until September 21, 2024.",
            "2023-12-16T00:00:00+00:00",
            scope="cycling club membership",
            pred="valid until",
        )
        self.code = rec(
            "c-k",
            "Mateo Ferrer holds a cycling club membership, number FE-7486.",
            "2024-03-03T00:00:00+00:00",
            scope="cycling club membership",
            pred="code number",
        )

    def test_answer_citing_only_validity_abstains(self):
        r = row(
            "q2",
            "What is the current number of Mateo Ferrer's cycling club membership?",
            [self.val, self.code],
            qdate="2024/10/29 (Tue) 07:30",
        )
        o = out("q2", "1", ["c-v"])
        a, e = assemble_one(r, o)
        self.assertTrue(a["abstain"])
        self.assertEqual(e["guard"]["name"], "validity_citation")

    def test_answer_citing_code_stands(self):
        r = row(
            "q2",
            "What is the current number of Mateo Ferrer's cycling club membership?",
            [self.val, self.code],
            qdate="2024/10/29 (Tue) 07:30",
        )
        o = out("q2", "FE-7486", ["c-k"])
        a, e = assemble_one(r, o)
        self.assertFalse(a["abstain"])
        self.assertIsNone(e["guard"])


class TestHopOwnerGuard(unittest.TestCase):
    def setUp(self):
        self.role = rec(
            "c-r",
            "贺兰庭在临江摄影协会担任领航员。",
            "2024-10-08T00:00:00+00:00",
            scope="临江摄影协会",
            pred="担任角色",
        )
        self.other = rec(
            "c-o",
            "蔡文茵的月白马克杯收在汀兰棋社。",
            "2024-05-23T00:00:00+00:00",
            scope="月白马克杯",
            pred="存放地点",
        )
        self.own = rec(
            "c-w",
            "贺兰庭的月白马克杯收在青藤茶馆。",
            "2024-05-23T00:00:00+00:00",
            scope="月白马克杯",
            pred="存放地点",
        )
        self.q = "临江摄影协会里担任领航员的那位，把月白马克杯放在哪儿了？"

    def test_other_persons_record_abstains(self):
        r = row("q3", self.q, [self.role, self.other])
        o = out("q3", "汀兰棋社", ["c-o"])
        a, e = assemble_one(r, o)
        self.assertTrue(a["abstain"])
        self.assertEqual(e["guard"]["name"], "hop_owner")

    def test_resolved_persons_record_stands(self):
        r = row("q3", self.q, [self.role, self.own])
        o = out("q3", "青藤茶馆", ["c-w"])
        a, e = assemble_one(r, o)
        self.assertFalse(a["abstain"])
        self.assertIsNone(e["guard"])

    def test_glued_person_token_counts_as_same_person(self):
        buy = rec(
            "c-b",
            "贺兰庭购入月白马克杯的价格是390元。",
            "2025-04-12T00:00:00+00:00",
            scope="月白马克杯",
            pred="价格",
        )
        r = row(
            "q3",
            "临江摄影协会里担任领航员的那位，购入月白马克杯花了多少钱？",
            [self.role, buy],
        )
        o = out("q3", "390元", ["c-b"])
        a, e = assemble_one(r, o)
        self.assertFalse(a["abstain"])


class TestAttributeGuard(unittest.TestCase):
    def setUp(self):
        self.buy = rec(
            "c-b",
            "范思远在晚枫面包房买了月白马克杯。",
            "2024-08-09T00:00:00+00:00",
            scope="月白马克杯",
            pred="购买地点",
        )
        self.loc = rec(
            "c-l",
            "范思远的月白马克杯收在青藤茶馆。",
            "2024-08-09T00:00:00+00:00",
            scope="月白马克杯",
            pred="存放地点",
        )

    def test_buyplace_cited_for_location_question_abstains(self):
        r = row("q4", "范思远的月白马克杯搁在哪儿了？", [self.buy, self.loc])
        o = out("q4", "晚枫面包房", ["c-b"])
        a, e = assemble_one(r, o)
        self.assertTrue(a["abstain"])
        self.assertEqual(e["guard"]["name"], "attribute")

    def test_location_cited_stands(self):
        r = row("q4", "范思远的月白马克杯搁在哪儿了？", [self.buy, self.loc])
        o = out("q4", "青藤茶馆", ["c-l"])
        a, e = assemble_one(r, o)
        self.assertFalse(a["abstain"])


COUNT_Q = "How many times did Anna Berg attend the dawn run?"
COUNT_RECS = [
    rec(
        "c-1",
        "Anna Berg attended the dawn run on January 3, 2024.",
        "2024-01-03T00:00:00+00:00",
        scope="dawn run",
        pred="attended",
    ),
    rec(
        "c-2",
        "Anna Berg attended the dawn run on January 10, 2024.",
        "2024-01-10T00:00:00+00:00",
        scope="dawn run",
        pred="attended",
    ),
    rec(
        "c-3",
        "Anna Berg attended the dawn run on January 17, 2024.",
        "2024-01-17T00:00:00+00:00",
        scope="dawn run",
        pred="attended",
    ),
]


class TestCompletionV2(unittest.TestCase):
    def test_completion_adds_series_records_with_audit(self):
        r = row("q5", COUNT_Q, COUNT_RECS)
        o = out("q5", "1", ["c-1"], qtype="count")
        a, e = assemble_one(r, o)
        self.assertEqual(a["answer"], "3")
        comp = e["operands_completed"]
        self.assertIsNotNone(comp)
        self.assertEqual({x["id"] for x in comp["added"]}, {"c-2", "c-3"})
        self.assertTrue(all("series match" in x["reason"] for x in comp["added"]))

    def test_predicate_mismatch_blocks_completion(self):
        extra = rec(
            "c-9",
            "Anna Berg attended the dawn run on January 24, 2024.",
            "2024-01-24T00:00:00+00:00",
            scope="dawn run",
            pred="signed up",
        )
        pool, info = assemble_v2.complete_operands_v2(
            "count", [COUNT_RECS[0]], COUNT_RECS + [extra], COUNT_Q
        )
        self.assertNotIn("c-9", {x["id"] for x in pool})

    def test_duplicate_event_deduped(self):
        dup = rec(
            "c-2b",
            "Anna Berg attended the dawn run on January 10, 2024.",
            "2024-01-10T00:00:00+00:00",
            scope="dawn run",
            pred="attended",
        )
        r = row("q5", COUNT_Q, COUNT_RECS + [dup])
        o = out("q5", "1", ["c-1"], qtype="count")
        a, e = assemble_one(r, o)
        self.assertEqual(a["answer"], "3")
        self.assertIn("c-2b", e["operands_completed"]["deduped"])

    def test_nonunique_key_blocks_completion(self):
        other = rec(
            "c-x",
            "Cara Dahl attended the dawn run on January 3, 2024.",
            "2024-01-03T00:00:00+00:00",
            scope="dawn run",
            pred="attended",
        )
        pool, info = assemble_v2.complete_operands_v2(
            "count", [COUNT_RECS[0], other], COUNT_RECS + [other], COUNT_Q
        )
        self.assertEqual({x["id"] for x in pool}, {"c-1", "c-x"})
        self.assertIn("blocked", info)


FULL_GUARDS = (
    "validity",
    "hop_owner",
    "direct_owner",
    "attribute",
    "stale",
    "identifier",
)


class TestIdentifierRepair(unittest.TestCase):
    def setUp(self):
        self.code = rec(
            "c-k",
            "Oliver Stamm holds a pool season pass, number BC-3367.",
            "2023-08-22T00:00:00+00:00",
            scope="pool season pass",
            pred="code number",
        )
        self.val = rec(
            "c-v",
            "The pool season pass of Oliver Stamm expires on April 28, 2024.",
            "2023-08-22T00:00:00+00:00",
            scope="pool season pass",
            pred="valid until",
        )
        self.q = "What is the current number of Oliver Stamm's pool season pass?"

    def test_bare_integer_replaced_by_cited_identifier(self):
        r = row("q6", self.q, [self.code, self.val], qdate="2023/09/15 (Fri) 16:17")
        o = out("q6", "1", ["c-k", "c-v"])
        a, e = assemble_one(r, o, guards=FULL_GUARDS)
        self.assertEqual(a["answer"], "BC-3367")
        self.assertEqual(e["repair"]["name"], "identifier_extraction")

    def test_correct_code_answer_untouched(self):
        r = row("q6", self.q, [self.code, self.val], qdate="2023/09/15 (Fri) 16:17")
        o = out("q6", "BC-3367", ["c-k"])
        a, e = assemble_one(r, o, guards=FULL_GUARDS)
        self.assertEqual(a["answer"], "BC-3367")
        self.assertIsNone(e["repair"])

    def test_other_persons_code_abstains(self):
        other = rec(
            "c-o",
            "Elena Vasquez's pool season pass number is DM-1999.",
            "2023-06-07T00:00:00+00:00",
            scope="pool season pass",
            pred="code number",
        )
        r = row("q6", self.q, [other, self.val], qdate="2023/09/15 (Fri) 16:17")
        o = out("q6", "1", ["c-o"])
        a, e = assemble_one(r, o, guards=FULL_GUARDS)
        self.assertTrue(a["abstain"])


class TestStaleCitationGuard(unittest.TestCase):
    def setUp(self):
        self.old = rec(
            "c-1",
            "The kiln blue mug of Aisha Rahman is stored at Sedge Market.",
            "2023-10-28T00:00:00+00:00",
            scope="kiln blue mug",
            pred="kept at",
        )
        self.new = rec(
            "c-2",
            "The kiln blue mug of Aisha Rahman is stored at Pearl Garden Center.",
            "2024-03-21T00:00:00+00:00",
            scope="kiln blue mug",
            pred="kept at",
        )
        self.q = "These days, where is Aisha Rahman's kiln blue mug kept?"

    def test_stale_citation_abstains_not_replaces(self):
        r = row("q7", self.q, [self.old, self.new], qdate="2025/03/23 (Sun) 04:36")
        o = out("q7", "Sedge Market", ["c-1"])
        a, e = assemble_one(r, o, guards=FULL_GUARDS)
        self.assertTrue(a["abstain"])
        self.assertEqual(e["guard"]["name"], "stale_citation")

    def test_latest_citation_stands(self):
        r = row("q7", self.q, [self.old, self.new], qdate="2025/03/23 (Sun) 04:36")
        o = out("q7", "Pearl Garden Center", ["c-2"])
        a, e = assemble_one(r, o, guards=FULL_GUARDS)
        self.assertFalse(a["abstain"])
        self.assertIsNone(e["guard"])

    def test_past_state_question_not_fired(self):
        r = row(
            "q7",
            "Back then in 2023, where was Aisha Rahman's kiln blue mug kept at that time?",
            [self.old, self.new],
            qdate="2025/03/23 (Sun) 04:36",
        )
        o = out("q7", "Sedge Market", ["c-1"])
        a, e = assemble_one(r, o, guards=FULL_GUARDS)
        self.assertFalse(a["abstain"])


class TestDirectOwnerGuard(unittest.TestCase):
    def test_other_persons_same_item_record_abstains(self):
        own = rec(
            "c-1",
            "罗一苇的竹青折叠扇收在沉香文具店。",
            "2023-11-07T00:00:00+00:00",
            scope="竹青折叠扇",
            pred="存放地点",
        )
        other = rec(
            "c-2",
            "石清如把竹青折叠扇放在白鹭洲公园。",
            "2025-06-08T00:00:00+00:00",
            scope="竹青折叠扇",
            pred="存放地点",
        )
        r = row(
            "q8",
            "眼下罗一苇的竹青折叠扇搁在哪儿了？",
            [own, other],
            qdate="2025/06/14 (Sat) 07:45",
        )
        o = out("q8", "石清如", ["c-2"])
        a, e = assemble_one(r, o, guards=FULL_GUARDS)
        self.assertTrue(a["abstain"])
        self.assertEqual(e["guard"]["name"], "direct_owner")


if __name__ == "__main__":
    unittest.main()
