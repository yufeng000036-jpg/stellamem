"""注入门：weak 结果默认不进上下文。

检索出来 ≠ 要注入。weak 条目的意思是"字面与语义都没命中，只是按时间兜底"，
把它塞进 prompt 等于用不相关的记忆误导回答。

判定全部落日志（JSONL）和审计（Markdown 追加），可回查"这次为什么没注入"。

改进（2026-11-02 改进轮 P0）：
1. 低分缺口：原来只拦 recall 标了 weak=True 的条目。recall 之外的调用方
   可能送来没标 weak、但同样没有信号的条目（字面重叠 0 且语义 < MIN_SIM，
   或综合分极低），现在同样按弱信号拦下。阈值见 LOW_SCORE / MIN_SIM，
   文档化在模块常量里，不藏在调用方。
2. 日志轮转：gate_log.jsonl 超过 1 MB 或 10,000 行时轮转，最多保留
   3 个旧文件，轮转在写入当前条目前完成，当前写入永不丢失。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

try:
    from .semantic import MIN_SIM
except Exception:  # pragma: no cover
    try:
        from semantic import MIN_SIM  # type: ignore
    except Exception:
        MIN_SIM = 0.45

WEAK_LABEL = "（弱信号，仅供参考，未确认）"
LOW_SCORE = 0.10
MAX_LOG_BYTES = 1_048_576
MAX_LOG_LINES = 10_000
MAX_ROTATED = 3


def _num(item: dict, key: str):
    """Read a numeric signal. Numeric strings are accepted; bool is not."""
    value = item.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str) and value.strip():
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def is_low_signal(item: dict) -> bool:
    """True when an item has no reliable signal, even if weak=False.

    Any one rule is enough:
    - `weak` is truthy (the original flag, still trusted);
    - overlap/semantic give no real signal. A missing field is treated as
      zero *only when the other field is present*: a caller sending
      overlap=0 without semantic, or semantic=0.2 without overlap, is
      sending a partial tuple and IS judged here — a zero literal overlap
      with an unknown semantic is exactly the "low signal that did not set
      weak" gap this gate exists to close (PATCH1, fixes the P0 boundary
      where a single-field item bypassed the rule);
    - score present and < LOW_SCORE (very low combined signal).
    Items carrying none of overlap/semantic/score are NOT judged here: the
    gate cannot invent a signal from nothing, and blocking every field-less
    item would break callers that legitimately pass only text.
    Numeric strings ("0.2") count as numbers; booleans do not.
    """
    if item.get("weak"):
        return True
    overlap = _num(item, "overlap")
    semantic = _num(item, "semantic")
    if overlap is not None or semantic is not None:
        literal = 0.0 if overlap is None else overlap
        meaning = 0.0 if semantic is None else semantic
        if literal <= 0 and meaning < MIN_SIM:
            return True
    score = _num(item, "score")
    if score is not None and score < LOW_SCORE:
        return True
    return False


class InjectionGate:
    def __init__(self, log_path=None, audit_path=None, allow_weak=False,
                 max_log_bytes: int = MAX_LOG_BYTES,
                 max_log_lines: int = MAX_LOG_LINES,
                 max_rotated: int = MAX_ROTATED):
        self.log_path = Path(log_path) if log_path else None
        self.audit_path = Path(audit_path) if audit_path else None
        self.allow_weak = allow_weak
        self.max_log_bytes = max_log_bytes
        self.max_log_lines = max_log_lines
        self.max_rotated = max_rotated

    def decide(self, bundle, query=None, allow_weak=None) -> dict:
        allow = self.allow_weak if allow_weak is None else allow_weak
        strong = [i for i in bundle.get("items", []) if not is_low_signal(i)]
        blocked = [i for i in bundle.get("items", []) if is_low_signal(i)]
        low_score_only = [i for i in blocked if not i.get("weak")]

        injected, blocked_out = list(strong), []
        if blocked:
            if allow:
                injected += [dict(w, text=w["text"] + WEAK_LABEL, low_confidence=True)
                             for w in blocked]
            else:
                blocked_out = list(blocked)

        if injected and not blocked_out:
            decision = "inject"
        elif injected and blocked_out:
            decision = "inject_partial"
        else:
            decision = "empty"

        entry = {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "query": query,
            "decision": decision,
            "injected": len(injected),
            "blocked_weak": len(blocked_out),
            "blocked_low_score_only": len(low_score_only) if not allow else 0,
            "blocked_low_score_total": len(low_score_only),
            "allow_weak": allow,
        }
        self._write(entry)

        return {
            "decision": decision,
            "inject": injected,
            "blocked": blocked_out,
            "text": "\n".join(i["text"] for i in injected),
            "note": ("没有可靠结果：命中的都是弱信号，已拦下，未注入上下文"
                     if decision == "empty" and blocked_out else ""),
            "injected": len(injected),
            "blocked_weak": len(blocked_out),
            "blocked_low_score": len(low_score_only),
            "allow_weak": allow,
        }

    def rotate_if_needed(self) -> bool:
        """Rotate log_path before the next write if it is too big/long.

        Order: .3 dropped, .2 -> .3, .1 -> .2, current -> .1.
        Returns True when rotation happened. The current write is never
        lost: rotation happens before appending, never instead of it.
        """
        if not self.log_path or not self.log_path.exists():
            return False
        try:
            size = self.log_path.stat().st_size
        except OSError:
            return False
        needs = size > self.max_log_bytes
        if not needs and self.max_log_lines:
            try:
                with self.log_path.open("r", encoding="utf-8", errors="replace") as fh:
                    lines = sum(1 for _ in fh)
                needs = lines >= self.max_log_lines
            except OSError:
                return False
        if not needs:
            return False
        base = str(self.log_path)
        try:
            oldest = Path(f"{base}.{self.max_rotated}")
            if oldest.exists():
                oldest.unlink()
            for i in range(self.max_rotated - 1, 0, -1):
                src = Path(f"{base}.{i}")
                if src.exists():
                    src.rename(f"{base}.{i + 1}")
            self.log_path.rename(f"{base}.1")
        except OSError:
            return False
        return True

    def _write(self, entry):
        if self.log_path:
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                self.rotate_if_needed()
                with self.log_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except OSError:
                pass
        if self.audit_path:
            try:
                self.audit_path.parent.mkdir(parents=True, exist_ok=True)
                line = (f"- {entry['at']}  gate  decision={entry['decision']}"
                        f"  injected={entry['injected']}  blocked_weak={entry['blocked_weak']}"
                        f"  allow_weak={entry['allow_weak']}"
                        f"  query={entry['query'] or ''}\n")
                if not self.audit_path.exists():
                    self.audit_path.write_text("# 状态变更审计\n\n", encoding="utf-8")
                with self.audit_path.open("a", encoding="utf-8") as fh:
                    fh.write(line)
            except OSError:
                pass

    def recent(self, n=10):
        if not self.log_path or not self.log_path.exists():
            return []
        lines = self.log_path.read_text(encoding="utf-8").strip().splitlines()[-n:]
        return [json.loads(x) for x in lines if x.strip()]
