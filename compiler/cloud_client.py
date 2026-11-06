"""云端抽取通道（DeepSeek），带预算闸门与结构化脱敏。

设计约束（沿用项目既有纪律）
- 价格未知的模型直接拒绝调用，不猜。
- 超月度预算立刻停，不做"最后一次"。
- 发出去之前替换手机号/身份证/邮箱/真名，回来再还原。
- 语义隐私（病史、家庭、情感）无法脱敏，这一点必须由使用者知情。

预算门是劝阻、不是硬保证：调用前的 check() 用 token 粗估，
实际费用只有调用后才知道；账本损坏时失败关闭（直接报错，
绝不静默清零），并发记账不丢数。详见 Budget 类 docstring。
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

try:  # 包内与扁平导入都要能用（测试只把 compiler/ 加进 sys.path）
    from .file_lock import atomic_write_text_locked, cross_platform_lock
except ImportError:  # pragma: no cover - flat layout
    from file_lock import atomic_write_text_locked, cross_platform_lock  # type: ignore

# 价格（元 / 百万 token）。只登记确认过的模型，其余一律拒绝。
PRICES = {
    "deepseek-chat": {"in": 2.0, "out": 8.0},
    "deepseek-flash": {"in": 2.0, "out": 8.0},
}

DEFAULT_STATE = Path(r"./stellamem-data/budget.json")
# 凭证文件路径只来自显式参数或本环境变量，代码里不写死任何默认路径。
KEY_FILE_ENV = "STELLAMEM_DEEPSEEK_KEY_FILE"
ENV_NAME = "DEEPSEEK_API_KEY"


class BudgetExceeded(RuntimeError):
    pass


class UnknownModel(RuntimeError):
    pass


class CloudUnavailable(RuntimeError):
    pass


class BudgetLedgerError(ValueError):
    """预算账本损坏或不可读：失败关闭，绝不把已花金额清零重算。"""


# --------------------------------------------------------------------------
# 脱敏
# --------------------------------------------------------------------------

PATTERNS = (
    ("PHONE", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("ID", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")),
    ("CARD", re.compile(r"(?<!\d)\d{16,19}(?!\d)")),
)


def redact(text: str, names: tuple[str, ...] | list[str] | None = None) -> tuple[str, dict]:
    """替换结构化隐私，返回 (脱敏文本, 映射表)。

    真名只按调用方传入的 names 脱敏，默认不脱敏任何名字：
    名字清单是用户配置（config.cloud_redact_names），不是代码常量，
    代码里不写死任何真名。结构化模式（电话/身份证/邮箱/卡号）不受影响。
    """
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
    for name in names or ():
        if name and name in out:
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
    """月度预算闸门（云端抽取通道专用；默认关闭，本地抽取是生产路径）。

    能保证的：
    - 账本损坏（JSON 解析失败、结构不对、读不出来）时直接报错，
      绝不静默清零已花金额（失败关闭）。
    - check()/charge() 在账本锁内重读磁盘最新状态再决定/记账，
      多进程并发记账不会互相覆盖、不会丢计数；写回是原子替换。
    - charge() 记的是调用后才知道的实际费用：钱已经花了就必须记下来，
      哪怕当月因此轻微超支——丢账比超支更坏。

    保证不了的（诚实说明，不假装是硬保证）：
    - 调用前的 check() 用的是 token 粗估（estimate_tokens），不是实际费用；
      实际费用只有调用后才知道，估少了当月就可能超一点。
    - check() 与 charge() 是两个步骤，中间别的进程也可能记账；
      所以 check() 是“尽力而为的劝阻”，不是“绝不超过”的数学保证。
      想卡死预算，靠月度限额 + 事后账本对账，而不是只靠这一道门。
    """

    def __init__(self, path: Path = DEFAULT_STATE, monthly_cny: float = 50.0):
        self.path = Path(path)
        self.monthly = float(monthly_cny)
        self.state = self._load()

    def _load(self) -> dict:
        """读磁盘账本。文件不存在返回空账本；损坏则 raise，绝不返回空账本。"""
        if not self.path.exists():
            return {"months": {}, "total_cny": 0.0, "calls": 0}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise BudgetLedgerError(
                f"budget ledger is corrupt or unreadable, refusing to reset spent to 0: {self.path}"
            ) from exc
        if not isinstance(data, dict):
            raise BudgetLedgerError(f"budget ledger is not a JSON object: {self.path}")
        months = data.get("months", {})
        if not isinstance(months, dict) or not all(
            isinstance(month, str)
            and isinstance(amount, (int, float))
            and not isinstance(amount, bool)
            for month, amount in months.items()
        ):
            raise BudgetLedgerError(f"budget ledger has a malformed 'months' section: {self.path}")
        total = data.get("total_cny", 0.0)
        calls = data.get("calls", 0)
        if (
            not isinstance(total, (int, float))
            or isinstance(total, bool)
            or not isinstance(calls, int)
            or isinstance(calls, bool)
        ):
            raise BudgetLedgerError(f"budget ledger has malformed totals: {self.path}")
        return {"months": dict(months), "total_cny": float(total), "calls": int(calls)}

    def _save(self):
        """原子写回当前 state。

        调用方必须已持有 cross_platform_lock(self.path)：锁不能嵌套，
        这里不自己加锁；无锁调用仍可能被别的进程覆盖。
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text_locked(self.path, json.dumps(self.state, ensure_ascii=False, indent=1))

    @property
    def month(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m")

    def spent(self) -> float:
        # 每次都重读磁盘：别的进程记的账立刻可见；账本坏了直接报错（失败关闭）。
        # 读到的是原子替换后的完整文件，不会读到写一半的内容。
        self.state = self._load()
        return float(self.state.get("months", {}).get(self.month, 0.0))

    def remaining(self) -> float:
        return round(self.monthly - self.spent(), 4)

    def check(self, estimated_cny: float) -> None:
        """按最新账本 + 预估费用判断。预估只是粗估，见类 docstring。"""
        with cross_platform_lock(self.path):
            self.state = self._load()
            spent = float(self.state.get("months", {}).get(self.month, 0.0))
            if spent + estimated_cny > self.monthly:
                raise BudgetExceeded(
                    f"monthly budget would be exceeded: spent={spent:.3f} est={estimated_cny:.3f} "
                    f"limit={self.monthly:.1f}"
                )

    def charge(self, cost: float):
        """记一笔实际费用。锁内重读再写，别的进程的账不会被覆盖。"""
        with cross_platform_lock(self.path):
            self.state = self._load()
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


def _resolve_key_file(explicit=None) -> Path | None:
    raw = explicit if explicit is not None else os.environ.get(KEY_FILE_ENV, "")
    raw = str(raw).strip() if raw is not None else ""
    return Path(raw) if raw else None


def load_key(key_file=None) -> str:
    """凭证文件优先。文件存在时绝不回退到环境变量，避免旧 key 顶掉新 key。

    凭证文件路径只来自显式参数或 STELLAMEM_DEEPSEEK_KEY_FILE 环境变量，
    代码里不写死任何默认路径；什么都没配时只看 DEEPSEEK_API_KEY。
    """
    configured = _resolve_key_file(key_file)
    if configured is not None and configured.exists():
        key = configured.read_text(encoding="utf-8").strip()
        if key:
            if _fingerprint(key) in REVOKED_SHA256:
                raise CloudUnavailable("credential file contains a revoked key")
            return key
    key = os.environ.get(ENV_NAME, "").strip()
    if key:
        if _fingerprint(key) in REVOKED_SHA256:
            if os.environ.get("STELLAMEM_ALLOW_REVOKED") != "1":
                hint = (
                    f"write the new key to {configured} instead"
                    if configured is not None
                    else f"set {ENV_NAME} to a new key"
                )
                raise CloudUnavailable(f"{ENV_NAME} holds a revoked key; {hint}")
            print("[warn] using a revoked key because STELLAMEM_ALLOW_REVOKED=1", flush=True)
        return key
    if configured is not None:
        raise CloudUnavailable(f"no credential: write the key to {configured} or set {ENV_NAME}")
    raise CloudUnavailable(f"no credential: set {ENV_NAME} or configure {KEY_FILE_ENV}")


def make_cloud_llm(
    model: str = "deepseek-chat",
    monthly_cny: float = 50.0,
    state_path=None,
    key_file=None,
    redact_names: tuple[str, ...] | list[str] | None = None,
):
    """返回一个与 pipeline 兼容的 llm(system, payload) 函数。

    key_file：凭证文件路径（显式参数或 STELLAMEM_DEEPSEEK_KEY_FILE），
    不配则只看 DEEPSEEK_API_KEY；redact_names：要脱敏的真名清单，
    默认空（不写死任何真名）。
    """
    if model not in PRICES:
        raise UnknownModel(f"unknown price for model {model!r}; refusing to call")
    price = PRICES[model]
    budget = Budget(state_path or DEFAULT_STATE, monthly_cny)
    stats = {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost": 0.0, "redacted": 0}

    def llm(system: str, payload: str) -> str:
        key = load_key(key_file)
        safe_system, map_s = redact(system, names=redact_names)
        safe_payload, map_p = redact(payload, names=redact_names)
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
        names = config.get("cloud_redact_names") or ()
        if isinstance(names, str):
            names = (names,)
        names = tuple(n for n in names if isinstance(n, str) and n)
        return make_cloud_llm(
            model=config.get("cloud_model", "deepseek-chat"),
            monthly_cny=float(config.get("cloud_budget_cny", 50)),
            state_path=config.get("cloud_budget_state"),
            key_file=config.get("cloud_key_file"),
            redact_names=names,
        )
    raise ValueError(f"unknown extract_backend: {backend}")
