"""Raw ↔ Claim 端到端关联验证（非空集合）。

上一轮"4,002 条记忆里 0 条引用 raw、孤儿 0"只是空集检查，证明不了关联能力。
本脚本用真实抽取结果做非空验证：每条 claim 的 record_id 必须能反查原文，
且证据、时间、说话人、会话三者一致。
"""

import argparse
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "compiler"))
import raw_archive  # noqa: E402
import raw_source  # noqa: E402

ARCHIVE = Path(r"<USER_HOME>\.dsh\memories\_raw")
MEMORIES = Path(r"<USER_HOME>\.dsh\memories")


def load_claims(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("claims") or []
    return data


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--pred", default=r"E:\mem-backup-2026-10-31\p2sandbox\pred_20261031.json")
    parser.add_argument("--day", default="2026-10-31")
    parser.add_argument("--out", default=str(ROOT / "docs" / "personal-edition"
                                             / "EVIDENCE-POINTER-MIGRATION.json"))
    args = parser.parse_args(argv)

    claims = load_claims(args.pred)
    checked = {"claims": len(claims), "with_record_id": 0, "located": 0, "evidence_in_text": 0,
               "speaker_match": 0, "time_match": 0, "session_match": 0, "failures": []}
    by_record = {}
    by_content = {}

    for claim in claims:
        prov = claim.get("provenance") or {}
        rid = prov.get("record_id")
        if not rid:
            checked["failures"].append({"claim": claim.get("claim_id"), "reason": "no record_id"})
            continue
        checked["with_record_id"] += 1
        by_record.setdefault(rid, 0)
        by_record[rid] += 1
        key = "|".join(str(claim.get(k) or "").strip() for k in ("kind", "predicate", "value"))
        by_content[key] = by_content.get(key, 0) + 1

        record = raw_source.get_record(ARCHIVE, rid)
        if record is None:
            checked["failures"].append({"claim": claim.get("claim_id"), "reason": "record_id not found",
                                        "record_id": rid})
            continue
        checked["located"] += 1
        evidence = (claim.get("evidence") or "").strip()
        if evidence and evidence[:60] in record["text"]:
            checked["evidence_in_text"] += 1
        else:
            checked["failures"].append({"claim": claim.get("claim_id"), "reason": "evidence not in text",
                                        "record_id": rid})
        if prov.get("speaker") == record["role"] == claim.get("source"):
            checked["speaker_match"] += 1
        else:
            checked["failures"].append({"claim": claim.get("claim_id"), "reason": "speaker mismatch",
                                        "claim_source": claim.get("source"),
                                        "prov_speaker": prov.get("speaker"),
                                        "record_role": record["role"]})
        claim_ts = str(prov.get("timestamp") or "")[:19]
        record_ts = str(record.get("timestamp") or "")[:19]
        if claim_ts == record_ts:
            checked["time_match"] += 1
        else:
            checked["failures"].append({"claim": claim.get("claim_id"), "reason": "timestamp mismatch",
                                        "claim": claim_ts, "record": record_ts})
        if prov.get("source_ref") == record["session_file"]:
            checked["session_match"] += 1
        else:
            checked["failures"].append({"claim": claim.get("claim_id"), "reason": "session mismatch"})

    total = max(1, checked["claims"])
    multi_claim_msg = [rid for rid, n in by_record.items() if n > 1]
    multi_msg_claim = [k for k, n in by_content.items() if n > 1 and k.strip("|")]

    # 删除传播：在副本里删掉一个会话，看这些 claim 是否变成孤儿
    workdir = Path(tempfile.mkdtemp(prefix="orphan-"))
    deletion = {}
    try:
        sandbox_archive = workdir / "_raw"
        shutil.copytree(ARCHIVE / "sessions", sandbox_archive / "sessions")
        shutil.copy2(ARCHIVE / "MANIFEST.jsonl", sandbox_archive / "MANIFEST.jsonl")
        shutil.copy2(ARCHIVE / "ledger.json", sandbox_archive / "ledger.json")
        # 用一份只含这些 claim 的最小记忆库来测孤儿检测
        fake_memories = workdir / "memories"
        (fake_memories / "system").mkdir(parents=True)
        blocks = "\n".join("```stellamem\n" + json.dumps(c, ensure_ascii=False) + "\n```"
                           for c in claims)
        (fake_memories / "system" / "memory-inbox.md").write_text(
            "# inbox\n\n<!-- stellamem:v2 -->\n\n" + blocks + "\n", encoding="utf-8")
        before = raw_archive.orphans(sandbox_archive, fake_memories)
        target_session = None
        for claim in claims:
            ref = (claim.get("provenance") or {}).get("source_ref")
            if ref:
                target_session = ref
                break
        raw_archive.forget(sandbox_archive, target_session)
        after = raw_archive.orphans(sandbox_archive, fake_memories)
        deletion = {
            "deleted_session": target_session,
            "orphans_before": before["orphaned_claims"],
            "orphans_after": after["orphaned_claims"],
            "detected": after["orphaned_claims"] > before["orphaned_claims"],
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    # 生产记忆的覆盖率（这是上轮 0/4002 的真实口径）
    prod = raw_archive.orphans(ARCHIVE, MEMORIES)
    total_formal = 0
    block_files = ["core/preferences.md", "core/identity.md", "core/user-core.md",
                   "long-term/projects.md", "long-term/decisions.md", "long-term/lessons.md",
                   "semantic/facts.md", "semantic/profile.md", "system/memory-inbox.md"]
    import re
    pattern = re.compile(r"```stellamem\s*\n(.*?)\n```", re.S)
    for rel in block_files:
        path = MEMORIES / rel
        if path.exists():
            total_formal += len(pattern.findall(path.read_text(encoding="utf-8", errors="replace")))

    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "inputs": {"predictions": args.pred,
                   "predictions_sha256": __import__("hashlib").sha256(
                       Path(args.pred).read_bytes()).hexdigest(),
                   "day": args.day,
                   "manifest_sha256": __import__("hashlib").sha256(
                       (ARCHIVE / "MANIFEST.jsonl").read_bytes()).hexdigest()},
        "checks": checked,
        "rates": {
            "record_id_coverage_pct": round(checked["with_record_id"] / total * 100, 1),
            "locate_success_pct": round(checked["located"] / max(1, checked["with_record_id"]) * 100, 1),
            "evidence_hit_pct": round(checked["evidence_in_text"] / max(1, checked["located"]) * 100, 1),
            "speaker_match_pct": round(checked["speaker_match"] / max(1, checked["located"]) * 100, 1),
            "time_match_pct": round(checked["time_match"] / max(1, checked["located"]) * 100, 1),
        },
        "one_message_multi_claims": {"record_ids": len(multi_claim_msg),
                                     "max_claims_per_message": max(by_record.values(), default=0)},
        "multi_messages_same_claim": {"groups": len(multi_msg_claim)},
        "deletion_propagation": deletion,
        "production_coverage": {
            "formal_memories": total_formal,
            "referencing_raw": prod["claims_referencing_raw"],
            "orphaned": prod["orphaned_claims"],
            "coverage_pct": round(prod["claims_referencing_raw"] / max(1, total_formal) * 100, 1),
        },
    }
    out = Path(args.out)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"checks": {k: v for k, v in checked.items() if k != "failures"},
                      "failures": len(checked["failures"]),
                      "rates": payload["rates"],
                      "multi": payload["one_message_multi_claims"],
                      "deletion": deletion,
                      "production": payload["production_coverage"]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
