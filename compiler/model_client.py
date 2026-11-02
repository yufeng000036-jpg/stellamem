"""Local-only OpenAI-compatible HTTP client and strict complete-output parser."""

import ipaddress
import json
import re
import urllib.parse
import urllib.request


class ModelError(RuntimeError):
    pass


class ModelOutputTruncated(ModelError):
    """The model hit its output budget (finish_reason == "length").

    Distinct from other ModelError cases so the pipeline can shrink the chunk and
    retry instead of failing the whole source. A truncated response is never
    parsed: silently repairing it could drop claims past the cut.
    """

    pass


def endpoint(base, suffix="chat/completions"):
    parsed = urllib.parse.urlsplit(base)
    try:
        local = parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname).is_loopback
    except (ValueError, TypeError):
        local = False
    if not local or parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise ModelError("Only loopback model endpoints are enabled; remote egress is disabled")
    if parsed.query or parsed.fragment or parsed.path.rstrip("/") not in {"", "/v1"}:
        raise ModelError("Model URL must end at the server root or /v1")
    return base.rstrip("/").removesuffix("/v1") + "/v1/" + suffix


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ModelError("Model redirect refused")


def call_llm(system, user, max_tokens=None, temp=0.0, config=None):
    if config is None:
        from config_loader import load_config

        config = load_config()
    if max_tokens is None:
        max_tokens = config.get("model_max_tokens", 8192)
    url = endpoint(config["server_url"])
    body = {
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": temp,
        "max_tokens": max_tokens,
        "stream": False,
    }
    model = config.get("model_id")
    if model:
        body["model"] = model
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"}
    )
    # No inherited system proxy and no redirects: local data stays loopback.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(req, timeout=config.get("model_timeout", 90)) as response:
            payload = json.loads(response.read(8 * 1024 * 1024 + 1))
        choice = payload["choices"][0]
        reason = choice.get("finish_reason")
        if reason == "length":
            raise ModelOutputTruncated("Model output hit the token budget")
        if reason != "stop":
            raise ModelError("Incomplete model response")
        content = choice["message"]["content"]
        if not isinstance(content, str):
            raise ModelError("Invalid model content")
        return content
    except ModelError:
        raise
    except Exception as exc:
        # Do not echo URLs, prompts, response bodies or credentials.
        raise ModelError("Model request failed: " + type(exc).__name__) from None


def parse_json(text):
    if not isinstance(text, str):
        return None
    text = text.strip()
    if text.startswith("```"):
        match = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if not match:
            return None
        text = match[1]
    try:
        result = json.loads(
            text, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value))
        )
    except (ValueError, TypeError):
        return None
    return result if isinstance(result, list) else None
