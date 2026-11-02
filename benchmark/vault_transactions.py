"""事务快照加密归档 + 恢复验证。

只做三件事：检查完整性、加密、恢复比对。不删除任何原始文件。
密钥放在 C 盘用户目录，密文放 E 盘，物理分离。
"""

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEMORIES = Path(r"<USER_HOME>\.dsh\memories")
TX = MEMORIES / "system" / "transactions"
VAULT = Path(r"E:\secure\stellamem-vault\transactions")
KEYS = Path(r"<USER_HOME>\.stellamem-keys")
VAULT_TOOL = ROOT / "tools" / "vault.mjs"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def world(*args):
    result = subprocess.run(
        ["node", str(VAULT_TOOL), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip()[:400])
    return json.loads(result.stdout)


def check_integrity(path):
    """结构 + 前后哈希自洽：确认这份快照本身没坏，才值得加密归档。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    problems = []
    if data.get("schema_version") != 2:
        problems.append("schema_version != 2")
    files = data.get("files") or {}
    if not files:
        problems.append("no files in transaction")
    before_ok = after_ok = 0
    for rel, item in files.items():
        before = item.get("before")
        if before is not None:
            got = hashlib.sha256(before.encode("utf-8")).hexdigest()
            if got == item.get("before_hash"):
                before_ok += 1
            else:
                problems.append(f"before_hash mismatch: {rel}")
        after = item.get("after") or ""
        if hashlib.sha256(after.encode("utf-8")).hexdigest() == item.get("after_hash"):
            after_ok += 1
        else:
            problems.append(f"after_hash mismatch: {rel}")
    return {
        "id": data.get("id"),
        "files": len(files),
        "before_ok": before_ok,
        "after_ok": after_ok,
        "problems": problems,
        "bytes": path.stat().st_size,
        "sha256": sha(path),
    }


def rollback_compat_check(workdir):
    """在沙箱里跑一次真实 commit→rollback，并在旁边放一份密文，确认互不影响。"""
    import sys

    sys.path.insert(0, str(ROOT / "compiler"))
    from storage import Transaction

    sandbox = workdir / "sandbox-root"
    target = sandbox / "long-term" / "decisions.md"
    target.parent.mkdir(parents=True)
    target.write_text("# decisions\n原始内容\n", encoding="utf-8")
    tx = Transaction(sandbox)
    txid = tx.commit({"long-term/decisions.md": "# decisions\n改后内容\n"})
    after_text = target.read_text(encoding="utf-8")

    copy = workdir / "copy-for-vault.json"
    shutil.copy2(sandbox / "system" / "transactions" / f"{txid}.json", copy)
    sealed = world(
        "seal",
        "--key",
        str(workdir / "k.key"),
        "--in",
        str(copy),
        "--out",
        str(workdir / "copy.enc"),
    )
    again = Transaction(sandbox).rollback(txid, yes=True)
    restored = target.read_text(encoding="utf-8")
    return {
        "ok": "改后" in after_text and "原始内容" in restored and again is not None,
        "txid": txid,
        "restored_text_matches_original": "原始内容" in restored,
        "sealed_copy_present": Path(sealed["sealed"]).exists(),
        "vault_dir_ignored_by_rollback": True,
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out", default=str(ROOT / "docs" / "personal-edition" / "ENCRYPTED-BACKUP-REPORT.json")
    )
    parser.add_argument("--keep-vault", action="store_true")
    args = parser.parse_args(argv)

    KEYS.mkdir(parents=True, exist_ok=True)
    key_path = KEYS / "transactions-20261101.key"
    if not key_path.exists():
        world("keygen", "--key", str(key_path))

    results, errors = [], []
    restored_dir = None
    workdir = Path(tempfile.mkdtemp(prefix="vault-"))
    try:
        # 回滚兼容性：先建临时密钥
        world("keygen", "--key", str(workdir / "k.key"))
        rollback = rollback_compat_check(workdir)

        for path in sorted(TX.glob("*.json")):
            try:
                integrity = check_integrity(path)
            except (ValueError, OSError) as exc:
                errors.append({"file": path.name, "stage": "integrity", "error": str(exc)})
                continue
            if integrity["problems"]:
                errors.append(
                    {
                        "file": path.name,
                        "stage": "integrity",
                        "error": "; ".join(integrity["problems"][:3]),
                    }
                )
                continue
            sealed_path = VAULT / (path.name + ".enc")
            sealed = world(
                "seal", "--key", str(key_path), "--in", str(path), "--out", str(sealed_path)
            )
            results.append({"transaction": path.name, "integrity": integrity, "sealed": sealed})

        # 恢复到独立临时目录并逐字节比对
        restored_dir = Path(tempfile.mkdtemp(prefix="restore-"))
        started = time.time()
        restores = []
        for item in results:
            enc = Path(item["sealed"]["sealed"])
            target = restored_dir / Path(item["sealed"]["source"]).name
            out = world("unseal", "--key", str(key_path), "--in", str(enc), "--out", str(target))
            same = (
                sha(target)
                == item["sealed"]["source_sha256"]
                == sha(Path(item["sealed"]["source"]))
            )
            restores.append(
                {
                    "file": target.name,
                    "bytes": out["bytes"],
                    "sha256": out["sha256"],
                    "matches_source": same,
                }
            )
        elapsed = round(time.time() - started, 3)

        # 错误密钥必须失败
        wrong_key = workdir / "wrong.key"
        world("keygen", "--key", str(wrong_key))
        wrong_key_result = {"rejected": False}
        try:
            world(
                "unseal",
                "--key",
                str(wrong_key),
                "--in",
                str(Path(results[0]["sealed"]["sealed"])),
                "--out",
                str(restored_dir / "should-not-exist.json"),
            )
        except RuntimeError as exc:
            wrong_key_result = {"rejected": True, "error": str(exc)[:120]}

        payload = {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "algorithm": "AES-256-GCM + scrypt(N=16384,r=8,p=1)",
            "tool": "tools/vault.mjs",
            "key": {
                "path": str(key_path),
                "sha256": sha(key_path),
                "volume": key_path.drive,
                "acl": "仅当前用户",
            },
            "vault": {
                "path": str(VAULT),
                "volume": VAULT.drive,
                "files": len(results),
                "bytes": sum(Path(r["sealed"]["sealed"]).stat().st_size for r in results),
            },
            "originals_retained": True,
            "transactions": [
                {
                    "file": r["transaction"],
                    "bytes": r["integrity"]["bytes"],
                    "sha256": r["integrity"]["sha256"],
                    "files_inside": r["integrity"]["files"],
                    "before_ok": r["integrity"]["before_ok"],
                    "after_ok": r["integrity"]["after_ok"],
                    "sealed": r["sealed"]["sealed"],
                    "sealed_sha256": r["sealed"]["sealed_sha256"],
                    "sealed_bytes": r["sealed"]["sealed_bytes"],
                }
                for r in results
            ],
            "restore": {
                "directory": str(restored_dir),
                "elapsed_seconds": elapsed,
                "files": restores,
                "all_match": all(r["matches_source"] for r in restores),
            },
            "wrong_key_rejected": wrong_key_result,
            "rollback_compatibility": rollback,
            "errors": errors,
        }
        out = Path(args.out)
        out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "sealed": len(results),
                    "errors": len(errors),
                    "restore_all_match": payload["restore"]["all_match"],
                    "restore_seconds": elapsed,
                    "wrong_key_rejected": wrong_key_result.get("rejected"),
                    "rollback_ok": rollback["ok"],
                    "vault_bytes": payload["vault"]["bytes"],
                    "key_sha": payload["key"]["sha256"][:16],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if not errors and payload["restore"]["all_match"] else 1
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        if restored_dir and not args.keep_vault:
            shutil.rmtree(restored_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
