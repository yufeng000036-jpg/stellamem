"""append-only 内容保护。

边界
- 禁止改：claim_id / revision_id / kind / subject / predicate / value / evidence / source
- 允许改：status、valid_to、expired_at、superseded_by、relation、write_policy 等流转字段
- 想改内容？只能新增一条新 revision，让旧的 superseded

不拦"记录从 inbox 移到正式层"——那是移动，不是改写。
"""

from __future__ import annotations

IMMUTABLE = (
    "claim_id",
    "revision_id",
    "kind",
    "subject",
    "predicate",
    "value",
    "evidence",
    "source",
)

MUTABLE = (
    "status",
    "valid_to",
    "expired_at",
    "superseded_by",
    "relation",
    "auto_write",
    "write_policy",
    "injection_policy",
    "confidence",
)


class ImmutabilityViolation(RuntimeError):
    pass


def fingerprint(rec: dict) -> tuple:
    return tuple(str(rec.get(k) or "") for k in IMMUTABLE)


def _fingerprint_groups(recs) -> dict:
    """按 (claim_id, revision_id) 分组，每组记指纹的多重集。

    不能直接用 dict 映射 id→记录：真实数据里存在同一 id 的多行
    （memory-inbox.md 有 20 组、41 行，值完全相同），字典会互相覆盖，
    把没被改过的行误报成"内容被改写"（部署时实测踩到）。
    """
    from collections import Counter, defaultdict

    groups = defaultdict(Counter)
    for rec in recs:
        key = (rec.get("claim_id"), rec.get("revision_id"))
        groups[key][fingerprint(rec)] += 1
    return groups


def diff_violations(snapshot: dict, current: dict) -> list[dict]:
    """snapshot / current 都是 {文件路径: [记录]}。返回被改写内容的条目。

    判定：同一个 id 的指纹多重集必须与快照完全相同。
    新增 id 合法；已有 id 的指纹多一个、少一个、换一个，都算违规。
    """
    out = []
    for rel, new_recs in current.items():
        old_groups = _fingerprint_groups(snapshot.get(rel, []))
        new_groups = _fingerprint_groups(new_recs)
        for key, new_count in new_groups.items():
            old_count = old_groups.get(key)
            if old_count is None:
                continue  # 新增记录，合法
            if old_count == new_count:
                continue
            sample_old = next(iter(old_count), ())
            sample_new = next(iter(new_count), ())
            changed = [k for k, a, b in zip(IMMUTABLE, sample_old, sample_new) if a != b]
            out.append(
                {
                    "file": rel,
                    "claim_id": key[0],
                    "revision_id": key[1],
                    "fields": changed or ["count"],
                }
            )
    return out


def state_changes(snapshot: dict, current: dict) -> list[dict]:
    """记录状态流转，供审计。"""
    out = []
    for rel, new_recs in current.items():
        old_map = {(r.get("claim_id"), r.get("revision_id")): r for r in snapshot.get(rel, [])}
        for rec in new_recs:
            key = (rec.get("claim_id"), rec.get("revision_id"))
            old = old_map.get(key)
            if old is None:
                out.append(
                    {
                        "file": rel,
                        "claim_id": key[0],
                        "revision_id": key[1],
                        "change": "added",
                        "from": None,
                        "to": rec.get("status"),
                    }
                )
                continue
            for field in ("status", "valid_to", "expired_at", "superseded_by"):
                if str(old.get(field) or "") != str(rec.get(field) or ""):
                    out.append(
                        {
                            "file": rel,
                            "claim_id": key[0],
                            "revision_id": key[1],
                            "change": field,
                            "from": old.get(field),
                            "to": rec.get(field),
                        }
                    )
        new_keys = {(r.get("claim_id"), r.get("revision_id")) for r in new_recs}
        for key, old in old_map.items():
            if key not in new_keys:
                out.append(
                    {
                        "file": rel,
                        "claim_id": key[0],
                        "revision_id": key[1],
                        "change": "moved_out",
                        "from": old.get("status"),
                        "to": None,
                    }
                )
    return out
