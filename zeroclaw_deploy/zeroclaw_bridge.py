#!/usr/bin/env python3
"""Minimal OpenAI-compatible bridge for ZeroClaw CLI."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import tempfile
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


HOST = "0.0.0.0"
PORT = 8080
GATEWAY_TOKEN = os.environ.get("ZEROCLAW_GATEWAY_TOKEN", "ZEROCLAW")
REQUEST_TIMEOUT = int(os.environ.get("ZEROCLAW_REQUEST_TIMEOUT", "1200"))
MAX_TOOL_ITERATIONS = int(os.environ.get("ZEROCLAW_MAX_TOOL_ITERATIONS", "100"))
MAX_ACTIONS_PER_HOUR = int(os.environ.get("ZEROCLAW_MAX_ACTIONS_PER_HOUR", "200"))
WORKSPACE_ONLY = os.environ.get("ZEROCLAW_WORKSPACE_ONLY", "false").strip().lower() == "true"
MODEL_NAME = os.environ.get("OPENAI_MODEL_NAME", "zeroclaw")
API_BASE = os.environ.get("OPENAI_BASE_URL", "")
API_KEY = os.environ.get("OPENAI_API_KEY", "")


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


def _quote_toml(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _build_config_toml() -> str:
    workspace_only = "true" if WORKSPACE_ONLY else "false"
    return (
        f"default_provider = {_quote_toml(PROVIDER)}\n"
        f"default_model = {_quote_toml(MODEL_NAME)}\n\n"
        "default_temperature = 0.7\n"
        "model_routes = []\n"
        "embedding_routes = []\n\n"
        "[model_providers]\n\n"
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


def _sanitize_output(raw_output: str) -> str:
    cleaned_lines: list[str] = []
    for raw_line in raw_output.splitlines():
        line = ANSI_RE.sub("", raw_line).rstrip()
        if LOG_LINE_RE.match(line) and "zeroclaw::" in line:
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()


def _run_zeroclaw(prompt: str) -> str:
    execution_id = uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix=f"zeroclaw_gateway_{execution_id}_") as td:
        base = Path(td)
        workspace_dir = base / "workspace"
        home_dir = base / "home"
        zc_home = home_dir / ".zeroclaw"
        stdout_path = base / "zeroclaw_output.txt"
        stderr_path = base / "zeroclaw_stderr.log"
        workspace_dir.mkdir(parents=True, exist_ok=True)
        zc_home.mkdir(parents=True, exist_ok=True)
        workspace_link = zc_home / "workspace"
        if workspace_link.exists() or workspace_link.is_symlink():
            workspace_link.unlink()
        workspace_link.symlink_to(workspace_dir)
        config_path = zc_home / "config.toml"
        config_path.write_text(_build_config_toml(), encoding="utf-8")
        os.chmod(config_path, 0o600)

        env = os.environ.copy()
        env.update({
            "HOME": str(home_dir),
            "ZEROCLAW_API_KEY": API_KEY,
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
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(workspace_dir),
            env=env,
            capture_output=True,
            text=True,
        )
        raw_output = stdout_path.read_text(encoding="utf-8", errors="replace").strip() if stdout_path.exists() else ""
        raw_stderr = stderr_path.read_text(encoding="utf-8", errors="replace").strip() if stderr_path.exists() else ""
        if result.returncode == 124:
            raise RuntimeError(f"ZeroClaw timed out after {REQUEST_TIMEOUT}s")
        if result.returncode != 0:
            raise RuntimeError(raw_stderr or raw_output or result.stderr.strip() or f"exit code {result.returncode}")
        if not raw_output:
            raise RuntimeError(f"ZeroClaw produced no output. stderr={raw_stderr}")
        return _sanitize_output(raw_output) or raw_output


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
            raw_output = _run_zeroclaw(prompt)
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
        except Exception as exc:
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                "error": {
                    "message": str(exc),
                    "type": exc.__class__.__name__,
                }
            })

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[ZeroClawBridge] {self.address_string()} - {fmt % args}", flush=True)


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[ZeroClawBridge] listening on {HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
