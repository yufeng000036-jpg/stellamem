"""写前自动快照 + 显式回滚（F1）。

与 reliability.snapshot() 的区别：
- reliability.snapshot() 是整树 zip，用于人工备份/迁移；
- 本模块是轻量写前快照：只对 index.db + 关键配置文件做文件级快照，
  在每次写操作（索引重建、标签写入、pending 落库、approve/reject）前自动触发，
  出错时可一键恢复。

铁律：
- 回滚是显式运维操作，必须传确认短语，否则拒绝；
- 恢复前校验 SHA256，不一致拒绝；
- 快照/回滚/清理全部写审计日志；
- 不绕过 append-only：记忆内容的常规修改仍走事务 + valid_to，
  快照恢复只用于运维事故，且每次调用可审计、可追溯。
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

# 写前快照覆盖的文件（相对 root）。注意：
# - openclaw.json 是只读红线，绝不快照也不恢复；
# - system/api-token 是密钥，不进快照。
SNAPSHOT_FILES = ("stellamem-data/index.db", "config.json")
SNAPSHOT_DIRNAME = "system/snapshots"
KEEP_DEFAULT = 10
CONFIRM_PHRASE = "RESTORE"


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


def auto_snapshot(root: Path | str, label: str) -> Path:
    """写操作前调用：对 SNAPSHOT_FILES 做文件级快照，返回快照目录。

    不存在的文件跳过（不报错），以便在部分初始化的环境里也能用。
    """
    root = Path(root)
    dest = _snap_dir(root) / f"{_now_ts()}-{label}"
    dest.mkdir(parents=True, exist_ok=False)
    manifest = {"created_at": _now_ts(), "label": label, "files": []}
    for rel in SNAPSHOT_FILES:
        src = root / rel
        if not src.is_file():
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        manifest["files"].append(
            {"path": rel, "bytes": target.stat().st_size, "sha256": _sha256(target)}
        )
    manifest["digest"] = hashlib.sha256(
        "".join(e["sha256"] for e in manifest["files"]).encode("utf-8")
    ).hexdigest()
    (dest / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )
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

    恢复前先校验快照完整性；恢复后记审计。恢复本身不删除快照。
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
