"""可重建检索索引：SQLite + FTS5(trigram)。

定位：索引**不是**权威库。Markdown 记忆根是唯一真相，索引随时可删可重建。
因此所有写入都是「读 markdown → 覆盖索引」，没有反向同步。

设计要点
- trigram 分词是中文检索的前提（英文空格分词对中文无效）。
- 分层、类型、时间、人物都建独立列，便于组合过滤。
- 向量是可选列：模型离线时检索完全依赖 FTS，不退化为不可用。
"""

from __future__ import annotations

import functools
import hashlib
import json
import sqlite3
import threading
from pathlib import Path

try:  # 包内与扁平导入都要能用
    from . import semantic
except ImportError:  # pragma: no cover
    import semantic  # type: ignore


MANAGED = (
    ("core/preferences.md", "core"),
    ("core/user-core.md", "core"),
    ("long-term/projects.md", "long-term"),
    ("long-term/decisions.md", "long-term"),
    ("long-term/lessons.md", "long-term"),
    ("semantic/facts.md", "semantic"),
    ("semantic/profile.md", "semantic"),
    ("system/memory-inbox.md", "inbox"),
)

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS records (
  id           TEXT PRIMARY KEY,
  claim_id     TEXT,
  revision_id  TEXT,
  layer        TEXT NOT NULL,
  kind         TEXT,
  subject      TEXT,
  predicate    TEXT,
  value        TEXT,
  evidence     TEXT,
  status       TEXT,
  source       TEXT,
  occurred_at  TEXT,
  observed_at  TEXT,
  record_id    TEXT,
  session_id   TEXT,
  file         TEXT,
  content_hash TEXT,
  redacted     INTEGER DEFAULT 0,
  grams        TEXT,
  valid_to     TEXT,
  expired_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_records_layer ON records(layer);
CREATE INDEX IF NOT EXISTS idx_records_kind ON records(kind);
CREATE INDEX IF NOT EXISTS idx_records_status ON records(status);
CREATE INDEX IF NOT EXISTS idx_records_time ON records(occurred_at);
CREATE INDEX IF NOT EXISTS idx_records_claim ON records(claim_id);
CREATE INDEX IF NOT EXISTS idx_records_person ON records(subject);
CREATE VIRTUAL TABLE IF NOT EXISTS records_fts USING fts5(
  subject, predicate, value, evidence, tokenize='trigram'
);
CREATE TABLE IF NOT EXISTS vectors (id TEXT PRIMARY KEY, dim INTEGER, vec BLOB, model TEXT);
CREATE TABLE IF NOT EXISTS docs (
  id TEXT PRIMARY KEY, layer TEXT, path TEXT, heading TEXT, text TEXT, occurred_at TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(text, tokenize='trigram');
CREATE VIRTUAL TABLE IF NOT EXISTS records_grams USING fts5(grams, tokenize='unicode61');
"""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _grams_str(text: str) -> str:
    """把正文拆成空格分隔的二元组，供 unicode61 FTS 建倒排。"""
    raw = (text or "").lower()
    out = []
    for run in __import__("re").findall(r"[\u4e00-\u9fff]+", raw):
        if len(run) == 1:
            out.append(run)
        for i in range(len(run) - 1):
            out.append(run[i : i + 2])
    out.extend(__import__("re").findall(r"[a-z0-9]{2,}", raw))
    return " ".join(out)


def source_fingerprint(root, include_daily=True) -> str:
    """源文件指纹：MANAGED 文件 + daily/*.md 的 (相对路径, 大小, mtime_ns)。

    索引是否过期只看它，不看记录数——写记忆不经过 API 时（如本地 agent
    直接写 markdown、或写完后重启服务），记录数不变但内容变了，
    旧的 `records == 0` 判断永远发现不了（验收 P1：重启后新记忆召回不到）。
    """
    root = Path(root)
    parts = []
    for rel_path, _ in MANAGED:
        p = root / rel_path
        if p.exists():
            st = p.stat()
            parts.append(f"{rel_path}:{st.st_size}:{st.st_mtime_ns}")
    if include_daily:
        daily = root / "daily"
        if daily.is_dir():
            for p in sorted(daily.glob("*.md")):
                st = p.stat()
                parts.append(f"daily/{p.name}:{st.st_size}:{st.st_mtime_ns}")
    return _sha("\n".join(parts))


def source_content_hash(root, include_daily=True) -> str:
    """源文件内容 hash：只在 mtime/size 指纹变了时才算。
    只 touch、不改内容时不重建（验收第三轮建议：touch 一下就全量重建 3s）。
    读全部文件，比 stat 贵，只走变化分支。"""
    root = Path(root)
    h = hashlib.sha256()
    for rel_path, _ in MANAGED:
        p = root / rel_path
        if p.exists():
            h.update(rel_path.encode("utf-8"))
            h.update(b"\0")
            h.update(p.read_bytes())
            h.update(b"\0")
    if include_daily:
        daily = root / "daily"
        if daily.is_dir():
            for p in sorted(daily.glob("*.md")):
                h.update(f"daily/{p.name}".encode("utf-8"))
                h.update(b"\0")
                h.update(p.read_bytes())
                h.update(b"\0")
    return h.hexdigest()


def _split_daily(path: Path) -> list[dict]:
    """把 daily 长文按二级标题切块，作为可检索的原文段落。"""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    blocks, heading, buf = [], "", []
    for line in text.splitlines():
        if line.startswith("## "):
            if buf:
                blocks.append({"heading": heading, "text": "\n".join(buf).strip()})
            heading, buf = line[3:].strip(), []
        else:
            buf.append(line)
    if buf:
        blocks.append({"heading": heading, "text": "\n".join(buf).strip()})
    out = []
    for idx, block in enumerate(blocks):
        body = block["text"]
        if len(body) < 20:
            continue
        # 超长段落再按 1200 字切，避免单条命中占满预算
        for part in range(0, len(body), 1200):
            chunk = body[part : part + 1200]
            if len(chunk) < 20:
                continue
            out.append(
                {
                    "id": f"{path.stem}#{idx}-{part}",
                    "heading": block["heading"],
                    "text": chunk,
                    "occurred_at": f"{path.stem}T00:00:00",
                }
            )
    return out


def _locked(method):
    """把 Index 的连接访问串行化：同一连接绝不能被两个线程同时用。

    背景：sqlite3 连接（check_same_thread=False）允许多线程“轮流”用，
    但两个线程同时在上面开 cursor 会互相顶掉，一个拿到 0 列的行
    （IndexError）或 InterfaceError。HTTP 线程池里 health/search/recall
    都会走到这个连接，#55 之前 ensure_index() 卡 3 秒把并发堵住了，
    现在它 3ms 就返回，并发读直接撞上这个既有缺陷。
    用 RLock：build() 内部会调 sync_vectors()，同线程重入必须放行。
    """

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


class Index:
    def __init__(self, db_path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 一把 RLock 护住整个连接：HTTP 线程池共用它，读和读之间也会互相干扰，
        # 不能只锁写。后台重建线程用自己的 Index/连接，不抢这把锁。
        self._lock = threading.RLock()
        self.con = sqlite3.connect(self.path, check_same_thread=False, timeout=30.0)
        self.con.row_factory = sqlite3.Row
        self._migrate()
        self.con.executescript(SCHEMA)

    def _migrate(self):
        """索引不是权威库：结构变了就整表重建，不做在线迁移。"""
        self.con.executescript(
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);"
        )
        row = self.con.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
        if row and row[0] == "3":
            return
        for table in ("records", "records_fts", "records_grams", "docs", "docs_fts", "vectors"):
            self.con.execute(f"DROP TABLE IF EXISTS {table}")
        self.con.execute("INSERT OR REPLACE INTO meta VALUES ('schema','3')")
        self.con.commit()

    # ---------------- 构建 ----------------

    @_locked
    def build(self, root, include_daily=True, include_pending=True) -> dict:
        # Records and freshness metadata commit together, or roll back together.
        with self.con:
            return self._build(root, include_daily, include_pending)

    def _build(self, root, include_daily=True, include_pending=True):
        try:  # 包内与扁平导入都要能用（MCP stdio 只加了仓库根，compiler/ 不在 sys.path）
            from .storage import load_records
        except ImportError:  # pragma: no cover - flat layout
            from storage import load_records

        root = Path(root)
        initial_fingerprint = source_fingerprint(root, include_daily)
        initial_content_hash = source_content_hash(root, include_daily)
        self.con.execute("DELETE FROM records")
        self.con.execute("DELETE FROM records_fts")
        self.con.execute("DELETE FROM records_grams")
        self.con.execute("DELETE FROM docs")
        self.con.execute("DELETE FROM docs_fts")

        total, redacted = 0, 0
        for rel_path, layer in MANAGED:
            path = root / rel_path
            if not path.exists():
                continue
            try:
                _, records = load_records(path)
            except ValueError as exc:
                raise ValueError(f"index incomplete: cannot parse {rel_path}") from exc
            for rec in records:
                status = rec.get("status")
                if status == "pending" and not include_pending:
                    continue
                rid = f"{rec.get('claim_id')}:{rec.get('revision_id')}"
                is_redacted = 1 if rec.get("redacted") or not rec.get("value") else 0
                redacted += is_redacted
                prov = rec.get("provenance") or {}
                occurred = prov.get("timestamp") or rec.get("observed_at") or ""
                row = (
                    rid,
                    rec.get("claim_id"),
                    rec.get("revision_id"),
                    layer,
                    rec.get("kind"),
                    rec.get("subject") or "",
                    rec.get("predicate") or "",
                    "" if is_redacted else (rec.get("value") or ""),
                    "" if is_redacted else (rec.get("evidence") or "")[:240],
                    status,
                    rec.get("source"),
                    occurred,
                    rec.get("observed_at"),
                    prov.get("record_id"),
                    prov.get("session_id") or (prov.get("source_ref") or "").split("/")[-1],
                    rel_path,
                    _sha(json.dumps(rec, ensure_ascii=False, sort_keys=True)),
                    is_redacted,
                )
                grams = _grams_str(row[7] + " " + row[8] + " " + row[5] + " " + row[6])
                cur = self.con.execute(
                    "INSERT OR REPLACE INTO records VALUES (%s)" % ",".join("?" * 21),
                    row + (grams, rec.get("valid_to"), rec.get("expired_at")),
                )
                self.con.execute(
                    "INSERT INTO records_fts(rowid, subject, predicate, value, evidence) VALUES (?,?,?,?,?)",
                    (cur.lastrowid, row[5], row[6], row[7], row[8]),
                )
                self.con.execute(
                    "INSERT INTO records_grams(rowid, grams) VALUES (?,?)",
                    (cur.lastrowid, grams),
                )
                total += 1

        doc_count = 0
        if include_daily:
            for path in sorted((root / "daily").glob("*.md")) if (root / "daily").exists() else []:
                for doc in _split_daily(path):
                    cur = self.con.execute(
                        "INSERT OR REPLACE INTO docs VALUES (?,?,?,?,?,?)",
                        (
                            doc["id"],
                            "daily",
                            f"daily/{path.name}",
                            doc["heading"],
                            doc["text"],
                            doc["occurred_at"],
                        ),
                    )
                    self.con.execute(
                        "INSERT INTO docs_fts(rowid, text) VALUES (?,?)",
                        (cur.lastrowid, doc["text"]),
                    )
                    doc_count += 1

        if source_fingerprint(root, include_daily) != initial_fingerprint:
            raise RuntimeError("memory sources changed during index build; retry")
        meta = {
            "built_at": __import__("datetime")
            .datetime.now(__import__("datetime").timezone.utc)
            .isoformat(timespec="seconds"),
            "records": total,
            "docs": doc_count,
            "redacted": redacted,
            "root": str(root),
            "schema": "3",
            "source_fingerprint": initial_fingerprint,
            "source_content_hash": initial_content_hash,
        }
        for key, value in meta.items():
            self.con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, str(value)))
        self.con.commit()

        # 向量与索引同批维护：先清孤儿（revision 变了 id 就变），再补缺失。
        # 嵌入服务不在线时内部静默跳过，不影响建索引本身。
        try:
            meta["vectors"] = self.sync_vectors()
        except Exception as exc:  # noqa: BLE001
            meta["vectors"] = {"error": type(exc).__name__}
        return meta

    @_locked
    def sync_vectors(self, force: bool = False) -> dict:
        """清孤儿向量 + 补缺失向量。嵌入服务不可用时返回 skipped。"""
        removed = self.con.execute(
            "DELETE FROM vectors WHERE id NOT IN (SELECT id FROM records)"
        ).rowcount
        self.con.commit()
        if not semantic.available():
            return {"removed": removed, "added": 0, "skipped": "embed offline"}
        result = semantic.build_vectors(self.con, force=force)
        result["removed"] = removed
        return result

    @_locked
    def is_stale(self, root, include_daily=True) -> bool:
        """索引是否过期：无指纹（旧版建的库）或指纹不一致都算过期。

        旧库第一次调用会重建一次，之后指纹正常，不会反复重建。
        mtime/size 指纹变了时再算内容 hash：只 touch、不改内容时
        只更新指纹，不重建（验收第三轮建议：touch 一下就全量重建 3s）。
        """
        current_fp = source_fingerprint(root, include_daily)
        row = self.con.execute("SELECT value FROM meta WHERE key='source_fingerprint'").fetchone()
        if row is None:
            return True
        if row[0] == current_fp:
            return False
        hash_row = self.con.execute(
            "SELECT value FROM meta WHERE key='source_content_hash'"
        ).fetchone()
        if hash_row is not None and hash_row[0] == source_content_hash(root, include_daily):
            self.con.execute(
                "INSERT OR REPLACE INTO meta VALUES ('source_fingerprint', ?)",
                (current_fp,),
            )
            self.con.commit()
            return False
        return True

    @_locked
    def stats(self) -> dict:
        def get(key):
            return (
                self.con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone() or [None]
            )[0]

        by_layer = {
            row["layer"]: row["n"]
            for row in self.con.execute("SELECT layer, COUNT(*) n FROM records GROUP BY layer")
        }
        by_kind = {
            row["kind"]: row["n"]
            for row in self.con.execute(
                "SELECT kind, COUNT(*) n FROM records WHERE kind IS NOT NULL GROUP BY kind ORDER BY n DESC"
            )
        }
        return {
            "built_at": get("built_at"),
            "records": self.con.execute("SELECT COUNT(*) FROM records").fetchone()[0],
            "docs": self.con.execute("SELECT COUNT(*) FROM docs").fetchone()[0],
            "vectors": self.con.execute("SELECT COUNT(*) FROM vectors").fetchone()[0],
            "redacted": self.con.execute(
                "SELECT COUNT(*) FROM records WHERE redacted=1"
            ).fetchone()[0],
            "by_layer": by_layer,
            "by_kind": by_kind,
            "db_bytes": self.path.stat().st_size if self.path.exists() else 0,
        }

    @_locked
    def close(self):
        self.con.close()
