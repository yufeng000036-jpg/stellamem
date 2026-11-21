#!/usr/bin/env python3
"""Judge must-fail arms: a permanent test OF THE JUDGE, not of the system.

Frozen from the LongMemEval stage-2 abstention audit (2026-11-19). Of the
30 abstention questions, the LLM judge marked 28 correct; the exact audit
confirmed 25. The three false positives are recorded below, verbatim from
the run's tracking table (tracking_stage2_final.csv):

- two rows where the run itself failed (ChunkFailure, integrity
  "execution_error;missing_response_state") and the judge counted the
  crash as a correct abstention;
- one row where the system answered an unanswerable question
  (integrity "answered_unanswerable", abstain=false) and the judge still
  marked it correct.

The ground truth of an abstention row is behavior (abstain vs answer),
not answer quality, and an execution error is neither a pass nor a
failed answer: it is a third state, indeterminate. Disposal agreed on
EigenFlux with 虾米 (2026-11-21) and approved by the repo owner the
same day: these rows are must-fail arms FOR THE JUDGE. A judge
implementation that passes one of them is itself red; the arm set is
pinned by digest so it cannot change silently; and a judge's readings
enter a comparable set only when its id and version are reported
alongside them (an unpinned judge is itself a drifting implementation).

Stdlib only. Run: python3 judge_arms.py (self-test).
"""

from __future__ import annotations

import hashlib
import json
import sys

VERDICTS = {"pass", "fail", "indeterminate"}

ARMS = (
    {
        "question_id": "gpt4_70e84552_abs",
        "failure_shape": "execution_error_counted_as_abstention",
        "recorded": {
            "judge_label": True,
            "correct_abstention": False,
            "integrity": "execution_error;missing_response_state",
            "error": "ChunkFailure",
        },
        "expected_verdict": "indeterminate",
    },
    {
        "question_id": "gpt4_372c3eed_abs",
        "failure_shape": "execution_error_counted_as_abstention",
        "recorded": {
            "judge_label": True,
            "correct_abstention": False,
            "integrity": "execution_error;missing_response_state",
            "error": "ChunkFailure",
        },
        "expected_verdict": "indeterminate",
    },
    {
        "question_id": "6456829e_abs",
        "failure_shape": "answered_unanswerable_judged_correct",
        "recorded": {
            "judge_label": True,
            "correct_abstention": False,
            "integrity": "answered_unanswerable",
            "abstain": False,
        },
        "expected_verdict": "fail",
    },
)


def _canonical_arms() -> str:
    return json.dumps(list(ARMS), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def arms_digest() -> str:
    return "sha256:" + hashlib.sha256(_canonical_arms().encode("utf-8")).hexdigest()


ARMS_DIGEST = "sha256:a90a57f588385e111b28cc0cf0f1d1c25a43b8faa7178d5b3bca5fc563474456"


def check_judge(verdicts, judge_id=None, judge_version=None, judge_fingerprint=None):
    """Grade a judge against the must-fail arms.

    verdicts maps question_id -> "pass" | "fail" | "indeterminate"
    (the verdict that judge gave that row). Returns a report dict:
    ok is True only when every arm got exactly its expected verdict;
    comparable is True only when judge_id and judge_version are both
    pinned, so the readings may enter a comparable set.
    """
    arms = []
    for arm in ARMS:
        qid = arm["question_id"]
        raw = verdicts.get(qid)
        got = str(raw).strip().lower() if raw is not None else None
        if got is None:
            status = "missing"
        elif got == arm["expected_verdict"]:
            status = "ok"
        elif got == "pass":
            status = "passed_must_fail"
        else:
            status = "wrong_axis"
        arms.append(
            {
                "question_id": qid,
                "failure_shape": arm["failure_shape"],
                "expected": arm["expected_verdict"],
                "got": got,
                "status": status,
            }
        )
    return {
        "ok": all(a["status"] == "ok" for a in arms),
        "comparable": bool(judge_id) and bool(judge_version),
        "judge": {"id": judge_id, "version": judge_version, "fingerprint": judge_fingerprint},
        "arms": arms,
        "arms_digest": arms_digest(),
    }


def self_test() -> bool:
    ids = [arm["question_id"] for arm in ARMS]
    recorded = check_judge(
        {qid: "pass" for qid in ids}, judge_id="stage2-judge", judge_version="as-run"
    )
    if recorded["ok"] or any(a["status"] != "passed_must_fail" for a in recorded["arms"]):
        return False
    correct_verdicts = {arm["question_id"]: arm["expected_verdict"] for arm in ARMS}
    correct = check_judge(correct_verdicts, judge_id="judge-x", judge_version="1.0")
    if not (correct["ok"] and correct["comparable"]):
        return False
    unpinned = check_judge(correct_verdicts, judge_id="judge-x")
    if unpinned["comparable"]:
        return False
    return arms_digest() == ARMS_DIGEST


def main() -> int:
    ok = self_test()
    print(json.dumps({"self_test_ok": ok, "arms_digest": arms_digest()}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
