"""写前自动快照 + 显式回滚（F1）。

与 reliability.snapshot() 的区别：
- reliability.snapshot() 是整树 zip，用于人工备份/迁移；
- 本模块是轻量写前快照：只对 index.db + 关键配置文件做文件级快照，
  在每次写操作（索引重建、标签写入、pending 落库、approve/reject）前自动触发，
  出错时可一键恢复。

铁律：
- 回滚是显式运维操作，必须传确认短语，否则拒绝；
- 恢复前校验 SHA256，不一致拒绝；
- .db 只用 sqlite3.Connection.backup()。在线备份失败则失败关闭：
  不写快照、不回退到 shutil.copy2（WAL 下 copy2 主文件会丢表）；
- 恢复前确认没有活动连接，再用 backup() 把快照写回 live db，
  并清掉 -wal/-shm，避免旧 WAL 被重放；
- 恢复后做 integrity_check，并核对 manifest 里的表、行数、最新记录时间；
  任一失败都保留恢复前的库；
- 快照/回滚/清理/失败全部写审计日志；
- 不绕过 append-only：记忆内容的常规修改仍走事务 + valid_to，
  快照恢复只用于运维事故，且每次调用可审计、可追溯。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# 写前快照覆盖的文件（相对 root）。注意：
# - openclaw.json 是只读红线，绝不快照也不恢复；
# - system/api-token 是密钥，不进快照。
SNAPSHOT_FILES = ("stellamem-data/index.db", "config.json")
SNAPSHOT_DIRNAME = "system/snapshots"
KEEP_DEFAULT = 10
CONFIRM_PHRASE = "RESTORE"
_SQLITE_MAGIC = b"SQLite format 3\x00"


class SnapshotError(RuntimeError):
    """Fail-closed snapshot or restore error. No partial snapshot is kept."""


def _now_ts() -> str:
    # 微秒精度：同秒内多次写前快照不会撞名
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _audit(root: Path, action: str, **fields) -> None:
    # 延迟导入，避免循环依赖
    from reliability import AuditLog

    AuditLog(root).append(action, None, **fields)


def _snap_dir(root: Path) -> Path:
    d = Path(root) / SNAPSHOT_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sidecar(path: Path, suffix: str) -> Path:
    return Path(str(path) + suffix)


def _is_sqlite_file(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return fh.read(16) == _SQLITE_MAGIC
    except OSError:
        return False


def _is_live_wal_db(path: Path) -> bool:
    """True when a file copy of this db would be unsafe."""
    if _sidecar(path, "-wal").exists() or _sidecar(path, "-shm").exists():
        return True
    if not _is_sqlite_file(path):
        return False
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            mode = con.execute("PRAGMA journal_mode").fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return True
    return bool(mode) and str(mode[0]).lower() == "wal"


def _remove_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        side = _sidecar(path, suffix)
        if side.exists():
            side.unlink()


def _backup_db(src: Path, target: Path) -> str:
    """WAL-safe 在线备份 .db 文件，返回所用方法名。

    生产 index.db 用 WAL 模式（indexer.py: PRAGMA journal_mode=WAL）。
    sqlite3 Connection.backup() 是官方在线备份 API。
    对真正的 SQLite / 活动 WAL 库，备份失败必须失败关闭，禁止 copy2。
    只有根本不是 SQLite 文件时才允许 copy2（历史测试夹具里的假 index.db）。
    """
    try:
        src_con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        try:
            if target.exists():
                target.unlink()
            _remove_sidecars(target)
            dst_con = sqlite3.connect(target)
            try:
                src_con.backup(dst_con)
            finally:
                dst_con.close()
        finally:
            src_con.close()
        return "sqlite-backup"
    except Exception as exc:
        if target.exists():
            try:
                target.unlink()
            except OSError:
                pass
        try:
            _remove_sidecars(target)
        except OSError:
            pass
        if _is_live_wal_db(src) or _is_sqlite_file(src):
            raise SnapshotError(
                "sqlite online backup failed for "
                f"{src}; refusing file-copy fallback for a live database "
                f"({type(exc).__name__}: {exc}). No snapshot was kept."
            ) from exc
        shutil.copy2(src, target)
        return "copy2-fallback"


def _db_manifest_stats(path: Path) -> dict:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise SnapshotError(f"snapshot database failed integrity_check: {integrity}")
        tables = [
            row[0]
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        row_count = None
        latest = None
        if "records" in tables:
            row_count = con.execute("SELECT COUNT(*) FROM records").fetchone()[0]
            latest = con.execute(
                "SELECT MAX(CASE "
                "WHEN COALESCE(observed_at, '') >= COALESCE(occurred_at, '') "
                "THEN NULLIF(observed_at, '') ELSE NULLIF(occurred_at, '') END) "
                "FROM records"
            ).fetchone()[0]
        return {
            "tables": tables,
            "row_count": row_count,
            "latest_record_timestamp": latest,
        }
    finally:
        con.close()


def _holders_proc(paths: list[Path]) -> list[str]:
    wanted = set()
    for path in paths:
        if path.exists():
            st = path.stat()
            wanted.add((st.st_dev, st.st_ino))
    if not wanted:
        return []
    found = []
    for pid_dir in Path("/proc").iterdir():
        if not pid_dir.name.isdigit():
            continue
        fd_dir = pid_dir / "fd"
        try:
            fds = list(fd_dir.iterdir())
        except OSError:
            continue
        for fd in fds:
            try:
                st = fd.stat()
            except OSError:
                continue
            if (st.st_dev, st.st_ino) in wanted:
                found.append(f"pid {pid_dir.name} fd {fd.name}")
    return found


def _holders_lsof(paths: list[Path]) -> list[str] | None:
    if shutil.which("lsof") is None:
        return None
    found = []
    for path in paths:
        if not path.exists():
            continue
        proc = subprocess.run(
            ["lsof", "-t", "--", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        for pid in proc.stdout.splitlines():
            pid = pid.strip()
            if pid:
                found.append(f"pid {pid} path {path}")
    return found


def _holders_windows(paths: list[Path]) -> list[str]:
    import ctypes

    generic_read = 0x80000000
    open_existing = 3
    file_attribute_normal = 0x80
    invalid = ctypes.c_void_p(-1).value
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    found = []
    for path in paths:
        if not path.exists():
            continue
        handle = kernel32.CreateFileW(
            str(path),
            generic_read,
            0,
            None,
            open_existing,
            file_attribute_normal,
            None,
        )
        if handle == invalid or handle == -1:
            err = ctypes.get_last_error()
            if err != 2:
                found.append(f"windows lock err={err} path={path}")
            continue
        kernel32.CloseHandle(handle)
    return found


def _active_connection_holders(db_path: Path) -> list[str]:
    """Return holders of db/-wal/-shm. Raise if the absence of holders cannot be verified."""
    targets = [db_path, _sidecar(db_path, "-wal"), _sidecar(db_path, "-shm")]
    if Path("/proc").is_dir():
        return _holders_proc(targets)
    if sys.platform == "win32":
        return _holders_windows(targets)
    found = _holders_lsof(targets)
    if found is None:
        raise SnapshotError(
            f"restore refused: cannot verify that {db_path.name} has no active connections"
        )
    return found


def _assert_no_active_connections(db_path: Path) -> None:
    if not db_path.exists():
        return
    holders = _active_connection_holders(db_path)
    if holders:
        raise SnapshotError(
            "restore refused: active connection(s) hold "
            f"{db_path.name}; close them before restore. " + "; ".join(holders[:8])
        )


def _uses_sqlite_restore(src: Path, entry: dict) -> bool:
    method = entry.get("method")
    if method == "sqlite-backup":
        return True
    if method == "copy2-fallback":
        return _is_sqlite_file(src)
    return src.suffix.lower() in {".db", ".sqlite", ".sqlite3"} and _is_sqlite_file(src)


def _verify_db_against_entry(path: Path, entry: dict) -> None:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise SnapshotError(f"integrity_check failed for {entry.get('path')}: {integrity}")
        present = {
            row[0]
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        expected = entry.get("tables")
        if expected is not None:
            missing = [name for name in expected if name not in present]
            if missing:
                raise SnapshotError(f"restored db missing tables {missing}")
        if entry.get("row_count") is not None:
            if "records" not in present:
                raise SnapshotError("restored db has no records table for row_count check")
            got = con.execute("SELECT COUNT(*) FROM records").fetchone()[0]
            if got != entry["row_count"]:
                raise SnapshotError(
                    "row_count mismatch for "
                    f"{entry.get('path')}: manifest {entry['row_count']} restored {got}"
                )
        if "latest_record_timestamp" in entry and "records" in present:
            got_ts = con.execute(
                "SELECT MAX(CASE "
                "WHEN COALESCE(observed_at, '') >= COALESCE(occurred_at, '') "
                "THEN NULLIF(observed_at, '') ELSE NULLIF(occurred_at, '') END) "
                "FROM records"
            ).fetchone()[0]
            if got_ts != entry["latest_record_timestamp"]:
                raise SnapshotError(
                    "latest_record_timestamp mismatch for "
                    f"{entry.get('path')}: manifest {entry['latest_record_timestamp']!r} "
                    f"restored {got_ts!r}"
                )
    finally:
        con.close()


def _restore_sqlite(src: Path, dst: Path, entry: dict) -> None:
    """把快照库 backup() 回 live 路径。调用方已确认没有活动连接。"""
    if dst.exists():
        _assert_no_active_connections(dst)
    pre_dir = Path(tempfile.mkdtemp(prefix="stellamem-pre-restore-"))
    pre_db = pre_dir / "pre.db"
    had_sqlite = dst.exists() and _is_sqlite_file(dst)
    had_other = dst.exists() and not had_sqlite
    try:
        if had_sqlite:
            _backup_db(dst, pre_db)
        elif had_other:
            shutil.copy2(dst, pre_dir / "pre.bin")
        dst.parent.mkdir(parents=True, exist_ok=True)
        _remove_sidecars(dst)
        if dst.exists():
            dst.unlink()
        _backup_db(src, dst)
        con = sqlite3.connect(dst)
        try:
            # DELETE 模式让恢复后的库不依赖 -wal/-shm，旧 WAL 无法被重放。
            con.execute("PRAGMA journal_mode=DELETE")
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            con.close()
        _remove_sidecars(dst)
        _verify_db_against_entry(dst, entry)
    except Exception:
        try:
            _remove_sidecars(dst)
            if dst.exists():
                dst.unlink()
            if had_sqlite and pre_db.exists():
                _backup_db(pre_db, dst)
            elif had_other and (pre_dir / "pre.bin").exists():
                shutil.copy2(pre_dir / "pre.bin", dst)
        finally:
            shutil.rmtree(pre_dir, ignore_errors=True)
        raise
    shutil.rmtree(pre_dir, ignore_errors=True)


def auto_snapshot(root: Path | str, label: str) -> Path:
    """写操作前调用：对 SNAPSHOT_FILES 做文件级快照，返回快照目录。

    不存在的文件跳过（不报错），以便在部分初始化的环境里也能用。
    .db 文件走 WAL-safe 的 sqlite3 在线备份，其余文件走 copy2。
    在线备份失败时删除已写的半份快照并记审计，不回退到文件拷贝。
    """
    root = Path(root)
    dest = _snap_dir(root) / f"{_now_ts()}-{label}"
    dest.mkdir(parents=True, exist_ok=False)
    try:
        manifest = {"created_at": _now_ts(), "label": label, "files": []}
        for rel in SNAPSHOT_FILES:
            src = root / rel
            if not src.is_file():
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if src.suffix.lower() in (".db", ".sqlite", ".sqlite3"):
                method = _backup_db(src, target)
            else:
                shutil.copy2(src, target)
                method = "copy2"
            file_entry = {
                "path": rel,
                "bytes": target.stat().st_size,
                "sha256": _sha256(target),
                "method": method,
            }
            if method == "sqlite-backup":
                file_entry.update(_db_manifest_stats(target))
            manifest["files"].append(file_entry)
        manifest["digest"] = hashlib.sha256(
            "".join(e["sha256"] for e in manifest["files"]).encode("utf-8")
        ).hexdigest()
        (dest / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    except Exception as exc:
        shutil.rmtree(dest, ignore_errors=True)
        try:
            _audit(root, "snapshot_auto_failed", label=label, error=str(exc))
        except Exception:
            pass
        raise
    _audit(root, "snapshot_auto", label=label, path=str(dest), files=len(manifest["files"]))
    prune_snapshots(root)
    return dest


def list_snapshots(root: Path | str) -> list[dict]:
    """列出快照（按时间升序）。"""
    root = Path(root)
    out = []
    snap_dir = Path(root) / SNAPSHOT_DIRNAME
    if not snap_dir.is_dir():
        return out
    for child in sorted(snap_dir.iterdir()):
        mf = child / "manifest.json"
        if not child.is_dir() or not mf.is_file():
            continue
        try:
            m = json.loads(mf.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        out.append(
            {
                "name": child.name,
                "label": m.get("label"),
                "created_at": m.get("created_at"),
                "files": len(m.get("files", [])),
                "path": str(child),
            }
        )
    return out


def verify_snapshot(snap_path: Path | str) -> dict:
    """校验快照内文件的 SHA256。返回 {ok, bad}。"""
    snap_path = Path(snap_path)
    manifest = json.loads((snap_path / "manifest.json").read_text(encoding="utf-8"))
    bad = []
    for entry in manifest["files"]:
        target = snap_path / entry["path"]
        if not target.is_file():
            bad.append({"path": entry["path"], "reason": "missing"})
        elif _sha256(target) != entry["sha256"]:
            bad.append({"path": entry["path"], "reason": "sha256_mismatch"})
    return {"ok": not bad, "bad": bad, "files": len(manifest["files"])}


def restore_snapshot(
    root: Path | str, name: str, confirm: str = "", prune_after: bool = True
) -> dict:
    """从快照恢复文件。必须传 confirm="RESTORE"，否则拒绝。

    SQLite 快照用 Connection.backup() 写回 live 库，不用 copy2。
    有活动连接时拒绝。校验失败时保留恢复前的库。
    """
    if confirm != CONFIRM_PHRASE:
        raise PermissionError(
            f"snapshot restore requires explicit confirmation (confirm={CONFIRM_PHRASE!r}); refused"
        )
    root = Path(root)
    snap_path = _snap_dir(root) / name
    if not snap_path.is_dir():
        raise FileNotFoundError(f"snapshot not found: {name}")
    check = verify_snapshot(snap_path)
    if not check["ok"]:
        raise ValueError(f"snapshot integrity check failed: {check['bad']}")
    manifest = json.loads((snap_path / "manifest.json").read_text(encoding="utf-8"))
    restored = []
    for entry in manifest["files"]:
        src = snap_path / entry["path"]
        dst = root / entry["path"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        if _uses_sqlite_restore(src, entry):
            _restore_sqlite(src, dst, entry)
        else:
            shutil.copy2(src, dst)
        restored.append(entry["path"])
    _audit(root, "snapshot_restore", name=name, files=restored)
    if prune_after:
        prune_snapshots(root)
    return {"ok": True, "name": name, "restored": restored}


def prune_snapshots(root: Path | str, keep: int = KEEP_DEFAULT) -> dict:
    """只保留最近 keep 份快照。删除动作记审计。"""
    snaps = list_snapshots(root)
    doomed = snaps[: max(0, len(snaps) - keep)]
    for s in doomed:
        shutil.rmtree(s["path"], ignore_errors=True)
    if doomed:
        _audit(root, "snapshot_prune", removed=[s["name"] for s in doomed], kept=keep)
    return {"removed": [s["name"] for s in doomed], "kept": min(keep, len(snaps))}
