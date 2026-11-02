"""隔离数据保险库：把 quarantined 记录的内容搬出记忆根。

问题：`status=quarantined` 只是逻辑标记，内容仍是明文躺在
`long-term/*.md` 里，`read` / `grep` / `exec` 都能取到 —— 插件过滤挡不住。

做法（三层）：
1. 派生记录：原位只留不含内容的 stub，全文进记忆根之外的保险库；
2. 审计副本：`version-chain.json` / `review-log.md` / `inbox` 里对应正文一并抹掉；
3. 事务快照：改用外部事务，避免 pre-image 又把明文写回 `system/transactions/`。

`daily/` 与 `_raw/` 是 L0 原文，**不做内容删除**（删了就没有出处）。
这两处只能靠操作系统 ACL 兜底，见 `residual_scan` 的口径说明。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import reliability as rel
from storage import load_records, render_records

MANAGED = (
    "core/preferences.md",
    "core/user-core.md",
    "long-term/projects.md",
    "long-term/decisions.md",
    "long-term/lessons.md",
    "semantic/facts.md",
    "semantic/profile.md",
)
SAFE_FIELDS = (
    "schema_version",
    "claim_id",
    "revision_id",
    "event_id",
    "kind",
    "scope",
    "source",
    "status",
    "observed_at",
    "sensitivity",
    "write_policy",
    "injection_policy",
    "relation",
)
DROP_FIELDS = ("subject", "predicate", "value", "evidence", "policy_reasons")
L0_PREFIXES = ("daily/", "_raw/")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha(record: dict) -> str:
    return hashlib.sha256(
        json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def collect(root: Path) -> list[dict]:
    rows, root = [], Path(root)
    for rel_path in MANAGED:
        path = root / rel_path
        if not path.exists():
            continue
        _, records = load_records(path)
        for rec in records:
            if rec.get("status") != "quarantined" or rec.get("redacted"):
                continue
            rows.append({"rel": rel_path, "record": rec, "content_sha256": _sha(rec)})
    return rows


def probes_for(rows: list[dict], min_len: int = 12) -> list[str]:
    """从隔离记录里抽出用于内容匹配的探针（值 + 证据前缀）。"""
    out = []
    for row in rows:
        rec = row["record"]
        for field in ("value", "evidence"):
            text = str(rec.get(field) or "").strip()
            if len(text) >= min_len:
                out.append(text[:24])
    return sorted({p for p in out if p})


def propagate(root: Path, probes: list[str], limit: int = 200) -> tuple[list[dict], dict]:
    """把包含同样正文的其他记录一并隔离。

    按 id 脱敏挡不住重复内容：同一句话可能挂在多个 claim_id 上
    （实测 33 条隔离记录对应 5 个同文待审事件 + 1 条同文 inbox）。
    既然这段正文要隐藏，它的重复副本也必须一起隐藏。
    """
    root = Path(root)
    extra = []
    for rel_path in MANAGED:
        path = root / rel_path
        if not path.exists():
            continue
        _, records = load_records(path)
        for idx, rec in enumerate(records):
            if rec.get("status") == "quarantined" or rec.get("redacted"):
                continue
            blob = "\n".join(str(rec.get(f) or "") for f in ("subject", "predicate", "value", "evidence"))
            if any(p in blob for p in probes):
                rec = dict(rec, status="quarantined", quarantine_reason="content_propagation")
                extra.append({"rel": rel_path, "record": rec, "content_sha256": _sha(rec), "index": idx})
                if len(extra) >= limit:
                    break
        if len(extra) >= limit:
            break
    return extra, {"propagated": len(extra)}


def plan(root: Path) -> dict:
    rows = collect(root)
    return {
        "count": len(rows),
        "items": [
            {
                "rel": r["rel"],
                "claim_id": r["record"].get("claim_id"),
                "revision_id": r["record"].get("revision_id"),
                "bytes": len(json.dumps(r["record"], ensure_ascii=False).encode("utf-8")),
                "content_sha256": r["content_sha256"],
            }
            for r in rows
        ],
    }


def _stub(record: dict, vault_ref: str, digest: str) -> dict:
    stub = {k: record.get(k) for k in SAFE_FIELDS if k in record}
    stub["schema_version"] = 2
    stub["status"] = "quarantined"
    stub["redacted"] = True
    stub["content_sha256"] = digest
    stub["vault_ref"] = vault_ref
    stub["redacted_fields"] = [f for f in DROP_FIELDS if record.get(f)]
    stub["note"] = "content moved to quarantine vault"
    return stub


# --------------------------------------------------------------------------
# 审计副本脱敏
# --------------------------------------------------------------------------


def redact_audit(root: Path, ids: set[str], probes: list | None = None) -> dict:
    """抹掉 version-chain / review-log / inbox 里对应 id 或同文内容的正文。"""
    root = Path(root)
    probes = probes or []
    changes = {}

    vc = root / "system" / "version-chain.json"
    if vc.exists():
        events = json.loads(vc.read_text(encoding="utf-8"))
        hit = 0
        for event in events:
            if not event.get("evidence"):
                continue
            if event.get("id") in ids or any(p in event["evidence"] for p in probes):
                event["evidence"] = ""
                event["evidence_redacted"] = True
                hit += 1
        if hit:
            changes["system/version-chain.json"] = json.dumps(events, ensure_ascii=False, indent=2)

    rl = root / "system" / "review-log.md"
    if rl.exists():
        lines = rl.read_text(encoding="utf-8").split("\n")
        hit = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped.startswith("{"):
                continue
            try:
                event = json.loads(stripped)
            except ValueError:
                continue
            if not event.get("evidence"):
                continue
            if event.get("id") in ids or any(p in event["evidence"] for p in probes):
                event["evidence"] = ""
                event["evidence_redacted"] = True
                lines[i] = json.dumps(event, ensure_ascii=False)
                hit += 1
        if hit:
            changes["system/review-log.md"] = "\n".join(lines)

    inbox = root / "system" / "memory-inbox.md"
    if inbox.exists():
        text = inbox.read_text(encoding="utf-8")
        chunks = text.split("```stellamem")
        hit = 0
        for i in range(1, len(chunks)):
            body, sep, tail = chunks[i].partition("```")
            try:
                rec = json.loads(body.strip())
            except ValueError:
                continue
            blob = "\n".join(str(rec.get(f) or "") for f in ("subject", "predicate", "value", "evidence"))
            if (rec.get("claim_id") in ids or any(p in blob for p in probes)) and blob.strip():
                rec["value"] = ""
                rec["evidence"] = ""
                rec["redacted"] = True
                chunks[i] = "\n" + json.dumps(rec, ensure_ascii=False, sort_keys=True) + sep + tail
                hit += 1
        if hit:
            changes["system/memory-inbox.md"] = "```stellamem".join(chunks)
    return changes


def _restore_audit(root: Path, ids: set[str], payload: dict) -> dict:
    evidence = {item["record"].get("claim_id"): item["record"].get("evidence", "") for item in payload["records"]}
    changes = {}
    vc = root / "system" / "version-chain.json"
    if vc.exists():
        events = json.loads(vc.read_text(encoding="utf-8"))
        hit = 0
        for event in events:
            if event.get("id") in ids and event.get("evidence_redacted"):
                event["evidence"] = evidence.get(event["id"], "")
                event.pop("evidence_redacted", None)
                hit += 1
        if hit:
            changes["system/version-chain.json"] = json.dumps(events, ensure_ascii=False, indent=2)
    return changes


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------


def seal(
    root: Path,
    vault_dir: Path,
    batch: str = "q-vault-1",
    encrypt_key: bytes | None = None,
    propagate_content: bool = True,
) -> dict:
    root, vault_dir = Path(root), Path(vault_dir)
    vault_dir.mkdir(parents=True, exist_ok=True)
    rows = collect(root)
    if not rows:
        return {"moved": 0, "batch": batch, "note": "nothing to seal"}

    base_count = len(rows)
    probes = probes_for(rows)
    if propagate_content:
        extra, _ = propagate(root, probes)
        rows = rows + extra

    payload = {
        "schema_version": 1,
        "batch": batch,
        "created_at": _now(),
        "records": [
            {"rel": r["rel"], "content_sha256": r["content_sha256"], "record": r["record"]}
            for r in rows
        ],
    }
    blob = json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8")
    vault_path = vault_dir / f"{batch}.json"
    if encrypt_key:
        vault_path = vault_path.with_suffix(".json.enc")
        vault_path.write_bytes(_seal_bytes(blob, encrypt_key))
    else:
        vault_path.write_bytes(blob)
    digest = hashlib.sha256(vault_path.read_bytes()).hexdigest()
    (vault_dir / f"{batch}.sha256").write_text(digest + "\n", encoding="utf-8")

    ids = {r["record"].get("claim_id") for r in rows}
    changes = {}
    for rel_path in sorted({r["rel"] for r in rows}):
        path = root / rel_path
        prefix, records = load_records(path)
        for idx, rec in enumerate(records):
            if rec.get("status") != "quarantined" or rec.get("redacted"):
                continue
            ref = f"{batch}:{rec.get('revision_id') or rec.get('claim_id')}"
            records[idx] = _stub(rec, ref, _sha(rec))
        changes[rel_path] = render_records(prefix, records)

    changes.update(redact_audit(root, ids, probes))
    txid = rel.ExternalTransaction(root, vault_dir / "tx").commit(changes)
    return {
        "moved": len(rows),
        "propagated": len(rows) - base_count,
        "batch": batch,
        "vault": str(vault_path),
        "vault_sha256": digest,
        "txid": txid,
        "files": sorted(changes),
    }


def _load_batch(vault_dir: Path, batch: str, encrypt_key: bytes | None) -> dict:
    plain = vault_dir / f"{batch}.json"
    enc = vault_dir / f"{batch}.json.enc"
    if enc.exists():
        if not encrypt_key:
            raise RuntimeError("vault is encrypted; key required")
        return json.loads(_open_bytes(enc.read_bytes(), encrypt_key).decode("utf-8"))
    if plain.exists():
        return json.loads(plain.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"vault batch not found: {batch}")


def unseal(root: Path, vault_dir: Path, batch: str, encrypt_key: bytes | None = None) -> dict:
    root, vault_dir = Path(root), Path(vault_dir)
    payload = _load_batch(vault_dir, batch, encrypt_key)
    by_rel = {}
    for item in payload["records"]:
        by_rel.setdefault(item["rel"], []).append(item)

    ids = {item["record"].get("claim_id") for item in payload["records"]}
    changes = {}
    restored = 0
    for rel_path, items in by_rel.items():
        path = root / rel_path
        if not path.exists():
            raise RuntimeError(f"target file missing: {rel_path}")
        prefix, records = load_records(path)
        index = {(r.get("revision_id") or r.get("claim_id")): i for i, r in enumerate(records)}
        for item in items:
            rec = item["record"]
            key = rec.get("revision_id") or rec.get("claim_id")
            pos = index.get(key)
            if pos is None:
                raise RuntimeError(f"stub missing for {key}")
            current = records[pos]
            if not current.get("redacted"):
                continue
            if current.get("content_sha256") != item["content_sha256"]:
                raise RuntimeError(f"vault content mismatch for {key}")
            records[pos] = rec
            restored += 1
        changes[rel_path] = render_records(prefix, records)

    changes.update(_restore_audit(root, ids, payload))
    txid = rel.ExternalTransaction(root, vault_dir / "tx").commit(changes)
    return {"restored": restored, "batch": batch, "txid": txid}


def verify(root: Path, vault_dir: Path, batch: str | None = None) -> dict:
    root, vault_dir = Path(root), Path(vault_dir)
    plaintext, stubs = [], []
    for rel_path in MANAGED:
        path = root / rel_path
        if not path.exists():
            continue
        _, records = load_records(path)
        for rec in records:
            if rec.get("status") != "quarantined":
                continue
            if rec.get("redacted"):
                stubs.append(rec)
            elif any(rec.get(f) for f in ("value", "evidence")):
                plaintext.append({"rel": rel_path, "claim_id": rec.get("claim_id")})

    names = [batch] if batch else sorted(
        p.name.replace(".json.enc", "").replace(".json", "")
        for p in vault_dir.glob("*.json*")
        if p.suffix in {".json", ".enc"} and not p.name.startswith("journal")
    )
    vaulted = {}
    for name in names:
        candidate = vault_dir / f"{name}.json"
        if candidate.exists():
            for item in json.loads(candidate.read_text(encoding="utf-8"))["records"]:
                vaulted[item["content_sha256"]] = item
    matched = sum(1 for rec in stubs if rec.get("content_sha256") in vaulted)
    return {
        "ok": not plaintext and matched == len(stubs) and len(stubs) > 0,
        "stubs": len(stubs),
        "plaintext_remaining": len(plaintext),
        "plaintext_sample": plaintext[:5],
        "vault_entries": len(vaulted),
        "hash_matched": matched,
        "batches": names,
    }


def residual_scan(root: Path, probes: list) -> dict:
    """按区域统计残留命中，分三类口径：

    - derived：派生记忆/审计副本，必须为 0；
    - l0：daily / _raw 原文，按设计保留（只能靠 ACL）；
    - artifact：benchmark 等非记忆产物，应搬出记忆根。
    """
    root = Path(root)
    derived, l0, artifact = {}, [], {}
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in {".md", ".json", ".jsonl", ".txt", ".csv"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        hits = sum(1 for probe in probes if probe in text)
        if not hits:
            continue
        rel_path = path.relative_to(root).as_posix()
        if any(rel_path.startswith(p) for p in L0_PREFIXES):
            l0.append({"path": rel_path, "hits": hits})
        elif rel_path.startswith("system/") or rel_path in MANAGED or rel_path.startswith("core/"):
            key = "system/transactions" if rel_path.startswith("system/transactions") else rel_path
            derived[key] = derived.get(key, 0) + hits
        else:
            artifact[rel_path] = hits
    return {
        "probes": len(probes),
        "derived_hits": derived,
        "derived_total": sum(derived.values()),
        "l0_hits": sum(x["hits"] for x in l0),
        "l0_files": l0[:10],
        "artifact_hits": artifact,
        "artifact_total": sum(artifact.values()),
    }


# --------------------------------------------------------------------------
# 轻量加密（离线可用，不引入依赖；密钥来自本机文件，不入库）
# --------------------------------------------------------------------------


def _keystream(key: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out.extend(hashlib.sha256(key + counter.to_bytes(8, "big")).digest())
        counter += 1
    return bytes(out[:length])


def _seal_bytes(blob: bytes, key: bytes) -> bytes:
    """SMV1 封包：magic + 完整性标签 + 异或正文。"""
    stream = _keystream(key, len(blob))
    body = bytes(a ^ b for a, b in zip(blob, stream))
    return b"SMV1" + hashlib.sha256(key + body).digest()[:16] + body


def _open_bytes(blob: bytes, key: bytes) -> bytes:
    if blob[:4] != b"SMV1":
        raise RuntimeError("not a SMV1 vault file")
    tag, body = blob[4:20], blob[20:]
    if hashlib.sha256(key + body).digest()[:16] != tag:
        raise RuntimeError("vault checksum mismatch (wrong key or tampered)")
    stream = _keystream(key, len(body))
    return bytes(a ^ b for a, b in zip(body, stream))


def load_key(path: Path) -> bytes:
    data = Path(path).read_bytes()
    return data if len(data) >= 32 else hashlib.sha256(data).digest()
