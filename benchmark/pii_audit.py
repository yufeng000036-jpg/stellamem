"""PII 全量审计：只统计、不输出任何真实号码。

覆盖区域
--------
daily / system(inbox, review-log, version-chain, transactions) / core / long-term / semantic /
raw 档案（含 gz 流）/ 原始 sessions / E:\\private / 历史备份

识别
----
手机号（连续与分隔两种写法）、身份证号（GB 11643 校验位验证，压掉误报）、
残疾人证号（身份证号 + 2 位）、密钥特征（私钥块 / sk- / ghp_ / AKIA / JWT）、私密档案文件。

统计口径
--------
- 先剔除 16 位以上连续十六进制串（哈希碎片），避免把 sha256 里的数字当手机号。
- 身份证号必须通过校验位，否则计入 `idcard_rejected`。
- 同一文件内容在多处出现时记 `duplicate_copies`，用于识别备份/副本扩散。

用法
----
    python benchmark/pii_audit.py [--out docs/personal-edition/PII-INVENTORY.csv]
                                  [--json docs/personal-edition/PII-INVENTORY.json]
                                  [--skip-backups]
"""

import argparse
import csv
import gzip
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

VERSION = "1.0.0"
MEMORIES = Path(r"<USER_HOME>\.dsh\memories")
SESSIONS = Path(r"<USER_HOME>\.openclaw\agents\main\sessions")
PRIVATE = Path(r"E:\private")
BACKUPS = [Path(r"E:\mem-backup-2026-10-31")]

# 哈希碎片形如 3f9a...，必然含字母；纯数字长串（身份证/残疾证号）不能当碎片剔除。
HEX_RUN = re.compile(r"(?=[0-9a-fA-F]*[a-fA-F])[0-9a-fA-F]{16,}")
PHONE = re.compile(r"(?<![0-9])1[3-9][0-9](?:[0-9]{8}|[ -][0-9]{4}[ -][0-9]{4})(?![0-9])")
ID18 = re.compile(r"(?<![0-9])([0-9]{17}[0-9Xx])(?![0-9])")
DISABILITY = re.compile(r"(?<![0-9])([0-9]{17}[0-9Xx][0-9]{2})(?![0-9])")
SECRET = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|sk-[A-Za-z0-9]{20,}"
    r"|ghp_[A-Za-z0-9]{30,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
)
WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
CHECK = "10X98765432"
PROVINCES = {
    11,
    12,
    13,
    14,
    15,
    21,
    22,
    23,
    31,
    32,
    33,
    34,
    35,
    36,
    37,
    41,
    42,
    43,
    44,
    45,
    46,
    50,
    51,
    52,
    53,
    54,
    61,
    62,
    63,
    64,
    65,
}


def _birth_ok(value):
    try:
        year, month, day = int(value[6:10]), int(value[10:12]), int(value[12:14])
    except ValueError:
        return False
    if not 1900 <= year <= datetime.now().year:
        return False
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return False
    try:
        datetime(year, month, day)
    except ValueError:
        return False
    return True


def id_checksum_ok(value):
    """身份证必须同时满足：行政区划号 + 生日合法 + 校验位。只靠校验位会放过随机数字。"""
    if len(value) != 18 or not value[:17].isdigit():
        return False
    if int(value[:2]) not in PROVINCES:
        return False
    if not _birth_ok(value):
        return False
    total = sum(int(value[i]) * WEIGHTS[i] for i in range(17))
    return CHECK[total % 11].upper() == value[17].upper()


def disability_ok(value):
    """残疾人证号 = 身份证号 + 2 位（类别 1-7 + 等级 1-4）。"""
    if len(value) != 20:
        return False
    if not id_checksum_ok(value[:18]):
        return False
    return value[18] in "1234567" and value[19] in "1234"


def _tag(value):
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]


def scan_text(text):
    stripped = HEX_RUN.sub(" ", text)
    phones = PHONE.findall(stripped)
    raw_ids = ID18.findall(stripped)
    valid_ids = [v for v in raw_ids if id_checksum_ok(v)]
    dis = [v for v in DISABILITY.findall(stripped) if disability_ok(v)]
    secrets = SECRET.findall(text)
    return {
        "phone": len(phones),
        "idcard": len(valid_ids),
        "idcard_rejected": len(raw_ids) - len(valid_ids),
        "disability": len(dis),
        "secret": len(secrets),
        # 只保留不可逆短指纹，用于区分"一个号码被抄了很多份"与"真的有很多号码"
        "u_phone": {_tag(v) for v in phones},
        "u_idcard": {_tag(v) for v in valid_ids},
        "u_disability": {_tag(v) for v in dis},
        "u_secret": {_tag(v) for v in secrets},
    }


def scan_file(path):
    try:
        if path.suffix == ".gz":
            with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        else:
            text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, EOFError, UnicodeError):
        return None
    hits = scan_text(text)
    data = path.read_bytes() if path.suffix != ".gz" else None
    return {
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(data).hexdigest() if data is not None else "",
        **hits,
    }


def _work(item):
    area, path = item
    return area, str(path), scan_file(path)


def iter_targets(skip_backups):
    root = MEMORIES
    for area in ["daily", "system", "core", "long-term", "semantic", "notes", "archive"]:
        base = root / area
        if base.exists():
            for f in base.rglob("*"):
                if f.is_file() and (
                    f.suffix.lower() in (".md", ".json", ".jsonl")
                    or f.name.startswith(".private-profile")
                ):
                    yield ("memories/" + area, f)
    raw = root / "_raw"
    if raw.exists():
        for f in raw.rglob("*"):
            if f.is_file() and f.suffix.lower() in (".gz", ".jsonl", ".json"):
                yield ("raw", f)
    if SESSIONS.exists():
        for f in SESSIONS.glob("*.jsonl"):
            if ".trajectory" not in f.name:
                yield ("sessions", f)
    if PRIVATE.exists():
        for f in PRIVATE.rglob("*"):
            if f.is_file():
                yield ("private", f)
    if not skip_backups:
        for base in BACKUPS:
            if not base.exists():
                continue
            for f in base.rglob("*"):
                if f.is_file() and f.suffix.lower() in (".md", ".json", ".jsonl", ".gz"):
                    if "p1sandbox" in str(f) or "p2sandbox" in str(f):
                        continue
                    yield ("backup", f)


def main(argv=None):
    parser = argparse.ArgumentParser(description="PII 全量审计")
    parser.add_argument(
        "--out",
        default=str(
            Path(__file__).resolve().parents[1] / "docs" / "personal-edition" / "PII-INVENTORY.csv"
        ),
    )
    parser.add_argument(
        "--json",
        default=str(
            Path(__file__).resolve().parents[1] / "docs" / "personal-edition" / "PII-INVENTORY.json"
        ),
    )
    parser.add_argument("--skip-backups", action="store_true")
    args = parser.parse_args(argv)

    rows, totals = [], {"phone": 0, "idcard": 0, "disability": 0, "secret": 0, "idcard_rejected": 0}
    by_area = {}
    scanned = 0
    sha_index = {}
    targets = list(iter_targets(args.skip_backups))
    from concurrent.futures import ProcessPoolExecutor

    workers = min(8, (os.cpu_count() or 4))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_work, targets, chunksize=8))
    uniques = {"phone": set(), "idcard": set(), "disability": set(), "secret": set()}
    for area, path_str, result in results:
        path = Path(path_str)
        scanned += 1
        if result is None:
            continue
        for key in uniques:
            uniques[key] |= result.pop("u_" + key, set())
        hits = result["phone"] + result["idcard"] + result["disability"] + result["secret"]
        for key in totals:
            totals[key] += result[key]
        stat = by_area.setdefault(area, {"files": 0, "files_with_hits": 0, "hits": 0})
        stat["files"] += 1
        stat["hits"] += hits
        if hits:
            stat["files_with_hits"] += 1
        if result["sha256"]:
            sha_index.setdefault(result["sha256"], []).append(str(path))
        rows.append(
            {
                "area": area,
                "path": str(path),
                "bytes": result["bytes"],
                "sha256": result["sha256"],
                "phone": result["phone"],
                "idcard": result["idcard"],
                "disability": result["disability"],
                "secret": result["secret"],
                "total": hits,
                "duplicate_copies": 0,
                "action": (
                    "encrypt"
                    if area in ("memories/system", "backup", "raw")
                    and path.suffix == ".json"
                    and "transactions" in str(path)
                    else (
                        "redact"
                        if area == "memories/daily" and hits
                        else ("encrypt" if hits else "keep")
                    )
                ),
            }
        )

    for row in rows:
        if row["sha256"]:
            row["duplicate_copies"] = len(sha_index.get(row["sha256"], [])) - 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "area",
        "path",
        "bytes",
        "sha256",
        "phone",
        "idcard",
        "disability",
        "secret",
        "total",
        "duplicate_copies",
        "action",
    ]
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in sorted(rows, key=lambda r: (-r["total"], r["path"])):
            writer.writerow(row)

    hit_rows = [r for r in rows if r["total"]]
    duplicate_groups = {k: v for k, v in sha_index.items() if len(v) > 1}
    summary = {
        "schema_version": 1,
        "script": "benchmark/pii_audit.py",
        "script_version": VERSION,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "files_scanned": scanned,
        "files_with_hits": len(hit_rows),
        "hits": totals,
        "unique_values": {k: len(v) for k, v in uniques.items()},
        "by_area": by_area,
        "duplicate_content_groups": len(duplicate_groups),
        "duplicate_copies_total": sum(len(v) - 1 for v in duplicate_groups.values()),
        "output_csv": str(out),
        "output_csv_sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
        "notes": [
            "不输出任何真实号码，只输出命中数量。",
            "身份证按 GB 11643 校验位过滤，未通过的计入 idcard_rejected。",
            "16 位以上十六进制串在匹配前剔除，避免哈希碎片误报。",
        ],
    }
    json_out = Path(args.json)
    json_out.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                k: summary[k]
                for k in (
                    "files_scanned",
                    "files_with_hits",
                    "hits",
                    "by_area",
                    "duplicate_content_groups",
                )
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print("csv:", out, "json:", json_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
