"""Configuration without import-time side effects or secret-bearing diagnostics."""

import json
import os
from pathlib import Path


def resolve_config_path():
    explicit = os.environ.get("STELLAMEM_CONFIG")
    if explicit:
        return Path(explicit).expanduser()
    repo = Path(__file__).resolve().parents[1]
    preferred = repo / "config.json"
    legacy = repo / "compiler/config.json"
    return preferred if preferred.exists() or not legacy.exists() else legacy


def load_config():
    try:
        config = json.loads(resolve_config_path().read_text(encoding="utf-8-sig"))
        if not isinstance(config, dict):
            raise ValueError()
        if not all(
            isinstance(config.get(k), str) and config[k] and "<" not in config[k]
            for k in ("memories_root", "server_url")
        ):
            raise ValueError()
        from model_client import endpoint

        endpoint(config["server_url"])
        limit = config.get("chunk_chars", 4000)
        if type(limit) is not int or not 128 <= limit <= 16000:
            raise ValueError()
        timeout = config.get("model_timeout", 90)
        if not isinstance(timeout, (int, float)) or not 1 <= timeout <= 300:
            raise ValueError()
        return config
    except (OSError, ValueError, TypeError, RuntimeError):
        raise ValueError(
            "Missing or invalid config; run setup.py check and review local paths"
        ) from None
