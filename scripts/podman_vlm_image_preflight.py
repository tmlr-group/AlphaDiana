#!/usr/bin/env python3
"""Preflight an OpenAI-compatible VLM from the Podman runtime context."""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


DEFAULT_IMAGE_URL = (
    "https://qianwen-res.oss-accelerate.aliyuncs.com/"
    "Qwen3.5/demo/RealWorld/RealWorld-04.png"
)
DEFAULT_MAX_TOKENS = 8192
DEFAULT_TOP_K = 20
MAX_ERROR_BODY_CHARS = 4000
MAX_IMAGE_BYTES = 8_000_000


@dataclass
class RequestError(Exception):
    error_type: str
    message: str
    http_status: int | None = None
    error_body: str = ""


def _json(status: dict[str, Any]) -> None:
    print(json.dumps(status, ensure_ascii=False, sort_keys=True))


def _bool_on(raw: str | None) -> bool:
    value = (raw or "1").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    raise ValueError("Phase 6 Podman MMMU-Pro preflight requires thinking mode to be enabled.")


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    value = int(raw)
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer.")
    return value


def _sanitize_text(text: str, *, secrets: tuple[str, ...]) -> str:
    sanitized = text
    for secret in secrets:
        if secret and secret != "EMPTY":
            sanitized = sanitized.replace(secret, "<redacted>")
    sanitized = re.sub(r"sk-[A-Za-z0-9._-]+", "<redacted>", sanitized)
    sanitized = re.sub(r"sk-or-v1-[A-Za-z0-9._-]+", "<redacted>", sanitized)
    sanitized = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer <redacted>", sanitized)
    return sanitized[:MAX_ERROR_BODY_CHARS]


def _read_error_body(exc: urllib.error.HTTPError, *, secrets: tuple[str, ...]) -> str:
    try:
        raw = exc.read()
    except Exception:
        return ""
    try:
        text = raw.decode("utf-8", errors="replace")
    except AttributeError:
        text = str(raw)
    return _sanitize_text(text, secrets=secrets)


def _response_status(response: Any) -> int:
    status = getattr(response, "status", None)
    if isinstance(status, int):
        return status
    code = response.getcode()
    return int(code) if code is not None else 0


def _request_json(
    url: str,
    *,
    api_key: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> tuple[Any, int]:
    secrets = (api_key,)
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
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = _response_status(response)
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise RequestError(
            "HTTPError",
            str(exc),
            http_status=int(exc.code),
            error_body=_read_error_body(exc, secrets=secrets),
        ) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RequestError(type(exc).__name__, str(exc)) from exc

    try:
        return json.loads(raw.decode("utf-8")), status
    except json.JSONDecodeError as exc:
        body = _sanitize_text(raw.decode("utf-8", errors="replace"), secrets=secrets)
        raise RequestError("JSONDecodeError", str(exc), http_status=status, error_body=body) from exc


def _download_image_data_url(url: str, *, timeout: float, api_key: str) -> tuple[str, dict[str, Any]]:
    request = urllib.request.Request(url, headers={"Accept": "image/*"}, method="GET")
    secrets = (api_key,)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = _response_status(response)
            content_type = response.headers.get_content_type() or "image/png"
            raw = response.read(MAX_IMAGE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise RequestError(
            "HTTPError",
            str(exc),
            http_status=int(exc.code),
            error_body=_read_error_body(exc, secrets=secrets),
        ) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RequestError(type(exc).__name__, str(exc)) from exc

    if len(raw) > MAX_IMAGE_BYTES:
        raise RequestError("ImageTooLarge", f"Image exceeds {MAX_IMAGE_BYTES} bytes.", http_status=status)
    import base64

    encoded = base64.b64encode(raw).decode("ascii")
    return (
        f"data:{content_type};base64,{encoded}",
        {
            "source_url": url,
            "http_status": status,
            "media_type": content_type,
            "bytes": len(raw),
        },
    )


def _assistant_text(message: dict[str, Any], key: str) -> str:
    value = message.get(key)
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def _response_summary(response: Any, *, http_status: int) -> dict[str, Any]:
    finish_reason = ""
    content_chars = 0
    reasoning_chars = 0
    if isinstance(response, dict):
        choices = response.get("choices")
        if isinstance(choices, list) and choices:
            choice = choices[0] if isinstance(choices[0], dict) else {}
            finish_reason = str(choice.get("finish_reason") or "")
            message = choice.get("message") if isinstance(choice, dict) else None
            if isinstance(message, dict):
                content_chars = len(_assistant_text(message, "content").strip())
                reasoning_chars = sum(
                    len(_assistant_text(message, key).strip())
                    for key in ("reasoning_content", "reasoning", "reasoning_text")
                )
    return {
        "ok": True,
        "http_status": http_status,
        "finish_reason": finish_reason,
        "content_chars": content_chars,
        "reasoning_chars": reasoning_chars,
        "response_non_empty": bool(content_chars or reasoning_chars),
    }


def _error_probe_status(exc: RequestError) -> dict[str, Any]:
    status: dict[str, Any] = {
        "ok": False,
        "http_status": exc.http_status,
        "finish_reason": "",
        "content_chars": 0,
        "reasoning_chars": 0,
        "response_non_empty": False,
        "error_type": exc.error_type,
        "error": exc.message[:500],
    }
    if exc.error_body:
        status["error_body"] = exc.error_body
    return status


def _chat_payload(
    *,
    model: str,
    image_url: str,
    max_tokens: int,
    top_k: int,
) -> dict[str, Any]:
    return {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": 0,
        "top_k": top_k,
        "chat_template_kwargs": {"enable_thinking": True},
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Inspect this image and answer with one short phrase.",
                    },
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        ],
    }


def _probe_chat(
    *,
    chat_url: str,
    api_key: str,
    model: str,
    image_url: str,
    max_tokens: int,
    top_k: int,
    timeout: float,
) -> dict[str, Any]:
    payload = _chat_payload(model=model, image_url=image_url, max_tokens=max_tokens, top_k=top_k)
    try:
        response, http_status = _request_json(chat_url, api_key=api_key, payload=payload, timeout=timeout)
    except RequestError as exc:
        return _error_probe_status(exc)
    return _response_summary(response, http_status=http_status)


def _missing_status(base_url: str, model: str, missing: list[str]) -> dict[str, Any]:
    parsed = urlparse(base_url)
    return {
        "ok": False,
        "base_url": base_url,
        "models_reachable": False,
        "chat_reachable": False,
        "response_non_empty": False,
        "model": model,
        "api_base_host": parsed.hostname or "",
        "api_base_scheme": parsed.scheme,
        "container_engine": os.environ.get("PODMAN_MMMU_CONTAINER_ENGINE", "unknown").strip() or "unknown",
        "network_mode": os.environ.get("PODMAN_MMMU_NETWORK_MODE", "").strip(),
        "thinking_mode": True,
        "max_tokens": DEFAULT_MAX_TOKENS,
        "error_type": "missing_env",
        "error": "Missing required environment variables: " + ", ".join(missing),
    }


def main() -> int:
    base_url = os.environ.get("OPENAI_BASE_URL", "").strip().rstrip("/")
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    model = os.environ.get("OPENAI_MODEL_NAME", "").strip()
    image_url = os.environ.get("PODMAN_MMMU_PRO_VLM_IMAGE_URL", DEFAULT_IMAGE_URL).strip()
    timeout = float(os.environ.get("PODMAN_MMMU_PREFLIGHT_REQUEST_TIMEOUT", "30"))
    container_engine = os.environ.get("PODMAN_MMMU_CONTAINER_ENGINE", "unknown").strip() or "unknown"
    network_mode = os.environ.get("PODMAN_MMMU_NETWORK_MODE", "").strip()

    try:
        thinking_mode = _bool_on(os.environ.get("PODMAN_MMMU_PRO_ENABLE_THINKING"))
        max_tokens = max(_int_env("PODMAN_MMMU_PRO_MAX_TOKENS", DEFAULT_MAX_TOKENS), DEFAULT_MAX_TOKENS)
        top_k = _int_env("PODMAN_MMMU_PRO_TOP_K", DEFAULT_TOP_K)
    except (TypeError, ValueError) as exc:
        _json({
            "ok": False,
            "base_url": base_url,
            "model": model,
            "container_engine": container_engine,
            "network_mode": network_mode,
            "thinking_mode": True,
            "max_tokens": DEFAULT_MAX_TOKENS,
            "error_type": type(exc).__name__,
            "error": str(exc),
        })
        return 2

    missing = [name for name, value in {
        "OPENAI_BASE_URL": base_url,
        "OPENAI_API_KEY": api_key,
        "OPENAI_MODEL_NAME": model,
    }.items() if not value]
    if missing:
        _json(_missing_status(base_url, model, missing))
        return 2

    parsed = urlparse(base_url)
    models_url = f"{base_url}/models"
    chat_url = f"{base_url}/chat/completions"
    started = time.time()
    models_status: dict[str, Any]
    try:
        _, models_http_status = _request_json(models_url, api_key=api_key, timeout=timeout)
        models_status = {"ok": True, "http_status": models_http_status}
    except RequestError as exc:
        models_status = _error_probe_status(exc)

    remote_status = _probe_chat(
        chat_url=chat_url,
        api_key=api_key,
        model=model,
        image_url=image_url,
        max_tokens=max_tokens,
        top_k=top_k,
        timeout=timeout,
    )
    remote_status["image_url"] = image_url

    try:
        data_url, data_source = _download_image_data_url(image_url, timeout=timeout, api_key=api_key)
        data_status = _probe_chat(
            chat_url=chat_url,
            api_key=api_key,
            model=model,
            image_url=data_url,
            max_tokens=max_tokens,
            top_k=top_k,
            timeout=timeout,
        )
        data_status["image_source"] = data_source
    except RequestError as exc:
        data_status = _error_probe_status(exc)
        data_status["image_source"] = {"source_url": image_url}

    podman_runtime_ok = container_engine == "podman"
    status = {
        "ok": bool(
            models_status.get("ok")
            and remote_status.get("ok")
            and data_status.get("ok")
            and podman_runtime_ok
            and thinking_mode
        ),
        "base_url": base_url,
        "models_reachable": bool(models_status.get("ok")),
        "chat_reachable": bool(remote_status.get("ok") and data_status.get("ok")),
        "response_non_empty": bool(
            remote_status.get("response_non_empty") or data_status.get("response_non_empty")
        ),
        "model": model,
        "api_base_host": parsed.hostname or "",
        "api_base_scheme": parsed.scheme,
        "container_engine": container_engine,
        "network_mode": network_mode,
        "podman_runtime_ok": podman_runtime_ok,
        "thinking_mode": True,
        "max_tokens": max_tokens,
        "top_k": top_k,
        "remote_image_url": remote_status,
        "data_url": data_status,
        "models": models_status,
        "elapsed_sec": round(time.time() - started, 3),
    }
    if not status["ok"] and container_engine != "podman":
        status["error_type"] = "not_podman_runtime"
        status["error"] = "Preflight must run from the Podman runtime context."
    _json(status)
    return 0 if status["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
