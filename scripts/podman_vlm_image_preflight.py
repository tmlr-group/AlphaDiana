#!/usr/bin/env python3
"""Preflight an OpenAI-compatible provider for tiny image chat support."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse


TINY_PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/"
    "l6U9JwAAAABJRU5ErkJggg=="
)


def _json(status: dict[str, Any]) -> None:
    print(json.dumps(status, ensure_ascii=False, sort_keys=True))


def _error_status(
    *,
    base_url: str,
    model: str,
    error_type: str,
    message: str,
    models_reachable: bool = False,
    chat_reachable: bool = False,
    response_non_empty: bool = False,
) -> dict[str, Any]:
    parsed = urlparse(base_url)
    return {
        "ok": False,
        "models_reachable": models_reachable,
        "chat_reachable": chat_reachable,
        "response_non_empty": response_non_empty,
        "model": model,
        "api_base_host": parsed.hostname or "",
        "api_base_scheme": parsed.scheme,
        "error_type": error_type,
        "error": message[:500],
    }


def _request_json(url: str, *, api_key: str, payload: dict[str, Any] | None = None, timeout: float = 30.0) -> Any:
    headers = {"Accept": "application/json"}
    data = None
    method = "GET"
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
        method = "POST"
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8"))


def _assistant_content(response: Any) -> str:
    if not isinstance(response, dict):
        return ""
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts).strip()
    return ""


def main() -> int:
    base_url = os.environ.get("OPENAI_BASE_URL", "").strip().rstrip("/")
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    model = os.environ.get("OPENAI_MODEL_NAME", "").strip()
    timeout = float(os.environ.get("PODMAN_MMMU_PREFLIGHT_REQUEST_TIMEOUT", "30"))

    missing = [name for name, value in {
        "OPENAI_BASE_URL": base_url,
        "OPENAI_API_KEY": api_key,
        "OPENAI_MODEL_NAME": model,
    }.items() if not value]
    if missing:
        _json({
            "ok": False,
            "models_reachable": False,
            "chat_reachable": False,
            "response_non_empty": False,
            "model": model,
            "api_base_host": urlparse(base_url).hostname or "",
            "api_base_scheme": urlparse(base_url).scheme,
            "error_type": "missing_env",
            "error": "Missing required environment variables: " + ", ".join(missing),
        })
        return 2

    models_url = f"{base_url}/models"
    chat_url = f"{base_url}/chat/completions"
    try:
        _request_json(models_url, api_key=api_key, timeout=timeout)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
        _json(_error_status(
            base_url=base_url,
            model=model,
            error_type=type(exc).__name__,
            message=str(exc),
        ))
        return 2

    payload = {
        "model": model,
        "max_tokens": 16,
        "temperature": 0,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Reply with the word ok if you can inspect this image."},
                    {"type": "image_url", "image_url": {"url": TINY_PNG_DATA_URL}},
                ],
            }
        ],
    }
    try:
        response = _request_json(chat_url, api_key=api_key, payload=payload, timeout=timeout)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
        _json(_error_status(
            base_url=base_url,
            model=model,
            error_type=type(exc).__name__,
            message=str(exc),
            models_reachable=True,
        ))
        return 2

    content = _assistant_content(response)
    parsed = urlparse(base_url)
    status = {
        "ok": bool(content),
        "models_reachable": True,
        "chat_reachable": True,
        "response_non_empty": bool(content),
        "model": model,
        "api_base_host": parsed.hostname or "",
        "api_base_scheme": parsed.scheme,
        "error_type": "" if content else "empty_response",
        "response_chars": len(content),
    }
    _json(status)
    return 0 if content else 2


if __name__ == "__main__":
    raise SystemExit(main())
