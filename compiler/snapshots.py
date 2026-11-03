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
- 快照先写入隐藏的 `.partial-*` 目录，fsync 之后再改名发布；
  进程在发布前被杀死时，不会留下可被列出的半份快照；
- 恢复先把完整内容写到旁边的暂存文件并校验，再原子替换 live 库。
  替换前清掉 -wal/-shm（已 checkpoint 进主文件）。杀死进程后，
  live 库要么仍是恢复前，要么是完整恢复后，integrity_check 为 ok；
- 恢复前确认没有活动连接。校验失败不替换 live 库；
- 快照/回滚/清理/失败全部写审计日志；
- 不绕过 append-only：记忆内容的常规修改仍走事务 + valid_to，
  快照恢复只用于运维事故，且每次调用可审计、可追溯。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
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


def _fsync_file(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _fault_point(name: str) -> None:
    """Test-only pause before a snapshot is published or a restore replaces the live db.

    Both STELLAMEM_SNAPSHOT_FAULT and STELLAMEM_SNAPSHOT_FAULT_READY must be set.
    The process then waits until SIGKILL / TerminateProcess. It does not publish
    or replace anything after the pause.
    """
    if os.environ.get("STELLAMEM_SNAPSHOT_FAULT") != name:
        return
    ready = os.environ.get("STELLAMEM_SNAPSHOT_FAULT_READY")
    if not ready:
        return
    marker = Path(ready)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(name, encoding="utf-8")
    try:
        _fsync_file(marker)
    except OSError:
        pass
    while True:
        time.sleep(0.2)


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def _checkpoint_live(path: Path) -> None:
    """Fold a WAL into the main file so sidecars can be removed without losing commits."""
    if not path.exists() or not _is_sqlite_file(path):
        return
    con = sqlite3.connect(path)
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error as exc:
        raise SnapshotError(f"cannot checkpoint live database before restore: {exc}") from exc
    finally:
        con.close()
    _remove_sidecars(path)


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
    """Stage a verified copy, then atomically replace the live database.

    The live file is not unlinked first. A kill before os.replace leaves the
    pre-restore database. A kill during os.replace leaves one of the two
    complete files. Sidecars are removed only after a checkpoint, and before
    the replace, so an old WAL cannot replay onto the new image.
    """
    if dst.exists():
        _assert_no_active_connections(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and _is_sqlite_file(dst):
        _checkpoint_live(dst)
    staging = dst.with_name(dst.name + ".restoring")
    if staging.exists():
        staging.unlink()
    _remove_sidecars(staging)
    try:
        _backup_db(src, staging)
        con = sqlite3.connect(staging)
        try:
            con.execute("PRAGMA journal_mode=DELETE")
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            con.close()
        _remove_sidecars(staging)
        _fsync_file(staging)
        _verify_db_against_entry(staging, entry)
        _remove_sidecars(dst)
        _fault_point("restore-before-replace")
        os.replace(staging, dst)
        _fsync_dir(dst.parent)
        _remove_sidecars(dst)
    except Exception:
        if staging.exists():
            try:
                staging.unlink()
            except OSError:
                pass
        _remove_sidecars(staging)
        raise


def _restore_file(src: Path, dst: Path) -> None:
    """Atomic replace for non-database snapshot files."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    staging = dst.with_name(dst.name + ".restoring")
    shutil.copy2(src, staging)
    _fsync_file(staging)
    os.replace(staging, dst)
    _fsync_dir(dst.parent)


def auto_snapshot(root: Path | str, label: str) -> Path:
    """写操作前调用：对 SNAPSHOT_FILES 做文件级快照，返回快照目录。

    不存在的文件跳过（不报错），以便在部分初始化的环境里也能用。
    .db 文件走 WAL-safe 的 sqlite3 在线备份，其余文件走 copy2。
    内容先写入 `.partial-*`，fsync 后再改名为可发布目录。在线备份失败
    或进程在改名前被杀死时，不留下可列出的半份快照，也不回退到文件拷贝。
    """
    root = Path(root)
    snap_root = _snap_dir(root)
    name = f"{_now_ts()}-{label}"
    partial = snap_root / f".partial-{name}"
    final = snap_root / name
    partial.mkdir(parents=True, exist_ok=False)
    try:
        manifest = {"created_at": _now_ts(), "label": label, "files": []}
        for rel in SNAPSHOT_FILES:
            src = root / rel
            if not src.is_file():
                continue
            target = partial / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if src.suffix.lower() in (".db", ".sqlite", ".sqlite3"):
                method = _backup_db(src, target)
            else:
                shutil.copy2(src, target)
                method = "copy2"
            _fsync_file(target)
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
        _atomic_write(
            partial / "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8"),
        )
        _fsync_dir(partial)
        _fault_point("backup-before-publish")
        os.rename(partial, final)
        _fsync_dir(snap_root)
    except Exception as exc:
        shutil.rmtree(partial, ignore_errors=True)
        try:
            _audit(root, "snapshot_auto_failed", label=label, error=str(exc))
        except Exception:
            pass
        raise
    _audit(root, "snapshot_auto", label=label, path=str(final), files=len(manifest["files"]))
    prune_snapshots(root)
    return final


def list_snapshots(root: Path | str) -> list[dict]:
    """列出快照（按时间升序）。"""
    root = Path(root)
    out = []
    snap_dir = Path(root) / SNAPSHOT_DIRNAME
    if not snap_dir.is_dir():
        return out
    for child in sorted(snap_dir.iterdir()):
        if child.name.startswith("."):
            continue
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
            _restore_file(src, dst)
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
