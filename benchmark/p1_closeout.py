"""P1 收尾验收：把"档案能不能长期用"变成可复现的检查，而不是口头保证。

六项检查
--------
1. 幂等：对真实档案做 dry-run 重导入，必须 0 新增、0 改动。
2. 崩溃/损坏恢复：截断一条流 -> verify 失败 -> ingest --force 修复 -> verify 通过。
3. 指针稳定：--force 重建前后 record_id 集合完全一致。
4. 增量稳定：源文件追加一行，旧记录 id 不变，只多出新记录。
5. 导出可回放：按天导出，记录数与文本哈希与清单一致。
6. 派生记忆孤儿：扫描记忆库中引用 raw 的 claim，指向缺失记录的必须为 0。

用法：python benchmark/p1_closeout.py [--out docs/.../P1-CLOSEOUT-METRICS.json]
"""

import argparse
import hashlib
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

MEMORIES = Path(r"<USER_HOME>\.dsh\memories")
SESSIONS = Path(r"<USER_HOME>\.openclaw\agents\main\sessions")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_idempotent(archive):
    report = raw_archive.ingest(archive, SESSIONS, ("user", "assistant"), 8, dry_run=True)
    return {
        "ok": report["added"] == 0 and report["parsed_files"] == 0,
        "added": report["added"],
        "skipped_unchanged": report["skipped_unchanged"],
        "parsed_files": report["parsed_files"],
    }


def check_corruption(workdir):
    sessions = workdir / "sessions"
    sessions.mkdir(parents=True)
    picks = sorted(
        [
            f
            for f in SESSIONS.glob("*.jsonl")
            if ".trajectory" not in f.name and ".deleted" not in f.name
        ],
        key=lambda f: f.stat().st_size,
    )[:3]
    for f in picks:
        shutil.copy2(f, sessions / f.name)
    archive = workdir / "raw"
    raw_archive.ingest(archive, sessions, ("user", "assistant"), 8, False)
    before = {e["record_id"] for e in raw_archive._iter_manifest(archive)}
    ok_before = raw_archive.verify(archive)["ok"]

    stream = sorted((archive / "sessions").glob("*.jsonl.gz"))[0]
    data = stream.read_bytes()
    with open(stream, "wb") as fh:
        fh.write(data[: max(1, len(data) // 2)])
    broken = raw_archive.verify(archive)

    report = raw_archive.ingest(archive, sessions, ("user", "assistant"), 8, False, force=True)
    after = {e["record_id"] for e in raw_archive._iter_manifest(archive)}
    fixed = raw_archive.verify(archive)
    return {
        "ok": ok_before and not broken["ok"] and fixed["ok"] and before == after,
        "verify_before": ok_before,
        "verify_after_corruption": broken["ok"],
        "corruption_signals": {
            "corrupt_streams": broken.get("corrupt_streams", 0),
            "text_hash_mismatch": broken["text_hash_mismatch"],
            "count_mismatch": broken["count_mismatch"],
            "missing_streams": broken["missing_streams"],
        },
        "repaired_records": report["added"],
        "verify_after_repair": fixed["ok"],
        "pointer_set_identical": before == after,
        "records": len(after),
    }


def check_append(workdir):
    sessions = workdir / "sessions"
    stream_dir = workdir / "raw" / "sessions"
    target = sorted(sessions.glob("*.jsonl"))[0]
    archive = workdir / "raw"
    before = {
        e["record_id"]: e["text_sha256"]
        for e in raw_archive._iter_manifest(archive)
        if e["session_file"] == target.name
    }
    line = json.dumps(
        {
            "timestamp": "2026-11-01T12:00:00Z",
            "message": {
                "role": "user",
                "id": "append-probe",
                "content": "追加一行用于验证指针稳定。",
            },
        },
        ensure_ascii=False,
    )
    with target.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    report = raw_archive.ingest(archive, sessions, ("user", "assistant"), 8, False)
    after = {
        e["record_id"]: e["text_sha256"]
        for e in raw_archive._iter_manifest(archive)
        if e["session_file"] == target.name
    }
    stable = all(after.get(k) == v for k, v in before.items())
    return {
        "ok": stable and len(after) == len(before) + 1 and report["added"] == 1,
        "old_records": len(before),
        "new_records": len(after),
        "added": report["added"],
        "old_ids_stable": stable,
        "stream_exists": (stream_dir / (target.name.replace(".jsonl", "") + ".jsonl.gz")).exists(),
    }


def check_export(workdir):
    archive = workdir / "raw"
    days = sorted(raw_source.days(archive).items(), key=lambda kv: -kv[1])
    day = days[0][0]
    out = workdir / "export.jsonl"
    count = raw_archive.export(archive, day=day, out=str(out))
    rows = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines() if x.strip()]
    hashes_ok = all(
        hashlib.sha256(r["text"].encode("utf-8")).hexdigest() == r["text_sha256"] for r in rows
    )
    ids = {r["record_id"] for r in rows}
    archived = {
        e["record_id"] for e in raw_archive._iter_manifest(archive) if e.get("date_local") == day
    }
    return {
        "ok": count == len(rows) and hashes_ok and ids == archived and count > 0,
        "day": day,
        "exported": count,
        "text_hashes_verified": hashes_ok,
        "ids_match_manifest": ids == archived,
        "export_sha256": sha(out),
    }


def check_orphans():
    result = raw_archive.orphans(MEMORIES / "_raw", MEMORIES)
    return {
        "ok": result["orphaned_claims"] == 0,
        "claims_scanned": result["claims_scanned"],
        "claims_referencing_raw": result["claims_referencing_raw"],
        "orphaned_claims": result["orphaned_claims"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="P1 收尾验收")
    parser.add_argument(
        "--out", default=str(ROOT / "docs" / "personal-edition" / "P1-CLOSEOUT-METRICS.json")
    )
    args = parser.parse_args(argv)

    workdir = Path(tempfile.mkdtemp(prefix="p1close-"))
    results = {}
    try:
        results["idempotent"] = check_idempotent(MEMORIES / "_raw")
        results["corruption_recovery"] = check_corruption(workdir)
        results["append_stability"] = check_append(workdir)
        results["export_roundtrip"] = check_export(workdir)
        results["orphans"] = check_orphans()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    archive_stats = raw_archive.stats(MEMORIES / "_raw")
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "archive": {
            "manifest_sha256": sha(MEMORIES / "_raw" / "MANIFEST.jsonl"),
            "records": archive_stats["records"],
            "unique_texts": archive_stats["unique_texts"],
            "streams": archive_stats["streams"],
            "compressed_bytes": archive_stats["compressed_bytes"],
        },
        "checks": results,
        "passed": sum(1 for r in results.values() if r["ok"]),
        "total": len(results),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    for name, result in results.items():
        print(
            f"{'PASS' if result['ok'] else 'FAIL'}  {name}: "
            + json.dumps({k: v for k, v in result.items() if k != "ok"}, ensure_ascii=False)
        )
    print(f"\n{payload['passed']}/{payload['total']} 通过 -> {out}")
    return 0 if payload["passed"] == payload["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
