#!/usr/bin/env python3
"""Minimal OpenAI-compatible bridge for ZeroClaw CLI."""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import shlex
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


logger = logging.getLogger(__name__)


def _json_object_from_env(name: str) -> dict[str, Any]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Ignoring invalid JSON in %s", name)
        return {}
    if not isinstance(parsed, dict):
        logger.warning("Ignoring non-object JSON in %s", name)
        return {}
    return parsed

HOST = os.environ.get("ZEROCLAW_BRIDGE_HOST", "127.0.0.1")
PORT = 8080
GATEWAY_TOKEN = os.environ.get("ZEROCLAW_GATEWAY_TOKEN", "ZEROCLAW")
REQUEST_TIMEOUT = int(os.environ.get("ZEROCLAW_REQUEST_TIMEOUT", "1200"))
MAX_TOOL_ITERATIONS = int(os.environ.get("ZEROCLAW_MAX_TOOL_ITERATIONS", "100"))
MAX_ACTIONS_PER_HOUR = int(os.environ.get("ZEROCLAW_MAX_ACTIONS_PER_HOUR", "200"))
WORKSPACE_ONLY = os.environ.get("ZEROCLAW_WORKSPACE_ONLY", "false").strip().lower() == "true"
MODEL_NAME = os.environ.get("OPENAI_MODEL_NAME", "zeroclaw")
API_BASE = os.environ.get("OPENAI_BASE_URL", "")
API_KEY = os.environ.get("OPENAI_API_KEY", "")
DEFAULT_TEMPERATURE = float(os.environ.get("ZEROCLAW_TEMPERATURE", "0.0"))
PROVIDER_TOP_P_RAW = os.environ.get("ZEROCLAW_TOP_P", "").strip()
PROVIDER_TOP_P = float(PROVIDER_TOP_P_RAW) if PROVIDER_TOP_P_RAW else None
PROVIDER_REQUEST_OVERRIDES = _json_object_from_env("ZEROCLAW_PROVIDER_REQUEST_OVERRIDES")
PROVIDER_TIMEOUT_SECS = int(os.environ.get("ZEROCLAW_PROVIDER_TIMEOUT_SECS", "120"))
PROVIDER_MAX_TOKENS_RAW = os.environ.get("ZEROCLAW_PROVIDER_MAX_TOKENS", "").strip()
PROVIDER_MAX_TOKENS = int(PROVIDER_MAX_TOKENS_RAW) if PROVIDER_MAX_TOKENS_RAW else None
DISABLE_TOOLS_RAW = os.environ.get("ZEROCLAW_DISABLE_TOOLS", "").strip().lower()
DISABLE_TOOLS = DISABLE_TOOLS_RAW in {"1", "true", "yes", "on"}
REASONING_ENABLED_RAW = os.environ.get("ZEROCLAW_REASONING_ENABLED", "").strip().lower()
if REASONING_ENABLED_RAW in {"1", "true", "yes", "on"}:
    REASONING_ENABLED = True
elif REASONING_ENABLED_RAW in {"0", "false", "no", "off"}:
    REASONING_ENABLED = False
else:
    REASONING_ENABLED = None
REASONING_EFFORT = os.environ.get("ZEROCLAW_REASONING_EFFORT", "").strip() or None
ARTIFACT_ROOT = Path(
    os.environ.get("ZEROCLAW_ARTIFACT_ROOT", "/tmp/zeroclaw-bridge-artifacts")
).expanduser()


_bridge_logger = logging.getLogger("zeroclaw_bridge.vision_proxy")

if HOST not in {"127.0.0.1", "localhost", "::1"}:
    logger.warning(
        "ZeroClaw bridge bound to non-loopback address %r; ensure this is intentional because it exposes agent credentials and the OpenAI-compatible endpoint.",
        HOST,
    )


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _attachments_have_image(attachments: list[dict[str, Any]]) -> bool:
    for item in attachments:
        mime = str(item.get("mime", "") or "").lower()
        if mime.startswith("image/") and item.get("data"):
            return True
    return False


def _vision_inject_messages(
    messages: list[dict[str, Any]],
    image_items: list[dict[str, Any]],
    target_text: str,
) -> tuple[list[dict[str, Any]], bool]:
    """Inject image_url content blocks into the first matching user message."""
    target_norm = (target_text or "").strip()
    new_messages: list[dict[str, Any]] = []
    injected = False
    for msg in messages:
        if injected or msg.get("role") != "user":
            new_messages.append(msg)
            continue
        content = msg.get("content")
        if isinstance(content, list):
            text_parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
            text = "\n".join(text_parts)
        else:
            text = str(content or "")
        if target_norm and target_norm not in text:
            new_messages.append(msg)
            continue
        parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
        for item in image_items:
            data = item.get("data")
            mime = str(item.get("mime") or "image/png")
            if not data:
                continue
            b64 = base64.b64encode(data).decode("ascii")
            parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            })
        new_msg = dict(msg)
        new_msg["content"] = parts
        new_messages.append(new_msg)
        injected = True
    return new_messages, injected


class _BridgeVisionProxyState:
    def __init__(self, upstream_base: str, upstream_api_key: str,
                 image_items: list[dict[str, Any]], target_text: str,
                 upstream_model: str | None,
                 request_overrides: dict[str, Any] | None = None) -> None:
        self.upstream_base = upstream_base.rstrip("/")
        self.upstream_api_key = upstream_api_key
        self.image_items = image_items
        self.target_text = target_text
        self.upstream_model = upstream_model
        self.request_overrides = dict(request_overrides or {})
        self.request_count = 0
        self.injection_count = 0


def _make_vision_proxy_handler(state: _BridgeVisionProxyState):

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            _bridge_logger.debug("[vision-proxy] " + fmt, *args)

        def _forward(self, body: bytes) -> None:
            forward_path = self.path
            if forward_path.startswith("/v1") and state.upstream_base.endswith("/v1"):
                forward_path = forward_path[len("/v1"):] or "/"
            url = f"{state.upstream_base}{forward_path}"
            headers = {
                k: v for k, v in self.headers.items()
                if k.lower() not in {"host", "content-length", "authorization"}
            }
            if state.upstream_api_key:
                headers["Authorization"] = f"Bearer {state.upstream_api_key}"
            req = Request(url, data=body, headers=headers, method=self.command)
            try:
                with urlopen(req, timeout=600) as resp:
                    payload = resp.read()
                    self.send_response(resp.status)
                    for hk, hv in resp.headers.items():
                        if hk.lower() in {"transfer-encoding", "connection", "content-length"}:
                            continue
                        self.send_header(hk, hv)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
            except Exception as exc:
                err = json.dumps({"error": {"message": str(exc), "type": "proxy_error"}}).encode()
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err)))
                self.end_headers()
                self.wfile.write(err)

        def do_GET(self) -> None:  # noqa: N802
            self._forward(b"")

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(length) if length > 0 else b""
            state.request_count += 1
            is_chat = self.path.endswith("/chat/completions")
            has_images = bool(state.image_items)
            if is_chat and (has_images or state.upstream_model or state.request_overrides):
                try:
                    payload = json.loads(body.decode("utf-8"))
                    if state.upstream_model:
                        payload["model"] = state.upstream_model
                    payload.update(state.request_overrides)
                    messages = payload.get("messages")
                    if has_images and isinstance(messages, list):
                        new_messages, injected = _vision_inject_messages(
                            messages, state.image_items, state.target_text,
                        )
                        if injected:
                            payload["messages"] = new_messages
                            state.injection_count += 1
                            print(
                                f"[ZeroClawBridge] [vision-proxy] injected {len(state.image_items)} "
                                f"images into request #{state.request_count}",
                                flush=True,
                            )
                    body = json.dumps(payload).encode("utf-8")
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
            self._forward(body)

    return Handler


class _BridgeVisionProxy:
    """In-bridge HTTP proxy that injects images into chat completions requests."""

    def __init__(self, upstream_base: str, upstream_api_key: str,
                 image_items: list[dict[str, Any]], target_text: str,
                 upstream_model: str | None = None,
                 request_overrides: dict[str, Any] | None = None) -> None:
        self._state = _BridgeVisionProxyState(
            upstream_base,
            upstream_api_key,
            image_items,
            target_text,
            upstream_model,
            request_overrides,
        )
        self._port: int | None = None
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "_BridgeVisionProxy":
        port = _find_free_port()
        self._server = HTTPServer(("127.0.0.1", port), _make_vision_proxy_handler(self._state))
        self._port = port
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name=f"zeroclaw-bridge-vproxy-{port}",
            daemon=True,
        )
        self._thread.start()
        print(
            f"[ZeroClawBridge] [vision-proxy] listening on {self.url} upstream={self._state.upstream_base}",
            flush=True,
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        print(
            f"[ZeroClawBridge] [vision-proxy] shutdown: "
            f"{self._state.request_count} requests, {self._state.injection_count} injections",
            flush=True,
        )

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._port}/v1"

    @property
    def injection_count(self) -> int:
        return self._state.injection_count


def _normalize_api_base(api_base: str) -> str:
    return api_base.strip().rstrip("/")


def _resolve_provider(provider: str, api_base: str) -> str:
    normalized_provider = provider.strip().lower()
    normalized_api_base = _normalize_api_base(api_base)
    if normalized_provider.startswith("custom:"):
        return normalized_provider
    if normalized_provider == "openrouter" or "openrouter" in normalized_api_base.lower():
        return "openrouter"
    if normalized_api_base and normalized_api_base not in {
        "https://api.openai.com",
        "https://api.openai.com/v1",
    }:
        return f"custom:{normalized_api_base}"
    return normalized_provider or "openai"


PROVIDER = _resolve_provider(os.environ.get("ZEROCLAW_PROVIDER", ""), API_BASE)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
LOG_LINE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T[0-9:.]+Z\s+(INFO|WARN|ERROR)\b")
_RUNTIME_LOG_PREFIXES = (
    "[ZeroClawBridge]",
    "OpenRouter transport error while reading response body",
    "OpenRouter transport error",
    "transport error while reading response body",
    "provider retry exhausted",
    "retrying request in ",
)
_RUNTIME_LOG_SUBSTRINGS = (
    " zeroclaw::",
    "openrouter transport error while reading response body",
    "provider retry exhausted",
    "retrying request in ",
    "retry attempt ",
)


def _is_runtime_log_line(line: str) -> bool:
    if not line:
        return False
    if LOG_LINE_RE.match(line):
        return True
    lowered = line.lower()
    if any(line.startswith(prefix) for prefix in _RUNTIME_LOG_PREFIXES):
        return True
    if any(fragment in lowered for fragment in _RUNTIME_LOG_SUBSTRINGS):
        return True
    return False


def _extract_runtime_failure(raw_output: str, raw_stderr: str) -> str:
    candidates = []
    for text in (raw_stderr, raw_output):
        for raw_line in text.splitlines():
            line = ANSI_RE.sub("", raw_line).strip()
            if not line:
                continue
            lowered = line.lower()
            if "transport error while reading response body" in lowered:
                candidates.append(line)
            elif "provider retry exhausted" in lowered:
                candidates.append(line)
            elif "retrying request in " in lowered:
                candidates.append(line)
    if not candidates:
        return ""
    return candidates[-1]


def _quote_toml(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _build_config_toml(
    temperature: float | None = None,
    provider_base_url_override: str | None = None,
) -> str:
    workspace_only = "true" if WORKSPACE_ONLY else "false"
    effective_temperature = DEFAULT_TEMPERATURE if temperature is None else float(temperature)
    provider_max_tokens_line = (
        f"provider_max_tokens = {PROVIDER_MAX_TOKENS}\n"
        if PROVIDER_MAX_TOKENS is not None
        else ""
    )
    runtime_section = ""
    if REASONING_ENABLED is not None or REASONING_EFFORT is not None:
        runtime_lines = ["[runtime]"]
        if REASONING_ENABLED is not None:
            runtime_lines.append(f"reasoning_enabled = {str(REASONING_ENABLED).lower()}")
        if REASONING_EFFORT is not None:
            runtime_lines.append(f"reasoning_effort = {_quote_toml(REASONING_EFFORT)}")
        runtime_section = "\n".join(runtime_lines) + "\n\n"
    provider_section = "[model_providers]\n\n"
    if provider_base_url_override:
        provider_section = (
            f"[model_providers.{PROVIDER}]\n"
            f"name = {_quote_toml(PROVIDER)}\n"
            f"base_url = {_quote_toml(provider_base_url_override)}\n\n"
        )
    return (
        f"default_provider = {_quote_toml(PROVIDER)}\n"
        f"default_model = {_quote_toml(MODEL_NAME)}\n\n"
        f"default_temperature = {effective_temperature}\n"
        f"provider_timeout_secs = {PROVIDER_TIMEOUT_SECS}\n"
        f"{provider_max_tokens_line}"
        "model_routes = []\n"
        "embedding_routes = []\n\n"
        f"{provider_section}"
        f"{runtime_section}"
        "[provider]\n\n"
        "[observability]\n"
        'backend = "none"\n'
        'runtime_trace_mode = "none"\n'
        'runtime_trace_path = "state/runtime-trace.jsonl"\n'
        "runtime_trace_max_entries = 200\n\n"
        "[autonomy]\n"
        'level = "full"\n'
        f"workspace_only = {workspace_only}\n"
        'allowed_commands = ["git", "npm", "cargo", "ls", "cat", "grep", "find", "echo", "pwd", "wc", "head", "tail", "date", "python", "python3", "bash", "sh", "sed", "awk", "mkdir", "mv", "cp", "rm"]\n'
        'forbidden_paths = ["/etc", "/usr", "/bin", "/sbin", "/lib", "/opt", "/boot", "/dev", "/proc", "/sys"]\n'
        f"max_actions_per_hour = {MAX_ACTIONS_PER_HOUR}\n\n"
        "max_cost_per_day_cents = 10000\n\n"
        "[agent]\n"
        f"max_tool_iterations = {MAX_TOOL_ITERATIONS}\n"
    )


def _coerce_text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    if isinstance(content, dict):
        text = content.get("text") or content.get("content")
        if isinstance(text, str):
            return text
    return ""


def _build_prompt(messages: list[dict[str, Any]]) -> str:
    system_parts: list[str] = []
    user_parts: list[str] = []
    for message in messages:
        role = str(message.get("role", ""))
        content = _coerce_text_content(message.get("content", ""))
        if not content:
            continue
        if role == "system":
            system_parts.append(content)
        elif role == "user":
            user_parts.append(content)
    if system_parts and user_parts:
        return f"{chr(10).join(system_parts)}\n\n{chr(10).join(user_parts)}"
    if user_parts:
        return "\n\n".join(user_parts)
    return "\n\n".join(system_parts)


def _extract_chat_completion_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {})
        if isinstance(message, dict):
            content = _coerce_text_content(message.get("content", ""))
            if content.strip():
                return content.strip()
        text = choices[0].get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()
    return ""


def _sanitize_output(raw_output: str) -> tuple[str, list[str]]:
    cleaned_lines: list[str] = []
    dropped_runtime_logs: list[str] = []
    for raw_line in raw_output.splitlines():
        line = ANSI_RE.sub("", raw_line).rstrip()
        if _is_runtime_log_line(line):
            dropped_runtime_logs.append(line)
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip(), dropped_runtime_logs


def _coerce_temperature(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _normalize_attachments(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        return []
    items: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        encoded = str(item.get("data_base64", "") or "").strip()
        rel_path = str(item.get("path", "") or "").strip()
        if not encoded or not rel_path:
            continue
        items.append({
            "key": str(item.get("key", "") or "").strip(),
            "filename": str(item.get("filename", "") or "").strip(),
            "path": rel_path,
            "mime": str(item.get("mime", "") or "").strip(),
            "data": base64.b64decode(encoded),
        })
    return items


def _safe_attachment_path(workspace_dir: Path, rel_path: str) -> Path:
    path = Path(rel_path)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("invalid attachment path")
    base = workspace_dir.resolve()
    target = (base / path).resolve()
    if not target.is_relative_to(base):
        raise ValueError("invalid attachment path")
    return target


def _write_attachments(workspace_dir: Path, attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    for item in attachments:
        rel_path = str(item["path"])
        dest = _safe_attachment_path(workspace_dir, rel_path)
        manifest_path = dest.relative_to(workspace_dir.resolve()).as_posix()
        dest.parent.mkdir(parents=True, exist_ok=True)
        data = bytes(item["data"])
        dest.write_bytes(data)
        manifest.append({
            "key": item.get("key", ""),
            "path": manifest_path,
            "filename": item.get("filename", "") or Path(manifest_path).name,
            "mime": item.get("mime", ""),
            "size_bytes": len(data),
        })
    return manifest


def _build_provider_messages(
    messages: list[dict[str, Any]],
    attachments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized_messages = [dict(message) for message in messages]
    if not _attachments_have_image(attachments):
        return normalized_messages
    updated_messages, _ = _vision_inject_messages(normalized_messages, attachments, "")
    return updated_messages


def _provider_request_overrides() -> dict[str, Any]:
    overrides = dict(PROVIDER_REQUEST_OVERRIDES)
    if PROVIDER_TOP_P is not None:
        overrides["top_p"] = PROVIDER_TOP_P
    if PROVIDER_MAX_TOKENS is not None:
        overrides["max_tokens"] = PROVIDER_MAX_TOKENS
    return overrides


def _run_provider_chat_without_tools(
    messages: list[dict[str, Any]],
    *,
    temperature: float | None = None,
    attachments: list[dict[str, Any]] | None = None,
) -> str:
    execution_id = uuid.uuid4().hex
    attachments_list = list(attachments or [])
    effective_messages = _build_provider_messages(messages, attachments_list)
    payload: dict[str, Any] = {
        "model": MODEL_NAME,
        "messages": effective_messages,
        "temperature": DEFAULT_TEMPERATURE if temperature is None else float(temperature),
        "stream": False,
    }
    payload.update(_provider_request_overrides())
    request_body = json.dumps(payload).encode("utf-8")
    request = Request(
        f"{API_BASE.rstrip('/')}/chat/completions",
        data=request_body,
        headers={
            "Content-Type": "application/json",
            **(
                {"Authorization": f"Bearer {API_KEY}"}
                if API_KEY
                else {}
            ),
        },
        method="POST",
    )
    status_payload = {
        "created_at": int(time.time()),
        "model": MODEL_NAME,
        "provider": PROVIDER,
        "api_base": API_BASE,
        "temperature": DEFAULT_TEMPERATURE if temperature is None else float(temperature),
        "top_p": PROVIDER_TOP_P,
        "max_tokens": PROVIDER_MAX_TOKENS,
        "provider_request_overrides": PROVIDER_REQUEST_OVERRIDES,
        "disable_tools": True,
        "error_message": "",
    }
    response_json: dict[str, Any]
    raw_output = ""
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            response_json = json.loads(response.read().decode("utf-8"))
            status_payload["http_status"] = getattr(response, "status", 200)
    except Exception as exc:
        response_json = {"error": {"message": str(exc), "type": exc.__class__.__name__}}
        status_payload["error_message"] = str(exc)
        _persist_last_request(
            execution_id=execution_id,
            prompt=_build_prompt(messages),
            attachment_manifest=[
                {
                    "key": item.get("key", ""),
                    "path": item.get("path", ""),
                    "filename": item.get("filename", ""),
                    "mime": item.get("mime", ""),
                    "size_bytes": len(bytes(item.get("data", b""))),
                }
                for item in attachments_list
            ],
            stdout_text="",
            stderr_text=str(exc),
            runtime_trace_text="",
            status_payload={
                **status_payload,
                "request_payload": payload,
                "response_payload": response_json,
            },
        )
        raise RuntimeError(str(exc)) from exc

    raw_output = _extract_chat_completion_text(response_json)
    if not raw_output:
        status_payload["error_message"] = (
            f"ZeroClaw no-tools provider call returned empty content: {response_json!r}"
        )
    _persist_last_request(
        execution_id=execution_id,
        prompt=_build_prompt(messages),
        attachment_manifest=[
            {
                "key": item.get("key", ""),
                "path": item.get("path", ""),
                "filename": item.get("filename", ""),
                "mime": item.get("mime", ""),
                "size_bytes": len(bytes(item.get("data", b""))),
            }
            for item in attachments_list
        ],
        stdout_text=raw_output,
        stderr_text="",
        runtime_trace_text="",
        status_payload={
            **status_payload,
            "request_payload": payload,
            "response_payload": response_json,
        },
    )
    if not raw_output:
        raise RuntimeError(status_payload["error_message"])
    return raw_output


def _persist_last_request(
    *,
    execution_id: str,
    prompt: str,
    attachment_manifest: list[dict[str, Any]],
    stdout_text: str,
    stderr_text: str,
    runtime_trace_text: str,
    status_payload: dict[str, Any],
) -> None:
    last_request_dir = ARTIFACT_ROOT / "last_request"
    last_request_dir.parent.mkdir(parents=True, exist_ok=True)
    if last_request_dir.exists():
        shutil.rmtree(last_request_dir)
    last_request_dir.mkdir(parents=True, exist_ok=True)
    (last_request_dir / "task.txt").write_text(prompt, encoding="utf-8")
    (last_request_dir / "attachment_manifest.json").write_text(
        json.dumps({"attachments": attachment_manifest}, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (last_request_dir / "status.json").write_text(
        json.dumps(
            {
                "execution_id": execution_id,
                **status_payload,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    if stdout_text:
        (last_request_dir / "zeroclaw_output.txt").write_text(stdout_text, encoding="utf-8", errors="replace")
    if stderr_text:
        (last_request_dir / "zeroclaw_stderr.log").write_text(stderr_text, encoding="utf-8", errors="replace")
    if runtime_trace_text:
        (last_request_dir / "runtime_trace.jsonl").write_text(
            runtime_trace_text,
            encoding="utf-8",
            errors="replace",
        )


def _run_zeroclaw(
    prompt: str,
    *,
    temperature: float | None = None,
    attachments: list[dict[str, Any]] | None = None,
) -> str:
    execution_id = uuid.uuid4().hex
    attachments_list = list(attachments or [])
    image_items = [item for item in attachments_list
                   if str(item.get("mime", "") or "").lower().startswith("image/")
                   and item.get("data")]
    provider_request_overrides = _provider_request_overrides()
    vision_proxy_ctx = (
        _BridgeVisionProxy(
            upstream_base=API_BASE,
            upstream_api_key=API_KEY,
            image_items=image_items,
            target_text=prompt,
            upstream_model=MODEL_NAME,
            request_overrides=provider_request_overrides,
        )
        if image_items
        else None
    )

    with tempfile.TemporaryDirectory(prefix=f"zeroclaw_gateway_{execution_id}_") as td:
        base = Path(td)
        workspace_dir = base / "workspace"
        home_dir = base / "home"
        zc_home = home_dir / ".zeroclaw"
        state_dir = workspace_dir / "state"
        stdout_path = base / "zeroclaw_output.txt"
        stderr_path = base / "zeroclaw_stderr.log"
        runtime_trace_path = state_dir / "runtime-trace.jsonl"
        workspace_dir.mkdir(parents=True, exist_ok=True)
        zc_home.mkdir(parents=True, exist_ok=True)
        state_dir.mkdir(parents=True, exist_ok=True)
        workspace_link = zc_home / "workspace"
        if workspace_link.exists() or workspace_link.is_symlink():
            workspace_link.unlink()
        workspace_link.symlink_to(workspace_dir)
        config_path = zc_home / "config.toml"

        attachment_manifest = _write_attachments(workspace_dir, attachments_list)

        env = os.environ.copy()
        env.update({
            "HOME": str(home_dir),
            "ZEROCLAW_API_KEY": API_KEY,
            "ZEROCLAW_PROVIDER": PROVIDER,
            "OPENAI_API_KEY": API_KEY,
            "OPENROUTER_API_KEY": API_KEY,
            "OPENAI_BASE_URL": API_BASE,
            "OPENAI_MODEL_NAME": MODEL_NAME,
        })

        command = (
            f"cd {shlex.quote(str(workspace_dir))} && "
            f"timeout {REQUEST_TIMEOUT} "
            f"zeroclaw agent -m {shlex.quote(prompt)} "
            f"> {shlex.quote(str(stdout_path))} "
            f"2> {shlex.quote(str(stderr_path))}"
        )

        if vision_proxy_ctx is not None:
            with vision_proxy_ctx as proxy:
                config_path.write_text(
                    _build_config_toml(temperature, provider_base_url_override=proxy.url),
                    encoding="utf-8",
                )
                os.chmod(config_path, 0o600)
                result = subprocess.run(
                    command, shell=True, cwd=str(workspace_dir), env=env,
                    capture_output=True, text=True,
                )
        else:
            config_path.write_text(_build_config_toml(temperature), encoding="utf-8")
            os.chmod(config_path, 0o600)
            result = subprocess.run(
                command, shell=True, cwd=str(workspace_dir), env=env,
                capture_output=True, text=True,
            )

        raw_output = stdout_path.read_text(encoding="utf-8", errors="replace").strip() if stdout_path.exists() else ""
        raw_stderr = stderr_path.read_text(encoding="utf-8", errors="replace").strip() if stderr_path.exists() else ""
        runtime_trace = (
            runtime_trace_path.read_text(encoding="utf-8", errors="replace").strip()
            if runtime_trace_path.exists()
            else ""
        )
        status_payload = {
            "created_at": int(time.time()),
            "model": MODEL_NAME,
            "provider": PROVIDER,
            "api_base": API_BASE,
            "temperature": DEFAULT_TEMPERATURE if temperature is None else float(temperature),
            "returncode": result.returncode,
            "timed_out": result.returncode == 124,
            "error_message": "",
        }
        sanitized_output, dropped_runtime_logs = _sanitize_output(raw_output)
        runtime_failure = _extract_runtime_failure(raw_output, raw_stderr)
        if result.returncode == 124:
            status_payload["error_message"] = f"ZeroClaw timed out after {REQUEST_TIMEOUT}s"
        elif result.returncode != 0:
            status_payload["error_message"] = (
                raw_stderr or raw_output or result.stderr.strip() or f"exit code {result.returncode}"
            )
        elif not raw_output:
            status_payload["error_message"] = f"ZeroClaw produced no output. stderr={raw_stderr}"
        elif not sanitized_output and dropped_runtime_logs:
            status_payload["error_message"] = runtime_failure or (
                "ZeroClaw stdout contained only runtime/provider logs, not assistant output."
            )

        _persist_last_request(
            execution_id=execution_id,
            prompt=prompt,
            attachment_manifest=attachment_manifest,
            stdout_text=raw_output,
            stderr_text=raw_stderr,
            runtime_trace_text=runtime_trace,
            status_payload=status_payload,
        )

        if result.returncode == 124:
            raise RuntimeError(f"ZeroClaw timed out after {REQUEST_TIMEOUT}s")
        if result.returncode != 0:
            raise RuntimeError(
                raw_stderr or raw_output or result.stderr.strip() or f"exit code {result.returncode}"
            )
        if not raw_output:
            raise RuntimeError(f"ZeroClaw produced no output. stderr={raw_stderr}")
        if not sanitized_output:
            if runtime_failure:
                raise RuntimeError(runtime_failure)
            if dropped_runtime_logs:
                raise RuntimeError(
                    "ZeroClaw produced no assistant output; stdout contained only runtime/provider logs."
                )
            raise RuntimeError(f"ZeroClaw produced no assistant output. stderr={raw_stderr}")
        return sanitized_output


class Handler(BaseHTTPRequestHandler):
    server_version = "ZeroClawBridge/0.1"

    @staticmethod
    def _normalized_path(path: str) -> str:
        trimmed = path.rstrip("/") or "/"
        if trimmed.startswith("/v1/"):
            trimmed = trimmed[3:]
        return trimmed

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _check_auth(self) -> bool:
        if not GATEWAY_TOKEN:
            return True
        auth = self.headers.get("Authorization", "")
        return auth == f"bearer {GATEWAY_TOKEN}" or auth == f"Bearer {GATEWAY_TOKEN}"

    def do_GET(self) -> None:  # noqa: N802
        if not self._check_auth():
            self._write_json(HTTPStatus.UNAUTHORIZED, {"error": {"message": "unauthorized"}})
            return
        if self._normalized_path(self.path) == "/models":
            self._write_json(HTTPStatus.OK, {
                "object": "list",
                "data": [{"id": MODEL_NAME, "object": "model"}],
            })
            return
        self._write_json(HTTPStatus.NOT_FOUND, {"error": {"message": "not found"}})

    def do_POST(self) -> None:  # noqa: N802
        if not self._check_auth():
            self._write_json(HTTPStatus.UNAUTHORIZED, {"error": {"message": "unauthorized"}})
            return
        if self._normalized_path(self.path) != "/chat/completions":
            self._write_json(HTTPStatus.NOT_FOUND, {"error": {"message": "not found"}})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            messages = payload.get("messages", [])
            if not isinstance(messages, list) or not messages:
                raise ValueError("messages must be a non-empty list")
            prompt = _build_prompt(messages)
            if not prompt.strip():
                raise ValueError("prompt is empty")
            temperature = _coerce_temperature(payload.get("temperature"))
            attachments = _normalize_attachments(payload.get("attachments"))
            disable_tools = _coerce_bool(payload.get("disable_tools")) or DISABLE_TOOLS
            if disable_tools:
                raw_output = _run_provider_chat_without_tools(
                    messages,
                    temperature=temperature,
                    attachments=attachments,
                )
            else:
                raw_output = _run_zeroclaw(prompt, temperature=temperature, attachments=attachments)
            now = int(time.time())
            self._write_json(HTTPStatus.OK, {
                "id": f"chatcmpl-{uuid.uuid4().hex}",
                "object": "chat.completion",
                "created": now,
                "model": payload.get("model") or MODEL_NAME,
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": raw_output,
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {},
            })
        except ValueError as exc:
            self._write_json(HTTPStatus.BAD_REQUEST, {
                "error": {
                    "message": str(exc),
                    "type": exc.__class__.__name__,
                }
            })
        except Exception as exc:
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                "error": {
                    "message": str(exc),
                    "type": exc.__class__.__name__,
                }
            })

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[ZeroClawBridge] {self.address_string()} - {fmt % args}", flush=True)


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> None:
    server = ReusableThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[ZeroClawBridge] listening on {HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
