"""语义检索通道：用本地 bge-m3（llama.cpp embedding server）补字面匹配的召回缺口。

设计约束
--------
- 绝不成为硬依赖：服务没起、超时、返回异常 → 一律返回 None，调用方静默降级回字面检索。
- 纯标准库实现：stellamem 运行在裸 venv 里，不能引入 numpy。
- 向量存 SQLite BLOB（float32 小端），检索时只读，不依赖生成端在线。
- 归一化在入库前完成，检索只做点积。

为什么需要它
------------
字符二元组无法跨越「剪辑 → 剪映」「住哪里 → 浙江海宁」这类词面差异：
查询和记忆没有任何公共二元组，overlap 直接为 0 被丢弃。
bge-m3 实测这类查询的正确命中在 0.53~0.71，噪音在 0.30~0.48。
"""

from __future__ import annotations

import array
import json
import os
import sqlite3
import struct
import time
import urllib.error
import urllib.request

DEFAULT_URL = os.environ.get("STELLAMEM_EMBED_URL", "http://127.0.0.1:8082/v1/embeddings")
MODEL = os.environ.get("STELLAMEM_EMBED_MODEL", "bge-m3")
DIM = 1024
# 低于此余弦视为不相关（实测噪音上沿 0.48、正确命中下沿 0.53）
MIN_SIM = float(os.environ.get("STELLAMEM_EMBED_MIN_SIM", "0.45"))
# 把 [0.35, 0.85] 映射到 [0, 1]，用于和历史字面分加权混合
_SIM_LO, _SIM_HI = 0.35, 0.85

# 熔断：服务不常驻，失败后冷却期内直接走字面通道，
# 否则每次检索都要等连接超时（实测 2s），把整体延迟拖垮。
_FAIL_TTL = float(os.environ.get("STELLAMEM_EMBED_FAIL_TTL", "60"))
_last_fail = 0.0


def _cooling() -> bool:
    return (time.time() - _last_fail) < _FAIL_TTL


def _mark_fail():
    global _last_fail
    _last_fail = time.time()


def reset_breaker():
    """供测试或服务恢复后手动复位。"""
    global _last_fail
    _last_fail = 0.0


def embed_texts(texts, timeout: float = 20.0, batch: int = 32):
    """批量取向量。失败返回 None（调用方据此降级），不抛异常。"""
    if not texts:
        return []
    if _cooling():
        return None
    out = []
    for i in range(0, len(texts), batch):
        chunk = [t if str(t or "").strip() else "空" for t in texts[i : i + batch]]
        body = json.dumps({"input": chunk, "model": MODEL}).encode("utf-8")
        req = urllib.request.Request(
            DEFAULT_URL, data=body, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read())
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
            _mark_fail()
            return None
        except Exception:  # noqa: BLE001 - 任何异常都降级，不让检索挂掉
            _mark_fail()
            return None
        items = data.get("data") or []
        if len(items) != len(chunk):
            _mark_fail()
            return None
        for item in items:
            vec = item.get("embedding")
            if not isinstance(vec, list) or len(vec) != DIM:
                _mark_fail()
                return None
            out.append(vec)
    return out


def _normalize(vec):
    norm = sum(x * x for x in vec) ** 0.5
    if norm <= 0:
        return None
    return [x / norm for x in vec]


def _pack(vec) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def _unpack(blob):
    n = len(blob) // 4
    arr = array.array("f")
    arr.frombytes(blob[: n * 4])
    return arr


def available() -> bool:
    """轻量探活，避免每条查询都等超时。"""
    probe = embed_texts(["探活"], timeout=1.5)
    return probe is not None


def record_text(rec) -> str:
    """参与向量化的文本。只取记忆本体，不掺 evidence——否则一句原文会把无关记忆拉进结果。"""
    parts = [str(rec.get(k) or "").strip() for k in ("subject", "predicate", "value")]
    return " ".join(p for p in parts if p)


def build_vectors(con: sqlite3.Connection, force: bool = False, limit: int = 0):
    """为 records 生成/补齐向量。增量：默认只补 vectors 表里没有的。"""
    base = "SELECT id, subject, predicate, value FROM records"
    if not force:
        base += " WHERE id NOT IN (SELECT id FROM vectors)"
    rows = con.execute(base + " ORDER BY id").fetchall()
    if limit:
        rows = rows[:limit]
    if not rows:
        return {"added": 0, "skipped": "no missing"}

    done, failed = 0, 0
    step = 64
    for i in range(0, len(rows), step):
        chunk = rows[i : i + step]
        texts = []
        for r in chunk:
            rec = {"subject": r[1], "predicate": r[2], "value": r[3]}
            texts.append(record_text(rec))
        vecs = embed_texts(texts, timeout=60.0, batch=16)
        if vecs is None:
            failed += len(chunk)
            return {"added": done, "failed": failed, "error": "embed unavailable"}
        for r, vec in zip(chunk, vecs):
            norm = _normalize(vec)
            if norm is None:
                failed += 1
                continue
            con.execute(
                "INSERT OR REPLACE INTO vectors (id, dim, vec, model) VALUES (?,?,?,?)",
                (r[0], DIM, _pack(norm), MODEL),
            )
            done += 1
        con.commit()
    return {"added": done, "failed": failed}


def semantic_scores(con: sqlite3.Connection, query: str, where: str = "1=1", params=()):
    """返回 {record_id: cosine}。服务不可用时返回 None。"""
    # 空表就别浪费一次嵌入调用（新建库、测试环境都没有向量）
    try:
        has = con.execute("SELECT 1 FROM vectors LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        return None
    if not has:
        return None
    vecs = embed_texts([query], timeout=1.5)
    if not vecs:
        return None
    q = _normalize(vecs[0])
    if q is None:
        return None
    import array as _array  # noqa: F401

    rows = con.execute(
        "SELECT v.id, v.vec FROM vectors v JOIN records r ON r.id = v.id WHERE " + where,
        list(params),
    ).fetchall()
    out = {}
    for rid, blob in rows:
        v = _unpack(blob)
        if len(v) != DIM:
            continue
        # 点积；两边都已归一化，等价于余弦
        s = 0.0
        for a, b in zip(q, v):
            s += a * b
        out[rid] = s
    return out


def to_unit_score(cos: float) -> float:
    """把余弦压成 0~1 的可用分，便于与字面分混合。"""
    return max(0.0, min(1.0, (cos - _SIM_LO) / (_SIM_HI - _SIM_LO)))
