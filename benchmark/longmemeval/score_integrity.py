"""Offline, versioned audit of tracking CSVs; never rewrites historical scores.

Citation validity is only an eligibility check, NOT semantic entailment.
The CSV contains the judge hypothesis, not necessarily the raw model response;
raw response schema and semantic support remain unverified by this audit.
"""

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

VERSION = "score-integrity-v1"
ABSTENTION_MARKER = "[The model abstained: it returned an empty answer and marked abstain=true.]"
REQUIRED = {
    "question_id",
    "category",
    "judge_main",
    "judge_hypothesis",
    "error",
    "abstain",
    "correct_abstention",
    "citation_valid",
    "cited_n",
}


def boolean(value):
    if type(value) is bool:
        return value
    if value == "True":
        return True
    if value == "False":
        return False
    if value is None or value == "":
        return None
    raise ValueError("invalid boolean field")


def audit_row(row):
    """Keep correctness, refusal and citation eligibility on separate axes."""
    issues = []
    legacy = None
    absent = row.get("category") == "abstention"
    try:
        if not REQUIRED <= row.keys():
            raise ValueError("missing columns")
        legacy = boolean(row["judge_main"])
        abstain = boolean(row["abstain"])
        refusal = boolean(row["correct_abstention"])
        citation_valid = boolean(row["citation_valid"])
        cited = row["cited_n"]
        if type(cited) is bool or str(cited) != str(int(cited)) or int(cited) < 0:
            raise ValueError("invalid citation count")
        cited = int(cited)
        answer = row["judge_hypothesis"]
        if not isinstance(answer, str) or not row["question_id"] or not row["category"]:
            raise ValueError("invalid row schema")
        if not isinstance(row["error"], (str, type(None))):
            raise ValueError("invalid error")
        if row["error"]:
            issues.append("execution_error")
        if abstain is None:
            issues.append("missing_response_state")
        if absent and refusal is None:
            issues.append("missing_abstention_verdict")
        if absent and refusal is not None and abstain is not None and refusal != abstain:
            issues.append("inconsistent_abstention_verdict")
        if not absent and refusal is not None:
            issues.append("unexpected_abstention_verdict")
        if answer.strip() == ABSTENTION_MARKER and abstain is not True:
            issues.append("inconsistent_abstention_marker")
        if citation_valid is True and cited == 0:
            issues.append("inconsistent_citation_fields")
        valid = not issues
        if absent:
            correct = valid and abstain is True and refusal is True
            if abstain is False:
                issues.append("answered_unanswerable")
        else:
            if not answer.strip() or answer.strip() == ABSTENTION_MARKER:
                issues.append("empty_answer")
            if abstain is True:
                issues.append("abstained_answerable")
            if legacy is None:
                issues.append("missing_judge_verdict")
            correct = not issues and legacy is True
        evidence = citation_valid is True and cited > 0
        if not absent and abstain is False and not evidence:
            issues.append("no_valid_cited_evidence")
        qualified = correct if absent else correct and evidence
    except (ValueError, TypeError, KeyError):
        issues.append("malformed_tracking_row")
        correct = qualified = False
    return {
        "question_id": row.get("question_id"),
        "category": row.get("category"),
        "legacy_judge_correct": legacy,
        "corrected_correct": correct,
        "correct_abstention": correct if absent else None,
        "citation_qualified_correct": qualified,
        "semantic_support": None,
        "issues": issues,
        "false_positive_detected": legacy is True and not qualified,
    }


def summarize(rows):
    def counts(group):
        n = len(group)
        corrected = sum(r["corrected_correct"] for r in group)
        return {
            "n": n,
            "legacy_judge_correct": sum(r["legacy_judge_correct"] is True for r in group),
            "corrected_correct": corrected,
            "corrected_rate": corrected / n if n else None,
            "citation_qualified_correct": sum(r["citation_qualified_correct"] for r in group),
            "false_positives_detected": sum(r["false_positive_detected"] for r in group),
            "issues": dict(sorted(Counter(i for r in group for i in r["issues"]).items())),
        }

    return {
        "all": counts(rows),
        "answerable": counts([r for r in rows if r["category"] != "abstention"]),
        "abstention": counts([r for r in rows if r["category"] == "abstention"]),
        "by_category": {
            category: counts([r for r in rows if r["category"] == category])
            for category in sorted({r["category"] for r in rows})
        },
        "semantic_support_rate": None,
        "new_model_calls": 0,
    }


def audit_file(path):
    raw = Path(path).read_bytes()
    # StringIO preserves quoted newlines in answer fields.
    import io

    reader = csv.DictReader(io.StringIO(raw.decode("utf-8"), newline=""))
    if not REQUIRED <= set(reader.fieldnames or []):
        raise ValueError("missing required tracking columns")
    records = list(reader)
    ids = [r["question_id"] for r in records]
    if not ids or any(not qid for qid in ids) or len(ids) != len(set(ids)):
        raise ValueError("empty dataset or missing/duplicate question IDs")
    rows = [audit_row(r) for r in records]
    return {
        "schema_version": VERSION,
        "input_sha256": hashlib.sha256(raw).hexdigest(),
        "auditor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "limitations": ["raw_response_schema_not_available", "semantic_entailment_not_measured"],
        "summary": summarize(rows),
        "rows": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument(
        "--out", type=Path, help="new JSON report; existing files are never replaced"
    )
    args = parser.parse_args()
    try:
        report = audit_file(args.input)
        if args.out:
            with args.out.open("x", encoding="utf-8") as stream:
                json.dump(report, stream, ensure_ascii=True, indent=2)
                stream.write("\n")
        print(json.dumps(report["summary"], ensure_ascii=True, sort_keys=True))
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "INVALID", "error": str(exc)}, ensure_ascii=True))
        return 2
    if any("malformed_tracking_row" in r["issues"] for r in report["rows"]):
        return 2
    return 1 if report["summary"]["all"]["false_positives_detected"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
