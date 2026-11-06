"""调用方注入门（P1）：问题与记忆没有词面关联时不注入，budget=0。

规则直接借 OpenClaw 适配器（adapters/openclaw/index.js）的 lexical 门，
不要另写一套。JS 那边的规则是：

- 内容词：拉丁词 >= 3 字符、中文连续串 >= 2 字，去掉意图停用词
  （偏好/喜欢/项目/决定……——这些词只说明问题类型，不说明话题）。
  命中 = 任一记忆 claim 的内容词出现在问题里（claim 词 ⊆ 问题）。
- 意图：问题命中某类意图词（项目/偏好/教训/决定……）时，该类记忆可进。
- 技术问题里的偏好词不算数：问端口/版本/文件时，单凭“偏好”二字
  不能把偏好记忆拖进来。

Python 侧的落法（compiler/search.py 的检索是同一套词面信号）：

- lexical 探针直接复用 search() 的候选 1/2/3 构造（二元组倒排 MATCH、
  trigram 短语、精确子串 LIKE），只是 LIMIT 1。探针命中当且仅当
  search() 的词面候选非空——JS 的整词命中一定蕴含二元组命中
  （一个 >= 2 字的 claim 词含有的二元组同时在问题与记录里），
  所以整词规则被二元组探针包含，不用另实现一份。
- 泛问（detect_broad）与时间问句（最近/这几天）是 search() 本就有的
  非词面通道，门直接放行——它们问的就是类型/时间，不是话题。
- 意图（detect_intent）放行：与 JS 的意图通道同义。意图词本身不注入
  任何东西，只是让召回有机会跑；召回内部的打分仍会滤掉零重叠记录。

精确性（语义离线时）：门拦住 ⟹ recall() 一定返回空。理由：
探针覆盖了 search() 的全部三条词面候选（trigram 命中蕴含二元组命中，
因为 trigram 词 >= 2 字，其二元组必在两边）；偏好卡/规律项通道
要求 overlap >= min_score，也需要共享二元组；泛问与时间问句已豁免。
唯一例外是语义通道在线时的意译匹配——语义能跨过零词面差异，
所以调用方在语义在线时应传 semantic_available=True（此时门放行）。

误拦（见 docs/INJECTION_GATE.md）：语义在线时的意译问句（默认
semantic_available=False，按生产现状语义离线）；技术问题里只有
意图/泛问信号、没有任何词面命中时会被拦住（JS 原本的 carve-out，
故意的）；意图词本身不产生注入，放行只等于“让召回跑一趟”。
"""

from __future__ import annotations

import os
import re
import sqlite3

try:  # 包内导入（compiler.injection_gate）与扁平导入（sys.path 加 compiler/）都要能用
    from .search import _filter_sql, bigrams, detect_broad, detect_intent, fts_expr
except ImportError:  # pragma: no cover
    from search import _filter_sql, bigrams, detect_broad, detect_intent, fts_expr  # type: ignore

# 与 search() 里的 want_recent 同一条正则：时间问句走时间通道，不硬凑词面。
_WANT_RECENT_RE = re.compile(r"最近|这几天|最近在|近期")

# 与 JS 适配器同一条技术判定：端口形数字、版本形数字、文件名/扩展名、
# 或中文技术词。技术问题里只有意图/泛问信号时，门拦住（JS 的 carve-out）。
_TECHNICAL_RE = re.compile(
    r"(?:\b\d{3,5}\b|:\d+|\b[\w-]+\.(?:js|json|md|py|txt|yml|yaml|toml)\b|端口|版本|文件|路径)",
    re.IGNORECASE,
)

_GATE_ENV = "STELLAMEM_INJECTION_GATE"
_GATE_OFF = {"0", "false", "no", "off"}


def gate_enabled() -> bool:
    """门是否启用。默认启用；STELLAMEM_INJECTION_GATE=0/false/no/off 可关。"""
    return os.environ.get(_GATE_ENV, "").strip().lower() not in _GATE_OFF


def _lexical_probe(con, prompt: str, where: str, params) -> tuple[bool, str | None, list[str]]:
    """词面探针：复用 search() 的候选 1/2/3 构造，只取 1 条。

    返回 (命中与否, 命中记录 id, 共享二元组样本<=5)。
    探针本身失败（例如旧索引缺表）时按未命中处理，不抛异常——
    门绝不能因为自己查不到而把一次召回搞挂。
    """
    text = (prompt or "").strip()
    if not text:
        return False, None, []
    query_grams = bigrams(text)
    grams = sorted(g for g in query_grams if len(g) >= 2)
    sample_id = None
    if grams:
        expr_grams = " OR ".join(f'"{g}"' for g in grams)
        try:
            row = con.execute(
                "SELECT r.id, r.grams FROM records_grams "
                "JOIN records r ON r.rowid = records_grams.rowid "
                f"WHERE records_grams MATCH ? AND {where} LIMIT 1",
                [expr_grams] + list(params),
            ).fetchone()
        except sqlite3.OperationalError:
            row = None
        if row is not None:
            sample_id = row[0]
    if sample_id is None:
        expr = fts_expr(text)
        if expr:
            try:
                row = con.execute(
                    "SELECT r.id, r.grams FROM records_fts "
                    "JOIN records r ON r.rowid = records_fts.rowid "
                    f"WHERE records_fts MATCH ? AND {where} LIMIT 1",
                    [expr] + list(params),
                ).fetchone()
            except sqlite3.OperationalError:
                row = None
            if row is not None:
                sample_id = row[0]
    if sample_id is None:
        like = f"%{text}%"
        try:
            row = con.execute(
                "SELECT r.id, r.grams FROM records r "
                "WHERE (value LIKE ? OR evidence LIKE ? OR subject LIKE ?) "
                f"AND {where} LIMIT 1",
                [like, like, like] + list(params),
            ).fetchone()
        except sqlite3.OperationalError:
            row = None
        if row is not None:
            sample_id = row[0]
    if sample_id is None:
        return False, None, []
    matched = []
    try:
        row = con.execute("SELECT grams FROM records WHERE id = ?", (sample_id,)).fetchone()
        if row is not None:
            rec_grams = set(str(row[0] or "").split())
            matched = sorted(query_grams & rec_grams)[:5]
    except sqlite3.OperationalError:
        matched = []
    return True, sample_id, matched


def check(
    con,
    prompt: str,
    subject: str | None = None,
    persons: set[str] | None = None,
    status=("active",),
    semantic_available: bool = False,
) -> dict:
    """判定一次召回该不该注入。

    con: 已打开的索引连接（只读查询）。subject/persons/status 与
    search() 的过滤语义一致：只看调用方有权看的主体。
    semantic_available: 语义通道是否在线。在线时意译问句可能零词面
    命中也有语义命中，门放行；默认 False（生产现状：语义离线）。

    返回 dict：allow / reason / lexical / broad / intent / recent /
    technical / semantic_available / matched_grams / sample_id。
    reason 取值：lexical | broad | recent | intent | semantic_may_match |
    empty_prompt | technical_no_lexical | no_signal。
    """
    text = (prompt or "").strip()
    if not text:
        return {
            "allow": False,
            "reason": "empty_prompt",
            "lexical": False,
            "broad": None,
            "intent": None,
            "recent": False,
            "technical": False,
            "semantic_available": bool(semantic_available),
            "matched_grams": [],
            "sample_id": None,
        }
    broad = detect_broad(text)
    intent = detect_intent(text)
    recent = bool(_WANT_RECENT_RE.search(text))
    technical = bool(_TECHNICAL_RE.search(text))
    where, params = _filter_sql(None, None, status, subject, persons)
    lexical, sample_id, matched = _lexical_probe(con, text, where, params)
    if lexical:
        reason = "lexical"
        allow = True
    elif technical and (broad is not None or intent is not None):
        # JS carve-out：技术问题里只有意图/泛问信号、没有任何词面命中时，
        # 单凭“偏好/项目”二字不能把记忆拖进来。
        reason = "technical_no_lexical"
        allow = False
    elif broad is not None:
        reason = "broad"
        allow = True
    elif recent:
        reason = "recent"
        allow = True
    elif intent is not None:
        reason = "intent"
        allow = True
    elif semantic_available:
        reason = "semantic_may_match"
        allow = True
    else:
        reason = "no_signal"
        allow = False
    return {
        "allow": allow,
        "reason": reason,
        "lexical": lexical,
        "broad": broad,
        "intent": intent,
        "recent": recent,
        "technical": technical,
        "semantic_available": bool(semantic_available),
        "matched_grams": matched,
        "sample_id": sample_id,
    }


def gated_budget(
    con,
    prompt: str,
    default_budget: int = 1200,
    subject: str | None = None,
    persons: set[str] | None = None,
    status=("active",),
    semantic_available: bool = False,
) -> int:
    """门控预算：门拦住返回 0（零注入），放行返回默认预算。"""
    result = check(
        con,
        prompt,
        subject=subject,
        persons=persons,
        status=status,
        semantic_available=semantic_available,
    )
    return default_budget if result["allow"] else 0
