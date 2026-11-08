"""Unit tests for the verify_replay.py raw-output and claim-replay checks.

Synthetic fixtures only: two questions x four arms, a matching raw/ tree
and a minimal claim-replay dir. No model calls, no network.
"""

import hashlib
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark/longmemeval"
SPEC = importlib.util.spec_from_file_location("verify_replay", BENCH / "verify_replay.py")
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)


def hid(session_id):
    return hashlib.sha256(session_id.encode()).hexdigest()[:16]


def make_case(qid, *, judge_main=True, error=None):
    def arm(*, judge, physical=1, cache=0):
        return {
            "arm": "main",
            "error": error,
            "extracted": 2,
            "retrieved_sessions": [hid("s1"), hid("s2")],
            "cited_sessions": [hid("s1")],
            "citation_valid": True,
            "exact_match": True,
            "correct_abstention": None,
            "retrieval_recall_at_1": 1.0,
            "retrieval_recall_at_5": 1.0,
            "extracted_evidence_turn_coverage": 1.0,
            "logical_calls": 3,
            "physical_calls": physical,
            "cache_hits": cache,
            "elapsed_seconds": 0.5,
            "peak_rss_kib": 1000,
            "abstain": False,
            "answer_sha256": "a" * 64,
            "answer_text": "42",
            "extracted_turns": ["s1:0", "s2:1"],
            "judge": {
                "judge_response": "yes" if judge else "no",
                "judge_label": judge,
                "hypothesis": "42",
            },
        }

    main = arm(judge=judge_main)
    candidate = arm(judge=judge_main, physical=0, cache=3)
    candidate["arm"] = "candidate"
    none = arm(judge=False)
    none["arm"] = "none"
    none["extracted"] = 0
    none["retrieved_sessions"] = []
    none["cited_sessions"] = []
    direct = arm(judge=True)
    direct["arm"] = "direct"
    direct["extracted"] = 0
    return {
        "question_id": qid,
        "category": "multi-session",
        "arms": {"main": main, "candidate": candidate, "none": none, "direct": direct},
    }


def write_raw(root, cases, *, tamper=None):
    """Write shard0/raw/<qid>_<arm>.json mirroring the cases (raw ids readable)."""
    raw_dir = Path(root) / "shard0" / "raw"
    raw_dir.mkdir(parents=True)
    for case in cases:
        for arm_name, rec in case["arms"].items():
            raw = {
                "arm": arm_name,
                "error": rec["error"],
                "extracted": rec["extracted"],
                "retrieved_sessions": [
                    s for s in ("s1", "s2") if hid(s) in rec["retrieved_sessions"]
                ],
                "cited_sessions": [s for s in ("s1", "s2") if hid(s) in rec["cited_sessions"]],
                "citation_valid": rec["citation_valid"],
                "logical_calls": rec["logical_calls"],
                "physical_calls": rec["physical_calls"],
                "cache_hits": rec["cache_hits"],
            }
            if tamper == (case["question_id"], arm_name):
                raw["extracted"] += 1
            (raw_dir / f"{case['question_id']}_{arm_name}.json").write_text(json.dumps(raw))
    return raw_dir.parent.parent


def write_replay(root, cases, *, drop_sidecar_for=None):
    """Write claim-replay-full/ with manifest, replay_results and sidecars."""
    replay = Path(root) / "claim-replay-full"
    diag = replay / "diagnostics"
    diag.mkdir(parents=True)
    manifest = {}
    replayed = {}
    for case in cases:
        qid = case["question_id"]
        sha = hashlib.sha256(qid.encode()).hexdigest()[:16]
        # manifest keys are sha256(question text) in production; any stable
        # 16-hex key works for the check as long as manifest/sidecar agree.
        manifest[sha] = {"question_id": qid}
        main = case["arms"]["main"]
        replayed[qid] = {
            "error": main["error"],
            "physical_calls": 0,
            "extracted": main["extracted"],
            "retrieved_sessions": ["s1", "s2"],
            "cited_sessions": ["s1"],
        }
        if qid != drop_sidecar_for:
            (diag / (sha + ".json")).write_text(json.dumps({"question_id": qid}))
    (replay / "manifest.json").write_text(json.dumps(manifest))
    (replay / "replay_results.json").write_text(json.dumps(replayed))
    return replay


class RawCheckTests(unittest.TestCase):
    def test_clean_raw_tree_passes(self):
        cases = [make_case("q1"), make_case("q2", judge_main=False)]
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            raw_root = write_raw(tmp, cases)
            problems, notes = verify.check_raw_consistency(cases, raw_root)
            self.assertEqual(problems, [])
            self.assertEqual(notes["raw_files"], 8)
            self.assertEqual(notes["candidate_physical_zero"], 2)

    def test_tampered_raw_is_caught(self):
        cases = [make_case("q1")]
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            raw_root = write_raw(tmp, cases, tamper=("q1", "main"))
            problems, _notes = verify.check_raw_consistency(cases, raw_root)
            self.assertTrue(any("q1/main.extracted" in p for p in problems))

    def test_flipped_judge_label_is_caught(self):
        cases = [make_case("q1")]
        cases[0]["arms"]["main"]["judge"]["judge_label"] = False  # response still "yes"
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            raw_root = write_raw(tmp, cases)
            problems, _notes = verify.check_raw_consistency(cases, raw_root)
            self.assertTrue(any("judge q1/main" in p for p in problems))
            self.assertTrue(any("candidate!=main q1.judge_label" in p for p in problems))

    def test_missing_raw_is_caught(self):
        cases = [make_case("q1")]
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp)
            problems, _notes = verify.check_raw_consistency(cases, raw_root)
            self.assertTrue(any("missing" in p for p in problems))


class ReplayCheckTests(unittest.TestCase):
    def test_clean_replay_passes(self):
        cases = [make_case("q1"), make_case("q2", judge_main=False)]
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            replay = write_replay(tmp, cases)
            problems, notes = verify.check_replay_consistency(cases, replay)
            self.assertEqual(problems, [])
            self.assertEqual(notes["replayed"], 2)
            self.assertEqual(notes["skipped"], [])

    def test_skipped_question_with_sidecar_passes(self):
        cases = [make_case("q1"), make_case("q2")]
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            replay = write_replay(tmp, cases)
            # Simulate the logged run skipping q2: drop it from replay_results
            # but keep its sidecar.
            replayed = json.loads((replay / "replay_results.json").read_text())
            del replayed["q2"]
            (replay / "replay_results.json").write_text(json.dumps(replayed))
            problems, notes = verify.check_replay_consistency(cases, replay)
            self.assertEqual(problems, [])
            self.assertEqual(notes["skipped"], ["q2"])

    def test_missing_sidecar_is_caught(self):
        cases = [make_case("q1")]
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            replay = write_replay(tmp, cases, drop_sidecar_for="q1")
            problems, _notes = verify.check_replay_consistency(cases, replay)
            self.assertTrue(any("no diagnostics sidecar" in p for p in problems))

    def test_chunk_failure_needs_no_sidecar(self):
        cases = [make_case("q1", error="ChunkFailure")]
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            replay = write_replay(tmp, cases, drop_sidecar_for="q1")
            problems, _notes = verify.check_replay_consistency(cases, replay)
            self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
