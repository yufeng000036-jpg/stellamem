#!/usr/bin/env python3
"""STELLAMEM_ABSTENTION v0.2 (in-repo resident copy)

Vendored from the llm-bench upstream fixture v0.1 (usermbzlj/API_Playground
PR #7, stellamem_abstention_v0_1.py) so the verdict contract runs in this
repo's own regression suite instead of only upstream. v0.2 adds the three
fields agreed with Nova (EigenFlux, 2026-11-18): per-row injection_point
and coverage_path_ref, plus a denominator_digest over the must-fail arms
(canonical deep-sorted JSON, sha256). Rows, rules and verdicts are byte-
identical to v0.1; only metadata and the self-test surface grew.

Original v0.1 header follows.

STELLAMEM_ABSTENTION v0.1

De-identified observation rows from Stellamem (open-source memory system,
github.com/yufeng000036-jpg/stellamem), contributed as a对照 fixture for
llm-bench (usermbzlj/API_Playground, integrations/llm-bench/).

Settled format (Grok Bot, EigenFlux 2026-11-10 10:01, msg 378363590492553216):
two layers, not a replacement of the old four-tuple.
- HEADER (metadata, always present): fixture_id + criteria_version.
  criteria_version must coexist with the expected verdict: a criteria
  upgrade must not silently flip an old fixture, so the version and the
  expected verdict are both in the submission.
- BODY (the submission columns): 输入 / 期望判词 / 必败形态 / 正控对照.
  Mapping from the old four-tuple: expected_verdict -> 期望判词,
  naive_wrong -> 必败形态. fixture_id and criteria_version are NOT body
  columns and must not replace them; the body must not drop the version.
Verdict cells carry LITERAL values only; explanations live in comments
and META, never in the verdict cells. Every row carries dual attribution:
a Stellamem validation source AND an llm-bench counterpart (S21/S22
shapes or Grok Bot integrity slots, EigenFlux item 378154720675495936).

Only assertions and expected verdicts, not implementations: each shape is a
minimal naive rule (provably wrong on the must-fail arm) against a structured
rule (the assertion). The rules are the assertions, not a reimplementation
of Stellamem. All numbers are verified against the originals (see META);
the negative-sample pool cases are fictional; LongMemEval IDs are a public
benchmark. No private data. analyze_only. Stdlib only.
Run: python3 stellamem_abstention_v0_1.py
"""
from __future__ import annotations
import hashlib
import json, os, sys

# ---------- verdict vocabulary (literal tokens only) ----------
VERDICTS = {
    "REFUSE", "ANSWER", "UNSUPPORTED", "SUPPORTED", "UNKNOWN",
    "NOT_COMPLETED", "COMPLETED", "DETECTED", "MISSED", "VIOLATION",
    "INDETERMINATE", "ABSENT",
}

# ---------- READER: validity metadata before answering ----------
def reader_naive(record):
    # naive reader answers from the record regardless of validity metadata
    return "ANSWER"

def reader_struct(record):
    if record.get("valid_to") == "past":
        return "REFUSE"
    if record.get("status") != "active":
        return "REFUSE"
    return "ANSWER"

# ---------- JUDGE: reasoning must be grounded ----------
def judge_naive(case):
    # naive judge: verdict by surface token match (answer token in evidence)
    return "SUPPORTED" if case["answer"] in case["evidence_text"] else "UNSUPPORTED"

def judge_struct(case):
    # structured judge: every content token of the reasoning must be grounded
    # in the question or the evidence; one fabricated token voids support
    grounded = set(case["question_tokens"]) | set(case["evidence_tokens"])
    if any(tok not in grounded for tok in case["reasoning_tokens"]):
        return "UNSUPPORTED"
    return "SUPPORTED" if case["answer"] in case["evidence_text"] else "UNSUPPORTED"

# ---------- SUFFICIENCY: thin evidence must be UNKNOWN ----------
def sufficiency_naive(case):
    # naive reader answers on thin or no evidence
    return "ANSWER"

def sufficiency_struct(case):
    return "UNKNOWN" if case["evidence"] in ("none", "insufficient") else "ANSWER"

# ---------- LEDGER: completion only from the attempt ledger ----------
def ledger_naive(run):
    # naive completion: exit 0 plus a log file present
    return "COMPLETED" if run["exit_code"] == 0 and run["log_present"] else "NOT_COMPLETED"

def ledger_struct(run):
    # structured completion: a completed attempt must exist in the ledger
    return "COMPLETED" if run["ledger_completed"] else "NOT_COMPLETED"

# ---------- NEGCTRL: one changed input, full chain ----------
def negctrl_naive(case):
    # criterion-entry-only check: sees only whether the entry changed
    return "DETECTED" if case["entry_changed"] else "MISSED"

def negctrl_struct(case):
    # full-chain check: detection requires the chain to block the fault
    return "DETECTED" if case["chain_blocked"] else "MISSED"

# ---------- VERDICT_TAXONOMY: four states stay separate ----------
def taxonomy_naive(verdict):
    # naive taxonomy folds UNKNOWN into VIOLATION
    return "VIOLATION" if verdict == "UNKNOWN" else verdict

def taxonomy_struct(verdict):
    return verdict

# ---------- EMPTY_RESULT: no receipt means INDETERMINATE ----------
def empty_naive(case):
    # naive reader writes "no such thing" with no receipt
    return "ABSENT"

def empty_struct(case):
    return "ABSENT" if case["receipt"] else "INDETERMINATE"

# ---------- rows: fixture_id, criteria_version, input, shape, args,
#               expected_verdict, naive_wrong, must_fail, reason_code,
#               receipt present ----------
# 输入 is the literal input fed to the rule (derived from args, no new data).
# 期望判词 = expected_verdict (literal). 必败形态 = naive_wrong (literal;
# null on a green twin, which IS the positive control). 正控对照 = the
# green twin's fixture_id (a twin points at itself).
ROWS = [
  # row 1: negative-sample reader case, three arms all 4/7, different misses
  ("neg.reader.three-arm.v1", "neg-reader-v1",
   "record{valid_to=past, status=expired}", "READER",
   ({"valid_to": "past", "status": "expired"},), "REFUSE", "ANSWER", True,
   "VALIDITY_METADATA_MISSING", True),
  ("neg.reader.three-arm.gt.v1", "neg-reader-v1",
   "record{valid_to=future, status=active}", "READER",
   ({"valid_to": "future", "status": "active"},), "ANSWER", "ANSWER", False,
   "", True),
  # row 2: expired / non-active cases, all three arms missed both
  ("neg-expired-01", "neg-reader-v1",
   "record{valid_to=past, status=active}", "READER",
   ({"valid_to": "past", "status": "active"},), "REFUSE", "ANSWER", True,
   "EXPIRED", True),
  ("neg-status-01", "neg-reader-v1",
   "record{valid_to=future, status=archived}", "READER",
   ({"valid_to": "future", "status": "archived"},), "REFUSE", "ANSWER", True,
   "STATUS_NOT_ACTIVE", True),
  ("neg-valid-coupon.gt.v1", "neg-reader-v1",
   "record{valid_to=future, status=active}", "READER",
   ({"valid_to": "future", "status": "active"},), "ANSWER", "ANSWER", False,
   "", True),
  # row 3: 0ddfec37 — judge right in verdict, fabricated reasoning (rugby)
  ("0ddfec37_abs", "abstain-1b-v1",
   "question_tokens=[收藏, 头三个月, 新增], evidence_text='三个月内已收集15个；近几个月新增20个', answer=15, reasoning_tokens=[橄榄球]",
   "JUDGE",
   ({"question_tokens": ["收藏", "头三个月", "新增"],
     "evidence_tokens": ["三个月内", "15", "近几个月", "新增", "20"],
     "evidence_text": "三个月内已收集15个；近几个月新增20个",
     "answer": "15", "reasoning_tokens": ["橄榄球"]},),
   "UNSUPPORTED", "SUPPORTED", True, "FABRICATED_REASONING", True),
  ("0ddfec37.grounded.gt.v1", "abstain-1b-v1",
   "question_tokens=[收藏, 头三个月, 新增], evidence_text='三个月内已收集15个；近几个月新增20个', answer=15, reasoning_tokens=[三个月内, 15]",
   "JUDGE",
   ({"question_tokens": ["收藏", "头三个月", "新增"],
     "evidence_tokens": ["三个月内", "15", "近几个月", "新增", "20"],
     "evidence_text": "三个月内已收集15个；近几个月新增20个",
     "answer": "15", "reasoning_tokens": ["三个月内", "15"]},),
   "SUPPORTED", "SUPPORTED", False, "", True),
  # row 4: MUST_JUDGE_UNKNOWN — thin evidence must be UNKNOWN
  ("must.judge.unknown.v1", "grok-integrity-20261109",
   "evidence=none", "SUFFICIENCY",
   ({"evidence": "none"},), "UNKNOWN", "ANSWER", True,
   "INSUFFICIENT_EVIDENCE", True),
  ("must.judge.unknown.gt.v1", "grok-integrity-20261109",
   "evidence=sufficient", "SUFFICIENCY",
   ({"evidence": "sufficient"},), "ANSWER", "ANSWER", False, "", True),
  # row 5: COMPLETION_STATUS — completion only from the attempt ledger
  ("completion.from.ledger.v1", "grok-integrity-20261109",
   "exit_code=0, log_present=true, ledger_completed=false", "LEDGER",
   ({"exit_code": 0, "log_present": True, "ledger_completed": False},),
   "NOT_COMPLETED", "COMPLETED", True, "NO_COMPLETED_ATTEMPT_IN_LEDGER", True),
  ("completion.from.ledger.gt.v1", "grok-integrity-20261109",
   "exit_code=0, log_present=true, ledger_completed=true", "LEDGER",
   ({"exit_code": 0, "log_present": True, "ledger_completed": True},),
   "COMPLETED", "COMPLETED", False, "", True),
  # row 6: NEGCTRL — one changed input, full chain
  ("negctrl.single.change.v1", "grok-integrity-20261109",
   "entry_changed=false, chain_blocked=true", "NEGCTRL",
   ({"entry_changed": False, "chain_blocked": True},),
   "DETECTED", "MISSED", True, "ENTRY_ONLY_CHECK", True),
  ("negctrl.single.change.gt.v1", "grok-integrity-20261109",
   "entry_changed=true, chain_blocked=true", "NEGCTRL",
   ({"entry_changed": True, "chain_blocked": True},),
   "DETECTED", "DETECTED", False, "", True),
  # row 7: FOUR_VERDICT_STATES — UNKNOWN must not fold into VIOLATION
  ("four.verdict.unknown.v1", "grok-integrity-20261109",
   "verdict=UNKNOWN", "VERDICT_TAXONOMY",
   ("UNKNOWN",), "UNKNOWN", "VIOLATION", True,
   "UNKNOWN_FOLDED_TO_VIOLATION", True),
  ("four.verdict.unknown.gt.v1", "grok-integrity-20261109",
   "verdict=VIOLATION", "VERDICT_TAXONOMY",
   ("VIOLATION",), "VIOLATION", "VIOLATION", False, "", True),
  # row 8: empty result needs a receipt
  ("empty.result.needs.receipt.v1", "grok-integrity-20261109",
   "result=empty, receipt=None", "EMPTY_RESULT",
   ({"result": "empty", "receipt": None},), "INDETERMINATE", "ABSENT", True,
   "NO_RECEIPT", True),
  ("empty.result.needs.receipt.gt.v1", "grok-integrity-20261109",
   "result=empty, receipt=present", "EMPTY_RESULT",
   ({"result": "empty", "receipt": "present"},), "ABSENT", "ABSENT", False,
   "", True),
]

# 正控对照: must-fail arm -> its green twin; a green twin -> itself.
POSITIVE_CONTROL = {
  "neg.reader.three-arm.v1": "neg.reader.three-arm.gt.v1",
  "neg.reader.three-arm.gt.v1": "neg.reader.three-arm.gt.v1",
  "neg-expired-01": "neg-valid-coupon.gt.v1",
  "neg-status-01": "neg-valid-coupon.gt.v1",
  "neg-valid-coupon.gt.v1": "neg-valid-coupon.gt.v1",
  "0ddfec37_abs": "0ddfec37.grounded.gt.v1",
  "0ddfec37.grounded.gt.v1": "0ddfec37.grounded.gt.v1",
  "must.judge.unknown.v1": "must.judge.unknown.gt.v1",
  "must.judge.unknown.gt.v1": "must.judge.unknown.gt.v1",
  "completion.from.ledger.v1": "completion.from.ledger.gt.v1",
  "completion.from.ledger.gt.v1": "completion.from.ledger.gt.v1",
  "negctrl.single.change.v1": "negctrl.single.change.gt.v1",
  "negctrl.single.change.gt.v1": "negctrl.single.change.gt.v1",
  "four.verdict.unknown.v1": "four.verdict.unknown.gt.v1",
  "four.verdict.unknown.gt.v1": "four.verdict.unknown.gt.v1",
  "empty.result.needs.receipt.v1": "empty.result.needs.receipt.gt.v1",
  "empty.result.needs.receipt.gt.v1": "empty.result.needs.receipt.gt.v1",
}

# dual attribution per row: (Stellamem source, llm-bench counterpart)
ATTRIBUTION = {
  "neg.reader.three-arm.v1": (
    "Stellamem abstain_validation.md, negative-sample reader cases (PR #84, v1 pool, 7 cases, three arms 4/7)",
    "llm-bench S22 ROUND_CLEARED_BACKLOG_REMAINS + S21 receipt trio, same shape"),
  "neg.reader.three-arm.gt.v1": (
    "Stellamem abstain_validation.md, negative-sample reader cases (green twin: valid record)",
    "llm-bench S22, same shape"),
  "neg-expired-01": (
    "Stellamem negative_samples.md, neg-expired-01 (valid_to past); pool now v2",
    "llm-bench S22, same shape (validity metadata missing)"),
  "neg-status-01": (
    "Stellamem negative_samples.md, neg-status-01 (status not active); pool now v2",
    "llm-bench S22, same shape (validity metadata missing)"),
  "neg-valid-coupon.gt.v1": (
    "Stellamem negative_samples.md (green twin: valid record)",
    "llm-bench S22, same shape"),
  "0ddfec37_abs": (
    "Stellamem abstain_validation.md, 1b judge section + '0ddfec37 的数' (sidecar 6817efa4a19118c0.json: projected 7, candidates 18, cited 1/7)",
    "llm-bench S21 TOOL_DONE_PRE_RECEIPT, counterpart (PR #86 receipt trio)"),
  "0ddfec37.grounded.gt.v1": (
    "Stellamem abstain_validation.md, 1b judge section (green twin: grounded reasoning)",
    "llm-bench S21, counterpart"),
  "must.judge.unknown.v1": (
    "Stellamem LongMemEval round 4 (0.354 / 0.857 / 0.800); abstention controls 30/30, 29/29, 24/29",
    "llm-bench MUST_JUDGE_UNKNOWN slot (Grok Bot 2026-11-09, EigenFlux item 378154720675495936)"),
  "must.judge.unknown.gt.v1": (
    "Stellamem LongMemEval round 4 (green twin: sufficient evidence)",
    "llm-bench MUST_JUDGE_UNKNOWN slot, same item"),
  "completion.from.ledger.v1": (
    "Stellamem PR #86 receipt trio + run_id binding; S22 settlement denominator; EigenFlux heartbeat log",
    "llm-bench COMPLETION_STATUS slot, same item"),
  "completion.from.ledger.gt.v1": (
    "Stellamem PR #86 receipt trio (green twin: completed attempt in ledger)",
    "llm-bench COMPLETION_STATUS slot, same item"),
  "negctrl.single.change.v1": (
    "Stellamem negative_samples.md v2 (9 items) + PR #84 projection-layer blocking",
    "llm-bench NEGCTRL slot, same item"),
  "negctrl.single.change.gt.v1": (
    "Stellamem negative_samples.md v2 (green twin: fault at the entry)",
    "llm-bench NEGCTRL slot, same item"),
  "four.verdict.unknown.v1": (
    "Stellamem PR #98 NOT_EXECUTED third verdict; receipts PASS / FAIL / NOT_EXECUTED; refusal gate 0.800 vs 0.90",
    "llm-bench FOUR_VERDICT_STATES slot, same item"),
  "four.verdict.unknown.gt.v1": (
    "Stellamem PR #98 (green twin: clear violation)",
    "llm-bench FOUR_VERDICT_STATES slot, same item"),
  "empty.result.needs.receipt.v1": (
    "Stellamem EigenFlux heartbeat, msg-fetch read-marking case; seen.json water level + daily log",
    "llm-bench empty-result definition slot, same item"),
  "empty.result.needs.receipt.gt.v1": (
    "Stellamem EigenFlux heartbeat (green twin: receipt present)",
    "llm-bench empty-result definition slot, same item"),
}

# ---------- v0.2 metadata (Nova mapping, EigenFlux 2026-11-18) ----------
# injection_point: <domain>.<action>.<failure shape>. The six values Nova
# specified are used verbatim (reader three-arm, judge, ledger, negctrl,
# taxonomy, empty-result); the remaining reader/sufficiency points extend
# the same naming. A green twin carries its must-fail arm's point.
INJECTION_POINT = {
  "neg.reader.three-arm.v1": "reader.validity_metadata.missing",
  "neg.reader.three-arm.gt.v1": "reader.validity_metadata.missing",
  "neg-expired-01": "reader.validity_metadata.expired",
  "neg-status-01": "reader.validity_metadata.status_not_active",
  "neg-valid-coupon.gt.v1": "reader.validity_metadata.expired",
  "0ddfec37_abs": "judge.reasoning_token.fabricated",
  "0ddfec37.grounded.gt.v1": "judge.reasoning_token.fabricated",
  "must.judge.unknown.v1": "sufficiency.evidence.absent",
  "must.judge.unknown.gt.v1": "sufficiency.evidence.absent",
  "completion.from.ledger.v1": "ledger.completion_source.attempt_ledger",
  "completion.from.ledger.gt.v1": "ledger.completion_source.attempt_ledger",
  "negctrl.single.change.v1": "negctrl.chain_blocking.missed",
  "negctrl.single.change.gt.v1": "negctrl.chain_blocking.missed",
  "four.verdict.unknown.v1": "taxonomy.unknown_folded_to_violation",
  "four.verdict.unknown.gt.v1": "taxonomy.unknown_folded_to_violation",
  "empty.result.needs.receipt.v1": "empty_result.receipt.absent",
  "empty.result.needs.receipt.gt.v1": "empty_result.receipt.absent",
}

# coverage_path_ref: criteria_path:<rule_family>.<assertion_id>
COVERAGE_PATH_REF = {
  "neg.reader.three-arm.v1": "criteria_path:reader.validity_gate",
  "neg.reader.three-arm.gt.v1": "criteria_path:reader.validity_gate",
  "neg-expired-01": "criteria_path:reader.validity_gate",
  "neg-status-01": "criteria_path:reader.validity_gate",
  "neg-valid-coupon.gt.v1": "criteria_path:reader.validity_gate",
  "0ddfec37_abs": "criteria_path:judge.reasoning_grounding",
  "0ddfec37.grounded.gt.v1": "criteria_path:judge.reasoning_grounding",
  "must.judge.unknown.v1": "criteria_path:sufficiency.unknown_on_thin_evidence",
  "must.judge.unknown.gt.v1": "criteria_path:sufficiency.unknown_on_thin_evidence",
  "completion.from.ledger.v1": "criteria_path:ledger.completion_from_attempts",
  "completion.from.ledger.gt.v1": "criteria_path:ledger.completion_from_attempts",
  "negctrl.single.change.v1": "criteria_path:negctrl.full_chain_detection",
  "negctrl.single.change.gt.v1": "criteria_path:negctrl.full_chain_detection",
  "four.verdict.unknown.v1": "criteria_path:taxonomy.four_states_separate",
  "four.verdict.unknown.gt.v1": "criteria_path:taxonomy.four_states_separate",
  "empty.result.needs.receipt.v1": "criteria_path:empty_result.receipt_required",
  "empty.result.needs.receipt.gt.v1": "criteria_path:empty_result.receipt_required",
}


def _canon(value):
    """Canonical JSON: dict keys sorted recursively, arrays keep order,
    nulls preserved (Nova 2026-11-19: deep sort, top-level-only sorting
    silently breaks)."""
    if isinstance(value, dict):
        return {k: _canon(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        return [_canon(v) for v in value]
    return value


def denominator_digest(fixture_ids=None):
    """sha256 over the canonical JSON of the sorted must-fail arm ids.

    Identifies the denominator of the fixture: any change to the
    must-fail set changes the digest, so a run can prove mechanically
    which denominator it scored against.
    """
    if fixture_ids is None:
        fixture_ids = [r[0] for r in ROWS if r[7]]
    payload = json.dumps(_canon(sorted(fixture_ids)), ensure_ascii=False,
                         separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main():
    fns = {
        "READER": (reader_naive, reader_struct),
        "JUDGE": (judge_naive, judge_struct),
        "SUFFICIENCY": (sufficiency_naive, sufficiency_struct),
        "LEDGER": (ledger_naive, ledger_struct),
        "NEGCTRL": (negctrl_naive, negctrl_struct),
        "VERDICT_TAXONOMY": (taxonomy_naive, taxonomy_struct),
        "EMPTY_RESULT": (empty_naive, empty_struct),
    }
    ok = 0; out = []; naive_wrong = 0; mf_rows = 0
    for fid, criteria, inp, shape, args, exp, naive_exp, mf, reason, receipt in ROWS:
        # literal-value discipline: both verdict cells must be literal tokens
        assert exp in VERDICTS, f"{fid}: expected verdict not a literal token"
        assert naive_exp in VERDICTS, f"{fid}: naive verdict not a literal token"
        # settled format, two layers: header keeps fixture_id + criteria_version;
        # criteria_version and the expected verdict must coexist in the row
        assert criteria, f"{fid}: criteria_version missing"
        assert exp, f"{fid}: expected verdict missing"
        naive_fn, struct_fn = fns[shape]
        g, n = struct_fn(*args), naive_fn(*args)
        # must-fail arm passes only when struct is right AND naive is provably wrong
        passed = (g == exp) and ((n != exp) if mf else (n == exp))
        # the row's stated naive verdict must match what the naive rule gives
        passed = passed and (n == naive_exp)
        naive_wrong += (n != exp)
        mf_rows += 1 if mf else 0
        ok += passed
        stellamem_src, llmbench_src = ATTRIBUTION[fid]
        out.append({
            "header": {"fixture_id": fid, "criteria_version": criteria},
            "body": {
                "输入": inp,
                "期望判词": exp,
                # 必败形态 = naive_wrong (literal); null on a green twin,
                # which IS the positive control, so it has no must-fail form
                "必败形态": naive_exp if mf else None,
                "正控对照": POSITIVE_CONTROL[fid],
            },
            "reason_code": reason, "receipt_present": receipt,
            "struct": g, "naive": n, "must_fail_arm": mf,
            "pass": passed,
            "source_stellamem": stellamem_src,
            "source_llm_bench": llmbench_src,
            "injection_point": INJECTION_POINT[fid],
            "coverage_path_ref": COVERAGE_PATH_REF[fid]})
    # aggregate control: every must-fail arm has expected != naive (literal)
    agg_ok = all(r["body"]["期望判词"] != r["body"]["必败形态"]
                 for r in out if r["must_fail_arm"])
    ok += agg_ok
    total = len(ROWS) + 1
    res = {"fixture": "STELLAMEM_ABSTENTION", "version": "0.2",
           "denominator_digest": denominator_digest(),
           "format": {
               "layers": "header (fixture_id, criteria_version) + "
                         "body (输入 / 期望判词 / 必败形态 / 正控对照)",
               "mapping": "expected_verdict -> 期望判词, "
                          "naive_wrong -> 必败形态; fixture_id and "
                          "criteria_version are header metadata, not body "
                          "columns (Grok Bot 2026-11-10 10:01, "
                          "msg 378363590492553216)",
           },
           "selftest": f"{ok}/{total}", "rows": len(ROWS),
           "must_fail_arms": mf_rows, "naive_wrong_rows": naive_wrong,
           "aggregate_must_fail_expected_ne_naive": agg_ok,
           "observation_rows": 8, "rows_out": out}
    # output: stdout by default; --out PATH to write a file instead.
    # Never write back into the fixture directory: a read-only checkout
    # would crash at json.dump (Nova 2026-11-18, via 灵光 review of PR #7).
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None,
                    help="write result JSON to PATH instead of stdout")
    args = ap.parse_args()
    payload = json.dumps(res, ensure_ascii=False, indent=1)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(payload)
    else:
        print(payload)
    # summary goes to stderr so stdout stays pure JSON when no --out given
    print(f"selftest {ok}/{total}; naive rules wrong on {naive_wrong} rows",
          file=sys.stderr)
    return 0 if ok == total else 1

if __name__ == "__main__":
    sys.exit(main())
