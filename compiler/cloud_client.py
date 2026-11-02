"""云端抽取通道（DeepSeek），带预算闸门与结构化脱敏。

设计约束（沿用项目既有纪律）
- 价格未知的模型直接拒绝调用，不猜。
- 超月度预算立刻停，不做"最后一次"。
- 发出去之前替换手机号/身份证/邮箱/真名，回来再还原。
- 语义隐私（病史、家庭、情感）无法脱敏，这一点必须由使用者知情。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# 价格（元 / 百万 token）。只登记确认过的模型，其余一律拒绝。
PRICES = {
    "deepseek-chat": {"in": 2.0, "out": 8.0},
    "deepseek-flash": {"in": 2.0, "out": 8.0},
}

DEFAULT_STATE = Path(r"./stellamem-data/budget.json")
KEY_FILE = Path(r"E:/private/credentials/deepseek.key")
ENV_NAME = "DEEPSEEK_API_KEY"


class BudgetExceeded(RuntimeError):
    pass


class UnknownModel(RuntimeError):
    pass


class CloudUnavailable(RuntimeError):
    pass


# --------------------------------------------------------------------------
# 脱敏
# --------------------------------------------------------------------------

PATTERNS = (
    ("PHONE", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("ID", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")),
    ("CARD", re.compile(r"(?<!\d)\d{16,19}(?!\d)")),
)
NAMES = ("田新涛", "宇枫")


def redact(text: str) -> tuple[str, dict]:
    """替换结构化隐私，返回 (脱敏文本, 映射表)。"""
    mapping: dict[str, str] = {}
    out = text
    seq = 0
    for tag, pattern in PATTERNS:
        def repl(match):
            nonlocal seq
            seq += 1
            token = f"__{tag}{seq}__"
            mapping[token] = match.group(0)
            return token

        out = pattern.sub(repl, out)
    for name in NAMES:
        if name in out:
            seq += 1
            token = f"__NAME{seq}__"
            mapping[token] = name
            out = out.replace(name, token)
    return out, mapping


def restore(text: str, mapping: dict) -> str:
    for token, original in mapping.items():
        text = text.replace(token, original)
        # 模型偶尔会把 __ 去掉
        text = text.replace(token.strip("_"), original)
    return text


# --------------------------------------------------------------------------
# 预算
# --------------------------------------------------------------------------


class Budget:
    def __init__(self, path: Path = DEFAULT_STATE, monthly_cny: float = 50.0):
        self.path = Path(path)
        self.monthly = float(monthly_cny)
        self.state = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except ValueError:
                pass
        return {"months": {}, "total_cny": 0.0, "calls": 0}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")

    @property
    def month(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m")

    def spent(self) -> float:
        return float(self.state.get("months", {}).get(self.month, 0.0))

    def remaining(self) -> float:
        return round(self.monthly - self.spent(), 4)

    def check(self, estimated_cny: float) -> None:
        if self.spent() + estimated_cny > self.monthly:
            raise BudgetExceeded(
                f"monthly budget would be exceeded: spent={self.spent():.3f} est={estimated_cny:.3f} "
                f"limit={self.monthly:.1f}"
            )

    def charge(self, cost: float):
        months = self.state.setdefault("months", {})
        months[self.month] = round(months.get(self.month, 0.0) + cost, 6)
        self.state["total_cny"] = round(self.state.get("total_cny", 0.0) + cost, 6)
        self.state["calls"] = int(self.state.get("calls", 0)) + 1
        self._save()


def estimate_tokens(text: str) -> int:
    """粗估 token：中文约 1 字 1 token，英文约 4 字符 1 token。"""
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    other = len(text) - cjk
    return int(cjk * 1.0 + other / 4) + 32


# --------------------------------------------------------------------------
# 客户端
# --------------------------------------------------------------------------


# 已泄露的凭证哈希：命中即拒绝，防止环境变量里的旧 key 被继续使用。
REVOKED_SHA256 = {
    "4b82da1d6815dc343129d9ab623bff0a613636d193a3ad231736ace0534e3f01",  # P0 事故
    "58ce4ec6e66138edd376476fafb70814fcd279b7067fbe6c5cea1958beb79462",  # 2026-11-01 会话中贴出，视为泄露
}


def _fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def load_key() -> str:
    """凭证文件优先。文件存在时绝不回退到环境变量，避免旧 key 顶掉新 key。"""
    if KEY_FILE.exists():
        key = KEY_FILE.read_text(encoding="utf-8").strip()
        if key:
            if _fingerprint(key) in REVOKED_SHA256:
                raise CloudUnavailable("credential file contains a revoked key")
            return key
    key = os.environ.get(ENV_NAME, "").strip()
    if key:
        if _fingerprint(key) in REVOKED_SHA256:
            if os.environ.get("STELLAMEM_ALLOW_REVOKED") != "1":
                raise CloudUnavailable(
                    f"{ENV_NAME} holds a revoked key; write the new key to {KEY_FILE} instead"
                )
            print("[warn] using a revoked key because STELLAMEM_ALLOW_REVOKED=1", flush=True)
        return key
    raise CloudUnavailable(f"no credential: write the key to {KEY_FILE}")


def make_cloud_llm(model: str = "deepseek-chat", monthly_cny: float = 50.0, state_path=None):
    """返回一个与 pipeline 兼容的 llm(system, payload) 函数。"""
    if model not in PRICES:
        raise UnknownModel(f"unknown price for model {model!r}; refusing to call")
    price = PRICES[model]
    budget = Budget(state_path or DEFAULT_STATE, monthly_cny)
    stats = {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost": 0.0, "redacted": 0}

    def llm(system: str, payload: str) -> str:
        key = load_key()
        safe_system, map_s = redact(system)
        safe_payload, map_p = redact(payload)
        mapping = {**map_s, **map_p}
        stats["redacted"] += len(mapping)

        est_in = estimate_tokens(safe_system) + estimate_tokens(safe_payload)
        est_cost = (est_in * price["in"] + 800 * price["out"]) / 1_000_000
        budget.check(est_cost)

        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": safe_system},
                {"role": "user", "content": safe_payload},
            ],
            "temperature": 0,
            "max_tokens": 1000,
            "stream": False,
        }
        req = urllib.request.Request(
            "https://api.deepseek.com/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
        )
        last = None
        for attempt in range(2):
            try:
                with urllib.request.urlopen(req, timeout=180) as resp:
                    data = json.loads(resp.read())
                break
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    raise CloudUnavailable("credential rejected") from None
                if exc.code == 402:
                    raise BudgetExceeded("provider reports insufficient balance") from None
                last = exc
                time.sleep(1 + attempt * 2)
            except Exception as exc:  # 网络类
                last = exc
                time.sleep(1 + attempt * 2)
        else:
            raise CloudUnavailable(f"cloud request failed: {type(last).__name__}")

        usage = data.get("usage") or {}
        tin = usage.get("prompt_tokens") or est_in
        tout = usage.get("completion_tokens") or 0
        cost = (tin * price["in"] + tout * price["out"]) / 1_000_000
        budget.charge(cost)
        stats["calls"] += 1
        stats["tokens_in"] += tin
        stats["tokens_out"] += tout
        stats["cost"] = round(stats["cost"] + cost, 6)

        choice = data["choices"][0]
        if choice.get("finish_reason") == "length":
            from model_client import ModelOutputTruncated

            raise ModelOutputTruncated("cloud output hit token budget")
        return restore(choice["message"]["content"], mapping)

    llm.stats = stats
    llm.budget = budget
    return llm


def get_llm(config: dict):
    """按配置选择抽取后端：local / cloud。"""
    backend = (config.get("extract_backend") or "local").lower()
    if backend == "local":
        from model_client import call_llm

        def local(system, payload):
            return call_llm(
                system,
                payload,
                max_tokens=config.get("model_max_tokens", 1000),
                config=config,
            )

        return local
    if backend == "cloud":
        return make_cloud_llm(
            model=config.get("cloud_model", "deepseek-chat"),
            monthly_cny=float(config.get("cloud_budget_cny", 50)),
            state_path=config.get("cloud_budget_state"),
        )
    raise ValueError(f"unknown extract_backend: {backend}")
