"""Repo merge.py must reproduce the archived shard merges, offline.

Builds a synthetic 4-shard run (500 rows, the merge asserts exactly 500)
and checks the repo copy of merge.py against the frozen aggregate() plus
its fail-closed behaviour on duplicate questions and missing shards.
No model calls, no network.
"""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark/longmemeval"
sys.path.insert(0, str(BENCH))  # evaluate.py does `from freeze import ...`
SPEC = importlib.util.spec_from_file_location("longmemeval_evaluate", BENCH / "evaluate.py")
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)

ARMS = ("main", "candidate", "none", "direct")


def make_arm(*, judge, error=None, extracted=5, logical=3, physical=1, cache=2):
    return {
        "error": error,
        "extraction_failure_code": "RuntimeError" if error == "ChunkFailure" else None,
        "extracted": extracted,
        "exact_match": True,
        "correct_abstention": None,
        "retrieval_recall_at_1": 1.0,
        "retrieval_recall_at_5": 1.0,
        "citation_valid": True,
        "extracted_evidence_turn_coverage": 1.0,
        "elapsed_seconds": 0.5,
        "peak_rss_kib": 1000,
        "logical_calls": logical,
        "physical_calls": physical,
        "cache_hits": cache,
        "judge": {"judge_label": judge},
    }


def make_case(i, *, judge_main=True, error=None):
    return {
        "question_id": f"q{i:04d}",
        "case_id_sha256": f"{i:064x}",
        "category": "multi-session",
        "in_manifest": i % 2 == 0,
        "arms": {
            "main": make_arm(judge=judge_main, error=error),
            "candidate": make_arm(judge=judge_main, error=error, physical=0, cache=3),
            "none": make_arm(judge=False, extracted=0, logical=1, physical=1, cache=0),
            "direct": make_arm(judge=True, extracted=0, logical=1, physical=1, cache=0),
        },
    }


def write_run(root, cases_per_shard=125):
    """Write a synthetic run dir; returns (run_dir, all_rows)."""
    run_dir = Path(root) / "run"
    full = run_dir / "work" / "full"
    rows = []
    for shard in range(4):
        rows_shard = [
            make_case(shard * cases_per_shard + j, judge_main=(j % 3 != 0))
            for j in range(cases_per_shard)
        ]
        rows.extend(rows_shard)
        shard_dir = full / f"shard{shard}"
        shard_dir.mkdir(parents=True)
        (shard_dir / "results.json").write_text(
            json.dumps(
                {
                    "cases": rows_shard,
                    "preflight_usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                    "repo_sha": "synthetic",
                    "dataset_sha256": "synthetic",
                    "elapsed_seconds": 1.0,
                }
            )
        )
        (shard_dir / "usage.jsonl").write_text(
            json.dumps(
                {
                    "kind": "answer",
                    "prompt_tokens": 100,
                    "completion_tokens": 10,
                    "prompt_cache_hit_tokens": 0,
                    "prompt_cache_miss_tokens": 100,
                }
            )
            + "\n"
        )
    return run_dir, rows


class MergeTests(unittest.TestCase):
    def run_merge(self, run_dir):
        return subprocess.run(
            [
                sys.executable,
                str(BENCH / "merge.py"),
                "--run-dir",
                str(run_dir),
                "--repo",
                str(BENCH),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )

    def test_output_matches_frozen_aggregate(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, rows = write_run(tmp)
            proc = self.run_merge(run_dir)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            out = json.loads((run_dir / "work" / "full" / "results.json").read_text())
            self.assertEqual(out["n_questions"], 500)
            self.assertEqual(out["repo_sha"], "synthetic")
            want = evaluation.aggregate(rows)
            for arm in ARMS:
                for key, value in want[arm].items():
                    self.assertEqual(out["summary"][arm][key], value, f"{arm}.{key}")
                judge_n = sum(1 for r in rows if r["arms"][arm]["judge"]["judge_label"])
                self.assertAlmostEqual(out["summary"][arm]["judge_accuracy"], judge_n / 500)
            # Cost: 4 usage lines x (100 miss tokens, 10 out) at flash off-peak.
            per_line = 100 * 0.15 / 1_000_000 + 10 * 0.6 / 1_000_000
            self.assertAlmostEqual(out["cost"]["total_usd"], round(4 * per_line, 4))
            self.assertEqual(out["cost"]["total_calls"], 4)
            self.assertEqual(out["budget_breach_question_ids"], [])
            self.assertEqual(out["extract_failed_main_question_ids"], [])
            self.assertEqual(
                [c["question_id"] for c in out["cases"]],
                [r["question_id"] for r in sorted(rows, key=lambda r: r["case_id_sha256"])],
            )

    def test_duplicate_question_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, rows = write_run(tmp)
            shard1 = json.loads((run_dir / "work" / "full" / "shard1" / "results.json").read_text())
            shard1["cases"][0]["question_id"] = shard1["cases"][1]["question_id"]
            (run_dir / "work" / "full" / "shard1" / "results.json").write_text(json.dumps(shard1))
            proc = self.run_merge(run_dir)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("duplicate question_ids", proc.stderr)

    def test_missing_shard_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, _rows = write_run(tmp)
            (run_dir / "work" / "full" / "shard3" / "results.json").unlink()
            proc = self.run_merge(run_dir)
            self.assertNotEqual(proc.returncode, 0)

    def test_default_run_dir_is_script_dir(self):
        # No flags: --run-dir defaults to the script's own directory, which
        # holds no work/full -- so it must fail loudly, not silently succeed.
        proc = subprocess.run(
            [sys.executable, str(BENCH / "merge.py")],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertNotEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
