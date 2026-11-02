"""原文清洗：把 OpenClaw 会话里的元数据包装剥掉，只留人说的话。

为什么必须有这一步
------------------
飞书转发的消息长这样：

    Conversation info (untrusted metadata):
    ```json
    {"message_id": "om_...", "is_group_chat": true, "timestamp": "..."}
    ```
    Sender (untrusted metadata): ```json {"label": "用户787547"}```
    Chat history since last reply (untrusted): ```json [...]```

    [message_id: om_x100b55e9f5aef130b34a70e13864816]
    用户787547: 你好

直接喂给模型，它会从 JSON 字段里"提炼"出用户从没说过的话（实测 39.9% 的
active 记忆是这么来的）。所以必须在抽取前剥到只剩最后一行。
"""

from __future__ import annotations

import re

# 时间前缀：[Sun 2026-03-08 22:39 GMT+8]
_TIME_PREFIX = re.compile(r"^\[\w{3}\s+\d{4}-\d{2}-\d{2}\s+[\d:]+\s+GMT[+-]\d+\]\s*")
# 发送者前缀：用户787547: / 宇枫：/ ou_xxx:
_SENDER_PREFIX = re.compile(r"^[^\s:：]{1,24}\s*[:：]\s*")
# 系统/框架消息，不是用户说的
_SYSTEM_HINTS = (
    "A new session was started",
    "Session Startup",
    "Conversation info (untrusted metadata)",
    "System:",
    "[System",
)

MIN_LEN = 6


def clean_message(text: str) -> str:
    """返回用户真正说出的那句话；无法判断时返回空串。"""
    raw = (text or "").strip()
    if not raw:
        return ""

    # 1) 带包装头的飞书/群聊消息：真实内容在最后一个 [message_id: ...] 之后
    if "untrusted metadata" in raw[:120] or raw.startswith("[message_id"):
        idx = raw.rfind("[message_id:")
        if idx < 0:
            return ""
        body = raw[idx:].split("]", 1)[-1].strip()
        raw = body

    # 2) 剥时间前缀与发送者前缀（可能叠加）
    for _ in range(3):
        before = raw
        raw = _TIME_PREFIX.sub("", raw)
        raw = _SENDER_PREFIX.sub("", raw, count=1)
        raw = raw.strip()
        if raw == before:
            break

    # 3) 系统消息丢弃
    if any(raw.startswith(hint) for hint in _SYSTEM_HINTS):
        return ""

    # 4) 纯结构化内容（只剩 JSON/括号）丢弃
    if raw.startswith(("{", "[", "```")) and raw.count("\n") > 2:
        return ""

    return raw.strip()


def is_usable(text: str, min_len: int = MIN_LEN) -> bool:
    cleaned = clean_message(text)
    return len(cleaned) >= min_len


def clean_messages(messages, min_len: int = MIN_LEN):
    """批量清洗，丢弃清洗后不可用的消息；保留原始 provenance。"""
    out = []
    for message in messages:
        cleaned = clean_message(message.get("text") or "")
        if len(cleaned) < min_len:
            continue
        item = dict(message)
        item["text"] = cleaned
        item["raw_chars"] = len(message.get("text") or "")
        out.append(item)
    return out


def stats(messages) -> dict:
    total = len(messages)
    cleaned = [clean_message(m.get("text") or "") for m in messages]
    usable = [c for c in cleaned if len(c) >= MIN_LEN]
    return {
        "total": total,
        "packaged": sum(
            1 for m in messages if "untrusted metadata" in (m.get("text") or "")[:120]
        ),
        "usable": len(usable),
        "usable_ratio": round(len(usable) / total, 3) if total else 0,
        "avg_len": round(sum(len(c) for c in usable) / len(usable), 1) if usable else 0,
    }
