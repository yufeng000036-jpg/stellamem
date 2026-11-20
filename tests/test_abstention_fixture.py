"""Resident regression for the STELLAMEM_ABSTENTION fixture (v0.2) and
for the eval denominator fingerprint.

The 17-row abstention fixture used to live only in the upstream
llm-bench PR; this suite runs its verdict contract (structured rule ==
expected verdict, naive rule == its stated must-fail form) on every
repo test run, checks the v0.2 metadata fields, and pins the
denominator digest so the must-fail set cannot change silently.
"""

import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BENCH = REPO / "benchmark" / "longmemeval"
sys.path.insert(0, str(BENCH))


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fixture = load_module("abstention_fixture", BENCH / "abstention_fixture.py")
# The frozen runner evaluate.py is byte-locked and must not be imported
# for new behaviour; the denominator fingerprint lives in its post-hoc
# companion module instead.
denominator = load_module("longmemeval_denominator", BENCH / "denominator.py")

EXPECTED_DENOMINATOR_DIGEST = (
    "sha256:c647117f6f88743bde370ce1bffdae58b3ec48141b07e16e0cfab943145e2c2c"
)


class AbstentionFixtureTests(unittest.TestCase):
    def test_selftest_is_fully_green(self):
        import io
        from contextlib import redirect_stderr, redirect_stdout
        from unittest import mock

        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(sys, "argv", ["abstention_fixture.py"]),
            redirect_stdout(out),
            redirect_stderr(err),
        ):
            rc = fixture.main()
        self.assertEqual(rc, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["selftest"], "18/18")
        self.assertEqual(payload["version"], "0.2")
        self.assertEqual(payload["rows"], 17)
        self.assertEqual(payload["must_fail_arms"], 9)

    def test_every_row_carries_v02_fields(self):
        ids = {r[0] for r in fixture.ROWS}
        self.assertEqual(set(fixture.INJECTION_POINT), ids)
        self.assertEqual(set(fixture.COVERAGE_PATH_REF), ids)
        for fid in ids:
            self.assertIn(".", fixture.INJECTION_POINT[fid])
            self.assertTrue(
                fixture.COVERAGE_PATH_REF[fid].startswith("criteria_path:"),
                fid,
            )

    def test_denominator_digest_is_pinned(self):
        self.assertEqual(fixture.denominator_digest(), EXPECTED_DENOMINATOR_DIGEST)

    def test_denominator_digest_tracks_the_must_fail_set(self):
        arms = [r[0] for r in fixture.ROWS if r[7]]
        self.assertEqual(len(arms), 9)
        self.assertNotEqual(fixture.denominator_digest(arms[:-1]), EXPECTED_DENOMINATOR_DIGEST)
        # Order does not matter; the set does.
        self.assertEqual(
            fixture.denominator_digest(list(reversed(arms))),
            EXPECTED_DENOMINATOR_DIGEST,
        )


class EvalDenominatorDigestTests(unittest.TestCase):
    def rows(self, *ids):
        return [{"case_id_sha256": hashlib.sha256(i.encode()).hexdigest()} for i in ids]

    def test_order_insensitive_set_sensitive(self):
        a = denominator.denominator_digest(self.rows("q1", "q2", "q3"))
        b = denominator.denominator_digest(self.rows("q3", "q1", "q2"))
        c = denominator.denominator_digest(self.rows("q1", "q2"))
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertTrue(a.startswith("sha256:"))

    def test_empty_denominator_is_a_fingerprint_not_a_crash(self):
        d = denominator.denominator_digest([])
        self.assertTrue(d.startswith("sha256:"))

    def test_stamp_adds_fields_without_touching_cases(self):
        result = {"cases": self.rows("q1", "q2"), "summary": {}}
        stamped = denominator.stamp(result)
        self.assertEqual(stamped["denominator_n"], 2)
        self.assertEqual(
            stamped["denominator_digest"],
            denominator.denominator_digest(result["cases"]),
        )
        self.assertNotIn("denominator_digest", result)


if __name__ == "__main__":
    unittest.main()
