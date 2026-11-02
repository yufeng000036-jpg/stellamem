"""旧密钥失效验证：只发一次最小请求，只输出分类结论。

安全约束：
- 密钥只存在于内存，不写入任何文件、不打印、不进日志
- max_tokens=1，把消耗压到最低
- 只发起 1 次请求，不做重试
- 输出只有 HTTP 状态码与错误类型，不含任何密钥片段
"""
from __future__ import annotations

import json
import re
import socket
import ssl
import urllib.error
import urllib.request
from pathlib import Path

MEM = Path(r"<USER_HOME>/.openclaw/workspace/MEMORY.md")
OUT = Path(r"D:/Stellamem/docs/p0/key-revocation-result.json")
URL = "https://api.deepseek.com/v1/chat/completions"


def main() -> int:
    text = MEM.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"sk-[A-Za-z0-9]{24,}", text)
    if not m:
        print(json.dumps({"result": "NO_KEY_FOUND"}))
        return 0
    key = m.group(0)

    payload = json.dumps({
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
    }).encode()
    req = urllib.request.Request(
        URL, data=payload,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
        method="POST",
    )

    result = {"attempts": 1, "endpoint": URL}
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read(400).decode("utf-8", "ignore")
            result.update({
                "http_status": r.status,
                "verdict": "STILL_VALID",
                "note": "旧密钥仍可用，未失效",
            })
    except urllib.error.HTTPError as e:
        body = e.read(400).decode("utf-8", "ignore")
        code = None
        try:
            j = json.loads(body)
            code = (j.get("error") or {}).get("code") or (j.get("error") or {}).get("type")
        except Exception:
            pass
        verdict = "UNKNOWN"
        if e.code in (401, 403):
            verdict = "REVOKED_OR_INVALID"
        elif e.code == 402:
            verdict = "INSUFFICIENT_BALANCE"
        elif e.code == 429:
            verdict = "RATE_LIMITED"
        elif e.code >= 500:
            verdict = "SERVICE_ERROR"
        result.update({"http_status": e.code, "error_code": code,
                       "verdict": verdict, "body_len": len(body)})
    except (urllib.error.URLError, socket.timeout, ssl.SSLError) as e:
        result.update({"http_status": None, "verdict": "NETWORK_ERROR",
                       "error_type": type(e).__name__})

    # 绝不在输出里出现 key：只保留长度与哈希前缀
    result["key_len"] = len(key)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
