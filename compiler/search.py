"""检索与召回：关键词 + 时间 + 人物 + 分层过滤，按预算打包上下文。

三条硬约束
1. 索引只读：任何查询都不写记忆根。
2. 模型离线可用：FTS5 trigram 是主路径，向量只是加分项。
3. 召回有预算：返回多少条、多少字符由调用方给，绝不整库注入。
"""

from __future__ import annotations

import contextvars
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone

try:  # 包内导入（compiler.search）与扁平导入（sys.path 加 compiler/）都要能用
    from . import semantic
    from . import rank as rank_mod
except ImportError:  # pragma: no cover
    import semantic  # type: ignore
    import rank as rank_mod  # type: ignore

# 字面/语义权重。语义通道默认开启后字面噪声会压住正确项，故可调。
W_LEX = float(os.environ.get("STELLAMEM_W_LEX", "0.20"))
W_SEM = float(os.environ.get("STELLAMEM_W_SEM", "0.62"))

KIND_LABEL = {
    "fact": "事实",
    "project": "项目",
    "decision": "决定",
    "preference": "偏好",
    "lesson": "教训",
    "procedure": "流程",
    "relationship": "关系",
    "identity": "身份",
}

LAYER_LABEL = {
    "core": "核心",
    "long-term": "长期",
    "semantic": "语义",
    "inbox": "待审",
    "daily": "日记",
}

# 意图 → 类型召回。不靠关键词命中，靠类型 + 主题双通道。
INTENTS = {
    "project": (r"项目|进度|做到哪|里程碑|交付", ("project",)),
    "decision": (r"决定|为什么选|改成|定下|放弃", ("decision",)),
    "preference": (r"偏好|喜欢|习惯|更愿意|讨厌", ("preference",)),
    "lesson": (r"教训|踩坑|下次|别再|怎么避免", ("lesson", "procedure")),
    "relationship": (r"关系|朋友|家人|谁|陪", ("relationship",)),
    "timeline": (r"什么时候|第一次|哪年|多久|变化", ("fact", "project", "decision")),
}

_TOKEN = re.compile(r"[0-9A-Za-z\u4e00-\u9fff]+")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def fts_expr(query: str) -> str | None:
    terms = [t for t in _TOKEN.findall(query or "") if len(t) >= 3]
    if not terms:
        terms = [t for t in _TOKEN.findall(query or "") if len(t) >= 2]
    if not terms:
        return None
    return " AND ".join(f'"{t}"' for t in terms[:6])


# 泛问：只问"我有哪些 X"，没有具体话题。这类问题走类型召回，不走文本匹配。
BROAD_WORDS = {
    "preference": ("偏好", "喜好", "习惯", "口味"),
    "project": ("项目", "手头", "在做"),
    "decision": ("决定", "选择", "定过"),
    "lesson": ("教训", "踩过", "坑"),
    "relationship": ("关系", "朋友", "认识谁"),
    "identity": ("身份", "我是谁", "情况"),
}


def detect_broad(prompt: str) -> str | None:
    """短问句 + 类型词 = 泛问。长句说明有具体话题，走文本检索。"""
    text = (prompt or "").strip()
    if not text or len(text) > 14:
        return None
    for kind, words in BROAD_WORDS.items():
        if any(w in text for w in words):
            return kind
    return None


def broad_recall(
    con,
    kind: str,
    limit: int = 8,
    status=("active", "pending"),
    subject: str | None = None,
) -> list[dict]:
    """按类型取记忆，按可信度 + 时间排序。

    subject: 可信主体过滤。传入时只返回该主体的记录（精确匹配），
    不传则不过滤（向后兼容）。泛问路径也必须接主体，否则隔离旁路。
    """
    where, params = _filter_sql((kind,), None, status, subject)
    rows = con.execute(
        f"SELECT * FROM records WHERE {where} ORDER BY redacted ASC, "
        "CASE status WHEN 'active' THEN 0 ELSE 1 END, occurred_at DESC LIMIT 200",
        params,
    ).fetchall()
    out = []
    for row in rows:
        rec = dict(row)
        if rec.get("redacted") or not rec.get("value"):
            continue
        rec["score"] = round(0.9 if rec.get("status") == "active" else 0.55, 3)
        rec["overlap"] = 1.0
        rec["match"] = "kind_recall"
        out.append(rec)
        if len(out) >= limit:
            break
    return out


def detect_intent(prompt: str) -> str | None:
    for name, (pattern, _) in INTENTS.items():
        if re.search(pattern, prompt or ""):
            return name
    return None


def _recency_score(occurred: str | None) -> float:
    if not occurred:
        return 0.3
    try:
        when = datetime.fromisoformat(occurred.replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
    except ValueError:
        return 0.3
    days = max(0.0, (_now() - when).total_seconds() / 86400)
    return 1.0 / (1.0 + days / 180.0)


def _like_where(query: str):
    terms = [t for t in _TOKEN.findall(query or "") if len(t) >= 2][:4]
    if not terms:
        return None, []
    clause = " AND ".join(
        "(subject LIKE ? OR predicate LIKE ? OR value LIKE ? OR evidence LIKE ?)"
        for _ in terms
    )
    params = []
    for term in terms:
        params += [f"%{term}%"] * 4
    return clause, params


def bigrams(text: str) -> set:
    """中文按二元组切分，拉丁按词切分。不依赖分词库。"""
    raw = (text or "").lower()
    grams = set()
    for run in re.findall(r"[\u4e00-\u9fff]+", raw):
        if len(run) == 1:
            grams.add(run)
        for i in range(len(run) - 1):
            grams.add(run[i : i + 2])
    for word in re.findall(r"[a-z0-9]{2,}", raw):
        grams.add(word)
    return grams


def overlap_score(query_grams: set, rec: dict) -> float:
    if not query_grams:
        return 0.0
    # 只比记忆本体，不把 evidence 拼进来：否则一句原文就能把无关记忆拉进结果
    blob = " ".join(str(rec.get(f) or "") for f in ("subject", "value"))
    return len(query_grams & bigrams(blob)) / len(query_grams)


def _as_tuple(value):
    if value is None:
        return None
    if isinstance(value, str):
        return (value,)
    return tuple(value)


def _filter_sql(kinds, layers, status, person):
    kinds, layers, status = _as_tuple(kinds), _as_tuple(layers), _as_tuple(status)
    clause, params = [], []
    if kinds:
        clause.append(f"kind IN ({','.join('?' * len(kinds))})")
        params += list(kinds)
    if layers:
        clause.append(f"layer IN ({','.join('?' * len(layers))})")
        params += list(layers)
    if status:
        clause.append(f"status IN ({','.join('?' * len(status))})")
        params += list(status)
    if person:
        # 主体隔离必须精确匹配：LIKE '%user%' 会误匹配 'superuser'。
        # 主体值来自固定枚举（user/persona/viewer），精确匹配是安全的。
        clause.append("subject = ?")
        params.append(person)
    return (" AND ".join(clause) if clause else "1=1"), params


def search(
    con: sqlite3.Connection,
    query: str,
    kinds=None,
    layers=None,
    since: str | None = None,
    until: str | None = None,
    person: str | None = None,
    status=("active", "pending"),
    limit: int = 10,
    min_overlap: float = 0.5,
) -> list[dict]:
    """混合检索：FTS5 候选 + 字符二元组重叠打分。

    纯 FTS 对中文长句无效（trigram 需要 ≥3 字连续命中），
    纯 LIKE 又无法排序；所以用重叠度做排序信号，FTS 只负责召回候选。
    """
    started = time.perf_counter()
    # 权重随语义通道是否真的可用而切换：降级时必须回到纯字面时代的配比，
    # 否则字面分只剩 0.20，会被 recency/status 这些次要项盖过（实测 r@1 掉到 0.078）。
    w_lex = float(os.environ.get("STELLAMEM_W_LEX", W_LEX))
    w_sem = float(os.environ.get("STELLAMEM_W_SEM", W_SEM))
    # 泛问走类型召回：问「我有哪些偏好」不该靠文本匹配
    broad = detect_broad(query)
    if broad and not kinds:
        picked = broad_recall(
            con,
            broad,
            limit=limit,
            status=status or ("active", "pending"),
            subject=person,
        )
        if picked:
            return picked
    # 默认只检索可注入状态：quarantined 记录不得被任何读路径搜出。
    if status is None:
        status = ("active", "pending")
    where, params = _filter_sql(kinds, layers, status, person)
    query_grams = bigrams(query)
    if not query_grams:
        return []

    pool: dict[str, dict] = {}
    fts_ids = set()

    # 候选 1：二元组倒排（中文可用，走索引，不随库变大而线性变慢）
    grams = [g for g in query_grams if len(g) >= 2][:24]
    if grams:
        expr_grams = " OR ".join(f'"{g}"' for g in grams)
        try:
            for row in con.execute(
                "SELECT r.* FROM records_grams JOIN records r ON r.rowid = records_grams.rowid "
                f"WHERE records_grams MATCH ? AND {where} LIMIT 300",
                [expr_grams] + params,
            ):
                rec = dict(row)
                pool[rec["id"]] = rec
                fts_ids.add(rec["id"])
        except sqlite3.OperationalError:
            pass

    # 候选 2：trigram 短语（长查询更精确）
    expr = fts_expr(query)
    if expr:
        try:
            for row in con.execute(
                "SELECT r.* FROM records_fts JOIN records r ON r.rowid = records_fts.rowid "
                f"WHERE records_fts MATCH ? AND {where} LIMIT 200",
                [expr] + params,
            ):
                rec = dict(row)
                pool[rec["id"]] = rec
                fts_ids.add(rec["id"])
        except sqlite3.OperationalError:
            pass

    # 候选 3：精确子串。任何方案都不能比"字符串就在里面"更差。
    exact_ids = set()
    like = f"%{query.strip()}%"
    for row in con.execute(
        f"SELECT * FROM records WHERE (value LIKE ? OR evidence LIKE ? OR subject LIKE ?) "
        f"AND {where} LIMIT 200",
        [like, like, like] + params,
    ):
        rec = dict(row)
        pool[rec["id"]] = rec
        exact_ids.add(rec["id"])

    # 候选 4：语义通道。字面匹配跨不过「剪辑→剪映」「住哪→海宁」这类词面差异，
    # 二元组交集为 0 会被直接丢弃，所以必须有一条不依赖字面的召回路径。
    # 服务不在线时 sem 为 None，整段静默跳过，行为退回纯字面检索。
    sem = None
    if os.environ.get("STELLAMEM_SEMANTIC", "1") != "0":
        try:
            sem = semantic.semantic_scores(con, query, where, params)
        except Exception:  # noqa: BLE001 - 语义是增强项，任何异常都不能影响字面检索
            sem = None
    if sem:
        hot = sorted(
            ((rid, cos) for rid, cos in sem.items() if cos >= semantic.MIN_SIM),
            key=lambda z: -z[1],
        )[:60]
        missing = [rid for rid, _ in hot if rid not in pool]
        if missing:
            marks = ",".join("?" * len(missing))
            for row in con.execute(
                f"SELECT * FROM records WHERE id IN ({marks}) AND {where}",
                missing + list(params),
            ):
                rec = dict(row)
                pool[rec["id"]] = rec

    if not sem:
        # 语义通道不可用（服务未启动 / 库里还没有向量）：权重回到纯字面配比。
        # 否则字面分只占 0.20，会被 recency/status 盖过，实测 r@1 从 0.33 掉到 0.078。
        w_lex, w_sem = 0.70, 0.0

    intent = detect_intent(query)
    intent_kinds = set(INTENTS[intent][1]) if intent else set()
    want_recent = bool(re.search(r"最近|这几天|最近在|近期", query or ""))

    scored = []
    for rec in pool.values():
        if rec.get("redacted"):
            continue
        occurred = rec.get("occurred_at") or rec.get("observed_at") or ""
        if since and occurred and occurred[:10] < since:
            continue
        if until and occurred and occurred[:10] > until:
            continue
        rec_grams = set((rec.get("grams") or "").split())
        overlap = (
            len(query_grams & rec_grams) / len(query_grams)
            if query_grams and rec_grams
            else 0.0
        )
        if rec["id"] in exact_ids:
            # 精确命中是最强信号：不受分词差异影响（拉丁片段、专有名词）
            overlap = 1.0
        cos = sem.get(rec["id"], 0.0) if sem else 0.0
        sem_unit = semantic.to_unit_score(cos)
        if overlap <= 0 and cos < semantic.MIN_SIM and not want_recent:
            # 两条通道都没有信号才丢。语义够高但字面为 0 的记录必须留下，
            # 否则「剪辑→剪映」这类正确命中永远进不来。
            continue
        if overlap <= 0 and cos < semantic.MIN_SIM and want_recent:
            # 「最近在做什么」这类时间问句：只取近 30 天，按时间排，不硬凑语义
            if (
                not occurred
                or occurred[:10]
                < (
                    __import__("datetime").date.today()
                    - __import__("datetime").timedelta(days=30)
                ).isoformat()
            ):
                continue
        lexical = overlap
        if rec["id"] in fts_ids:
            lexical = min(1.0, lexical + 0.15)
        boost = (
            0.25
            if rec.get("kind") in intent_kinds
            else (-0.08 if intent_kinds else 0.0)
        )
        needle = query.strip()
        if needle and (
            needle in (rec.get("value") or "") or needle in (rec.get("evidence") or "")
        ):
            boost += 0.35
        score = (
            w_lex * lexical
            + w_sem * sem_unit
            + 0.10 * _recency_score(occurred)
            + 0.05 * (1.0 if rec.get("status") == "active" else 0.25)
            + 0.03 * min(1.0, len(rec.get("evidence") or "") / 120.0)
            + boost
        )
        rec["score"] = round(score, 4)
        rec["overlap"] = round(overlap, 3)
        rec["semantic"] = round(cos, 3)
        rec["match"] = "hybrid"
        scored.append(rec)

    scored.sort(key=lambda r: (-r["score"], -len(r.get("value") or "")))

    # 折叠重复正文：同一句话只留最高分
    seen, out = set(), []
    for rec in scored:
        key = re.sub(r"\s+", "", (rec.get("value") or ""))[:48]
        if key in seen:
            continue
        seen.add(key)
        out.append(rec)
        if len(out) >= limit:
            break

    for rec in out:
        rec["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return out


def search_docs(con: sqlite3.Connection, query: str, limit: int = 5) -> list[dict]:
    expr = fts_expr(query)
    if not expr:
        return []
    rows = con.execute(
        "SELECT d.*, bm25(docs_fts) AS rank FROM docs_fts "
        "JOIN docs d ON d.rowid = docs_fts.rowid WHERE docs_fts MATCH ? LIMIT ?",
        (expr, limit),
    ).fetchall()
    out = []
    for row in rows:
        rec = dict(row)
        snippet_terms = [t for t in _TOKEN.findall(query or "") if len(t) >= 2]
        text = rec.get("text") or ""
        pos = min((text.find(t) for t in snippet_terms if t in text), default=0)
        rec["snippet"] = text[max(0, pos - 40) : pos + 160]
        rec["score"] = round(1.0 / (1.0 + max(0.0, float(rec.pop("rank")))), 4)
        out.append(rec)
    return out


def _as_of_base(moment: str, subject: str | None = None, kinds=None):
    """Build the as_of WHERE clause and args (without LIMIT/OFFSET).

    Shared by as_of() and as_of_paged() so the two can never drift apart
    on filtering: status is ('active', 'superseded') and the valid_to
    window is the real criterion, never status='active' alone.
    """
    where, params = _filter_sql(kinds, None, ("active", "superseded"), None)
    sql = (
        "SELECT * FROM records WHERE "
        "COALESCE(occurred_at,'') <= ? "
        "AND (valid_to IS NULL OR valid_to = '' OR valid_to > ?) "
        f"AND {where} "
    )
    args = [moment, moment] + list(params)
    if subject:
        sql = sql.replace(f"AND {where} ", f"AND {where} AND subject = ? ")
        args = args + [subject]
    return sql, args


def as_of(
    con: sqlite3.Connection,
    moment: str,
    subject: str | None = None,
    kinds=None,
    limit: int | None = None,
) -> list[dict]:
    """Replay: what this memory set believed at a moment.

    occurred_at is the "fact became true" time; empty valid_to means
    still valid. Superseded facts with empty valid_to are excluded from
    "now" but remain reachable via an earlier as_of moment.

    Fix for a defect found in the 2026-11-02 launch step 10: the old
    default limit=100 silently truncated by occurred_at DESC (412 active
    records returned only the first 100), so replay lost records. The
    default is now limit=None (no LIMIT clause). limit only applies when
    a caller passes it explicitly; use as_of_paged() when truncation
    must be reported.
    """
    sql, args = _as_of_base(moment, subject, kinds)
    sql += "ORDER BY occurred_at DESC"
    if limit is not None:
        sql += " LIMIT ?"
        args = args + [limit]
    rows = con.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def as_of_paged(
    con: sqlite3.Connection,
    moment: str,
    subject: str | None = None,
    kinds=None,
    limit: int | None = 100,
    offset: int = 0,
) -> dict:
    """Paged as_of(): same filter as as_of(), but truncation is explicit.

    Returns {"items", "total", "returned", "limit", "offset", "truncated"}.
    truncated=True exactly when total > offset + returned, so a caller
    can never again mistake a truncated page for the full replay.

    D1 fix: offset is honoured even when limit is None. Previously the
    LIMIT/OFFSET clause was only added when limit was not None, so
    as_of_paged(limit=None, offset=50) silently returned all rows from
    the start instead of rows 50... . Now an offset without a limit is
    applied as LIMIT -1 OFFSET ? (SQLite's unlimited-page form), so the
    returned page is always items[offset:offset+limit] (or items[offset:]
    when limit is None), total is the unpaged count, and truncated is
    False for a limit=None page because it already contains every row
    after the offset.
    """
    if limit is not None and limit < 0:
        raise ValueError("limit must be >= 0 or None")
    if offset < 0:
        raise ValueError("offset must be >= 0")
    sql, args = _as_of_base(moment, subject, kinds)
    total = con.execute(f"SELECT COUNT(*) FROM ({sql.rstrip()})", args).fetchone()[0]
    page_sql = sql + "ORDER BY occurred_at DESC"
    page_args = list(args)
    if limit is not None:
        page_sql += " LIMIT ? OFFSET ?"
        page_args += [limit, offset]
    elif offset:
        # D1: limit=None must not drop the offset. LIMIT -1 means
        # "no limit" in SQLite, so OFFSET still applies.
        page_sql += " LIMIT -1 OFFSET ?"
        page_args += [offset]
    rows = con.execute(page_sql, page_args).fetchall()
    items = [dict(r) for r in rows]
    return {
        "items": items,
        "total": total,
        "returned": len(items),
        "limit": limit,
        "offset": offset,
        "truncated": bool(limit is not None and total > offset + len(items)),
    }


def timeline(
    con: sqlite3.Connection,
    since: str | None = None,
    until: str | None = None,
    limit: int = 200,
) -> list[dict]:
    sql = (
        "SELECT id, claim_id, kind, subject, value, occurred_at, observed_at, layer, status "
        "FROM records WHERE occurred_at <> ''"
    )
    params = []
    if since:
        sql += " AND occurred_at >= ?"
        params.append(since)
    if until:
        sql += " AND occurred_at <= ?"
        params.append(until + "\uffff")
    sql += " ORDER BY occurred_at ASC LIMIT ?"
    params.append(limit)
    return [dict(row) for row in con.execute(sql, params)]


def history(con: sqlite3.Connection, claim_id: str) -> list[dict]:
    """同一条记忆的所有修订，按时间排列 → 思想演变。"""
    rows = con.execute(
        "SELECT id, claim_id, value, status, occurred_at, observed_at, file FROM records "
        "WHERE claim_id = ? ORDER BY COALESCE(NULLIF(occurred_at,''), observed_at) ASC",
        (claim_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def related(con: sqlite3.Connection, record_id: str, limit: int = 8) -> list[dict]:
    """同主题 / 同类型的关联记忆。"""
    row = con.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
    if not row:
        return []
    base = dict(row)
    rows = con.execute(
        "SELECT * FROM records WHERE id <> ? AND (subject = ? OR kind = ?) "
        "ORDER BY CASE WHEN subject = ? THEN 0 ELSE 1 END, occurred_at DESC LIMIT ?",
        (record_id, base.get("subject"), base.get("kind"), base.get("subject"), limit),
    ).fetchall()
    return [dict(r) for r in rows]


def counts(con: sqlite3.Connection) -> dict:
    def group(column):
        return {
            row[0]: row[1]
            for row in con.execute(
                f"SELECT {column}, COUNT(*) FROM records WHERE {column} IS NOT NULL GROUP BY {column}"
            )
        }

    return {
        "total": con.execute("SELECT COUNT(*) FROM records").fetchone()[0],
        "by_layer": group("layer"),
        "by_kind": group("kind"),
        "by_status": group("status"),
    }


# --------------------------------------------------------------------------
# 召回：把结果打包成可注入的上下文，受字符预算约束
# --------------------------------------------------------------------------


def _trim(text: str, width: int) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= width else text[: width - 1] + "…"


# 可信请求主体。只能由服务端在鉴权之后设置，不能从 prompt 或卡片内容里推断。
_REQUEST_SUBJECT: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "stellamem_request_subject", default=None
)


@contextmanager
def request_subject(subject: str | None):
    """在当前上下文绑定可信请求主体。嵌套调用按 contextvars 恢复。"""
    token = _REQUEST_SUBJECT.set(subject)
    try:
        yield
    finally:
        _REQUEST_SUBJECT.reset(token)


def _trusted_subject(explicit: str | None) -> str | None:
    """主体只来自可信上下文或调用方显式参数。上下文优先，空值视为未知。"""
    current = _REQUEST_SUBJECT.get()
    if isinstance(current, str) and current.strip():
        return current.strip()
    if explicit is None:
        return None
    text = str(explicit).strip()
    return text or None


def _match_pref_cards(prompt_grams, cards, quota, min_score, subject=None):
    """偏好卡独立匹配通道：按 subject+text 的 bigram overlap 打分。

    没有可信 subject 时返回空（失败关闭），绝不把多个主体的卡混进同一次召回。
    """
    if subject is None or not str(subject).strip():
        return []
    scored = []
    for card in cards or []:
        if not isinstance(card, dict):
            continue
        if card.get("status") not in (None, "active"):
            continue
        card_subject = str(card.get("subject") or "")
        if subject is not None and card_subject != str(subject):
            continue
        text = str(card.get("text") or "")
        if not card_subject or not text:
            continue
        score = overlap_score(prompt_grams, {"subject": card_subject, "value": text})
        if score >= min_score:
            scored.append((score, card))
    scored.sort(key=lambda t: (-t[0], str(t[1].get("subject"))))
    out = []
    for score, card in scored[: max(0, quota)]:
        ev = card.get("evidence") or []
        rec = {
            "kind": "preference_summary",
            "subject": card.get("subject"),
            "value": card.get("text"),
            "evidence": ev,
            "score": score,
            "weak": False,
            "injected": "preference_card",
            "_extra_line": "- [\u504f\u597d] %s: %s\uff08\u8bc1\u636e%d\u6761\uff09"
            % (card.get("subject"), _trim(card.get("text"), 90), len(ev)),
            "_usage_id": "pref:" + str(card.get("subject")),
        }
        out.append(rec)
    return out


def _match_patterns(prompt, patterns, quota, min_score, subject=None):
    """规律项独立匹配通道：得分已乘 PATTERN_WEIGHT(<1)，低于原始记录。

    与偏好卡同一条主体边界：没有可信 subject 就返回空；只返回该 subject 的规律项。
    配额在主体过滤之后计算，避免其他主体占掉名额。
    """
    if subject is None or not str(subject).strip():
        return []
    try:
        from sleep_consolidation import pattern_candidates
    except ImportError:
        return []
    matched = []
    for hit in pattern_candidates(patterns, prompt, min_score=min_score):
        pat = hit["pattern"]
        if str(pat.get("subject") or "") != str(subject):
            continue
        matched.append(hit)
    out = []
    for hit in matched[: max(0, quota)]:
        pat = hit["pattern"]
        rec = {
            "kind": "pattern",
            "subject": pat.get("subject"),
            "value": pat.get("value"),
            "provenance": pat.get("provenance"),
            "score": hit["score"],
            "weak": False,
            "injected": "pattern",
            "_extra_line": "- [\u89c4\u5f8b] %s %s: %s\uff08%d\u671f\u4f50\u8bc1\uff0c\u6743\u91cd\u00d70.8\uff09"
            % (
                pat.get("subject"),
                pat.get("predicate"),
                _trim(pat.get("value"), 90),
                pat.get("support") or 0,
            ),
            "_usage_id": "pattern:" + str(pat.get("id") or ""),
        }
        out.append(rec)
    return out


def recall(
    con: sqlite3.Connection,
    prompt: str,
    budget_chars: int = 1200,
    max_items: int = 6,
    min_score: float = 0.18,
    decay: bool = True,
    explain_decay: bool = False,
    preference_cards=None,
    pref_quota: int = 2,
    pref_subject: str | None = None,
    patterns=None,
    pattern_quota: int = 1,
    subject: str | None = None,
    usage_tracker=None,
) -> dict:
    """严格召回（F3：可选时效衰减）。

    decay=True 时，先按 rank（基础相关度 × 时效保持度）重排命中，
    再走去重/top-k/budget。衰减只影响排序，不删除任何条目、不改 status；
    identity/user-core/core 与 pinned 记录不参与衰减。
    explain_decay=True 时每个 item 附带 decay 诊断信息（调试用）。
    preference_cards: 可选的偏好卡列表（PreferenceCardStore.list_active() 的产出），
    走独立匹配通道注入，配额 pref_quota（默认 2），只占 max_items 剩余额度；
    pref_subject: 调用方显式给出的可信主体。request_subject() 里的上下文优先于它。
    两者都没有时，偏好卡和规律项通道失败关闭，返回零条主体范围外的注入，
    绝不把多个主体混进同一次召回。主体不能从 prompt 或卡片文本里推断。
    patterns: 可选的规律项列表（PatternStore.list("active") 的产出），
    得分已乘 0.8 权重（低于原始记录），配额 pattern_quota（默认 1），
    与偏好卡使用同一个可信主体；
    注入项明确标注 kind，不破坏 398 候选域语义；
    usage_tracker: 可选的 UsageTracker，注入项命中时记一次。
    """
    intent = detect_intent(prompt)
    # Subject isolation at the database level: when a subject is specified,
    # only records with matching subject are retrieved. This is fail-closed:
    # no subject means no filtering (backward compat), but callers that
    # require isolation must pass an explicit subject.
    hits = search(con, prompt, limit=max_items * 3, status=("active",), person=subject)

    if decay:
        # [(record, base, adjusted)] 按 adjusted 降序；min_score 仍按 base 过滤
        hits = [
            rec for rec, _base, _adj in rank_mod.rank([(r, r["score"]) for r in hits])
        ]

    # 去重：同一 claim 只留最高分那条；同值不同 id 也折叠。
    seen_claims, seen_values, picked = set(), set(), []
    for rec in hits:
        if rec["score"] < min_score:
            continue
        key = rec.get("claim_id")
        value_key = re.sub(r"\s+", "", (rec.get("value") or ""))[:40]
        if key in seen_claims or value_key in seen_values:
            continue
        seen_claims.add(key)
        seen_values.add(value_key)
        if explain_decay:
            rec["decay"] = rank_mod.explain(rec)
        picked.append(rec)
        if len(picked) >= max_items:
            break

    # 可选注入：偏好卡 / 规律项（带配额、有证据链）。独立匹配通道，
    # 只占 max_items 剩余额度。主体来自可信请求上下文，未知则失败关闭。
    trusted = _trusted_subject(pref_subject)
    prompt_grams = bigrams(prompt)
    slots_left = max(0, max_items - len(picked))
    extras = []
    if slots_left > 0 and trusted is not None:
        extras.extend(
            _match_pref_cards(
                prompt_grams,
                preference_cards,
                min(pref_quota, slots_left),
                min_score,
                subject=trusted,
            )
        )
        slots_left = max(0, max_items - len(picked) - len(extras))
    if slots_left > 0 and trusted is not None:
        extras.extend(
            _match_patterns(
                prompt,
                patterns,
                min(pattern_quota, slots_left),
                min_score,
                subject=trusted,
            )
        )
    picked = picked + extras

    lines, used = [], 0
    injected = []  # 实际进入注入文本的 extras（budget 裁剪后），用于使用计数
    for rec in picked:
        if "_extra_line" in rec:
            # 注入项（偏好卡/规律项）：行已预拼，有证据链，不参与 weak 判定。
            line = rec["_extra_line"]
            if len(line) + used + 1 > budget_chars:
                break
            lines.append(line)
            used += len(line) + 1
            if "_usage_id" in rec:
                injected.append(rec)
            continue
        # weak = 字面与语义都没有信号，纯粹按时间/重要性兜底出来的条目。
        # 上层据此决定"宁可说不知道"，不要拿它去误导。
        rec["weak"] = bool(
            rec.get("overlap", 0) <= 0
            and rec.get("semantic", 0) < getattr(semantic, "MIN_SIM", 0.45)
        )
        line = f"- [{KIND_LABEL.get(rec.get('kind'), rec.get('kind'))}] {_trim(rec.get('value'), 90)}"
        if rec.get("occurred_at"):
            line += f"（{rec['occurred_at'][:10]}）"
        if len(line) + used + 1 > budget_chars:
            break
        lines.append(line)
        used += len(line) + 1

    # 使用计数：只对实际进入注入文本的 extras 计数（budget 裁剪之后）。
    # 被 budget 截掉、没注入的卡不计——否则"被想起次数"虚高，污染质量信号。
    if usage_tracker is not None:
        for rec in injected:
            try:
                usage_tracker.record_hit(rec["_usage_id"], kind=rec.get("kind", ""))
            except Exception:
                pass

    return {
        "prompt": prompt,
        "intent": intent,
        "items": picked[: len(lines)],
        "chars": used,
        "budget": budget_chars,
        "dropped": max(0, len(picked) - len(lines)),
        "text": "\n".join(lines),
    }
