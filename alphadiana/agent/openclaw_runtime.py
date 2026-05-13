"""OpenClaw runtime manager for ROCK-backed execution.

Bootstraps the OpenClaw gateway inside a live ROCK sandbox, waits for
readiness, performs warmup, and collects runtime artifacts (gateway logs,
workspace snapshots) after task execution.
"""

from __future__ import annotations

import http.server
import json
import os
import re
import shlex
import socketserver
import tempfile
import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from alphadiana.container_runtime import (
    HTTPHealthcheck,
    PodmanAgentRuntime,
    PodmanAgentRuntimeResult,
    PodmanAgentSpec,
    RuntimeFile,
)


import logging as _logging

_logger = _logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
_UNRESOLVED_PLACEHOLDER_RE = re.compile(r"^\$\{.+\}$")

# Hint for slow npm installs in restricted networks.
NPM_TIMEOUT_HINT = (
    "npm install is taking too long. Consider switching to a faster registry:\n"
    "  npm config set registry https://registry.npmmirror.com/\n"
    "See https://npmmirror.com/ for details."
)


def _progress(message: str) -> None:
    print(f"[OpenClaw] {message}", flush=True)


def _is_unresolved_placeholder(value: str) -> bool:
    return bool(_UNRESOLVED_PLACEHOLDER_RE.match(value.strip()))


# ---------------------------------------------------------------------------
# MITM logprob capture proxy for Docker-backed OpenClaw sandboxes
#
# The OpenClaw gateway runs inside a Docker container and makes HTTP calls to
# vLLM.  Since the gateway strips logprob data from its response to AlphaDiana,
# we intercept the container-to-vLLM path by starting a host-side proxy that:
#   1. Binds to 0.0.0.0 (accessible from Docker containers via host bridge IP)
#   2. Forwards requests to the real vLLM endpoint (with logprobs=True injected)
#   3. Captures logprob data from vLLM SSE responses in real time
# ---------------------------------------------------------------------------

class _OpenClawLogprobProxyHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler that forwards to vLLM and captures per-token logprob dicts."""

    server: "_OpenClawLogprobServer"

    def log_message(self, fmt: str, *args: Any) -> None:  # silence access log
        pass

    def do_POST(self) -> None:  # noqa: N802
        import httpx

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(body)
        except Exception:
            payload = {}

        # Inject logprob request parameters
        payload["logprobs"] = True
        payload["top_logprobs"] = self.server.top_logprobs
        # Qwen3.x outputs thinking in delta.content using a non-standard "Thinking Process:"
        # prefix that the OpenClaw gateway cannot parse (it expects delta.reasoning_content
        # for thinking, not embedded in delta.content). Disable thinking mode so the gateway
        # receives clean answer content.
        chat_kwargs = payload.get("chat_template_kwargs") or {}
        chat_kwargs["enable_thinking"] = False
        payload["chat_template_kwargs"] = chat_kwargs

        upstream_url = self.server.upstream.rstrip("/") + self.path
        headers = {
            k: v for k, v in self.headers.items()
            if k.lower() not in ("host", "content-length", "transfer-encoding")
        }
        is_stream = payload.get("stream", False)

        try:
            if is_stream:
                self._handle_streaming(upstream_url, headers, payload, httpx)
            else:
                self._handle_non_streaming(upstream_url, headers, payload, httpx)
        except Exception as exc:
            _logger.debug("OpenClaw logprob proxy error: %s", exc)
            self.send_error(502, f"Proxy error: {exc}")

    def _handle_non_streaming(
        self, url: str, headers: dict, payload: dict, httpx: Any
    ) -> None:
        resp = self.server.client.post(url, headers=headers, json=payload)
        resp_bytes = resp.content

        # Capture logprobs
        try:
            resp_json = resp.json()
            with self.server.lock:
                self.server.raw_responses.append(resp_json)
        except Exception:
            pass

        self.send_response(resp.status_code)
        for k, v in resp.headers.items():
            if k.lower() not in ("transfer-encoding", "content-encoding"):
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(resp_bytes)))
        self.end_headers()
        self.wfile.write(resp_bytes)

    def _strip_and_capture_event(self, event_bytes: bytes) -> bytes:
        """Capture logprobs from an SSE event and return it with logprobs stripped.

        The gateway's SSE parser fails on large data: lines produced when
        top_logprobs=20 injects thousands of bytes per token. Strip logprobs
        from the forwarded event so the gateway sees compact normal-sized chunks,
        while still capturing the logprob data in streaming_logprobs.
        """
        out_lines: list[bytes] = []
        for line_bytes in event_bytes.split(b"\n"):
            line = line_bytes.decode("utf-8", errors="replace")
            if line.startswith("data:") and line.strip() != "data: [DONE]":
                data_str = line[5:].strip()
                try:
                    chunk = json.loads(data_str)
                    choices = chunk.get("choices", [])
                    if choices:
                        logprob_data = choices[0].get("logprobs")
                        if isinstance(logprob_data, dict):
                            content = logprob_data.get("content") or []
                            if content:
                                with self.server.lock:
                                    self.server.streaming_logprobs.extend(content)
                        choices[0]["logprobs"] = None
                    out_lines.append(b"data: " + json.dumps(chunk, separators=(",", ":")).encode())
                except Exception:
                    out_lines.append(line_bytes)
            else:
                out_lines.append(line_bytes)
        return b"\n".join(out_lines)

    def _handle_streaming(
        self, url: str, headers: dict, payload: dict, httpx: Any
    ) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        with self.server.client.stream(
            "POST", url, headers=headers, json=payload
        ) as resp:
            # Buffer raw bytes and emit complete SSE events with logprobs stripped.
            # Stripping is required: top_logprobs=20 makes each data: line thousands
            # of bytes long; the OpenClaw gateway SSE parser fails on such large lines.
            buf = b""
            for raw_chunk in resp.iter_raw():
                buf += raw_chunk
                while b"\n\n" in buf:
                    event_bytes, buf = buf.split(b"\n\n", 1)
                    stripped = self._strip_and_capture_event(event_bytes)
                    try:
                        self.wfile.write(stripped + b"\n\n")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        return
            if buf:
                try:
                    self.wfile.write(buf)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass


class _OpenClawLogprobServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

    def __init__(self, upstream: str, top_logprobs: int) -> None:
        super().__init__(("0.0.0.0", 0), _OpenClawLogprobProxyHandler)
        self.upstream = upstream
        self.top_logprobs = top_logprobs
        self.raw_responses: list[dict] = []
        self.streaming_logprobs: list[dict] = []
        self.lock = threading.Lock()
        import httpx as _httpx
        self.client = _httpx.Client(timeout=120.0)


class OpenClawLogprobProxy:
    """Host-side MITM proxy accessible from Docker containers via host bridge IP."""

    def __init__(self, upstream: str, top_logprobs: int) -> None:
        self._upstream = upstream
        self._top_logprobs = top_logprobs
        self._server: _OpenClawLogprobServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._server = _OpenClawLogprobServer(self._upstream, self._top_logprobs)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server:
            try:
                self._server.shutdown()
                self._server.client.close()
            except Exception:
                pass

    @property
    def local_port(self) -> int:
        assert self._server is not None
        return self._server.server_address[1]

    def proxy_url_for_docker(self, docker_host_ip: str = "host.docker.internal") -> str:
        """Return the URL that Docker containers can use to reach this proxy."""
        return f"http://{docker_host_ip}:{self.local_port}"

    def captured_records(self) -> list[dict]:
        """Return all captured per-token logprob dicts (SSE preferred over buffered)."""
        if self._server is None:
            return []
        from alphadiana.agent.logprob_capture import raw_token_logprob_dict_to_record, extract_openai_logprob_records
        records: list[dict] = []
        token_index = 0
        with self._server.lock:
            sse_tokens = list(self._server.streaming_logprobs)
            payloads = list(self._server.raw_responses)
        if sse_tokens:
            for item in sse_tokens:
                records.append(raw_token_logprob_dict_to_record(item, token_index))
                token_index += 1
        else:
            for payload in payloads:
                new_records, token_index = extract_openai_logprob_records(
                    payload, start_index=token_index
                )
                records.extend(new_records)
        return records


def _is_ready_probe_status(status_code: int) -> bool:
    """Return whether the gateway probe reached a live HTTP route.

    OpenClaw configs may expose only a subset of OpenAI-compatible endpoints.
    In that case probing ``/v1/models`` can legitimately return 405 even though
    the gateway is already running behind the ROCK proxy.
    """
    return status_code in (200, 404, 405)


def _extract_text_from_gateway_payload(payload: Any) -> str:
    """Extract assistant text or reasoning from a gateway warmup response."""
    if not isinstance(payload, dict):
        return ""

    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {})
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(content, list):
                parts: list[str] = []
                for item in content:
                    if isinstance(item, str):
                        parts.append(item)
                    elif isinstance(item, dict):
                        text = item.get("text") or item.get("content")
                        if isinstance(text, str):
                            parts.append(text)
                joined = "".join(parts).strip()
                if joined:
                    return joined
            reasoning_content = message.get("reasoning_content")
            if isinstance(reasoning_content, str) and reasoning_content.strip():
                return reasoning_content.strip()
            if isinstance(reasoning_content, list):
                parts = []
                for item in reasoning_content:
                    if isinstance(item, str):
                        parts.append(item)
                    elif isinstance(item, dict):
                        text = item.get("text") or item.get("content")
                        if isinstance(text, str):
                            parts.append(text)
                joined = "".join(parts).strip()
                if joined:
                    return joined
        text = choices[0].get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()

    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    message = payload.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()

    return ""


_OPENCLAW_HOME_CANDIDATES = (
    "/root/.openclaw",
    "/tmp/oc_home/.openclaw",
    "/home/node/.openclaw",
)


_OPENCLAW_WORKSPACE_CANDIDATES = tuple(
    f"{home}/workspace" for home in _OPENCLAW_HOME_CANDIDATES
)
_SECRET_FIELD_NAMES = frozenset({
    "api_key",
    "apikey",
    "authorization",
    "token",
})


def _redact_secret_fields(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in _SECRET_FIELD_NAMES or normalized.endswith("_api_key"):
                redacted[key] = "REDACTED"
            else:
                redacted[key] = _redact_secret_fields(item)
        return redacted
    if isinstance(value, list):
        return [_redact_secret_fields(item) for item in value]
    return value


def _sanitize_json_text(raw_text: str) -> str:
    try:
        payload = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return raw_text
    return json.dumps(_redact_secret_fields(payload), indent=2, ensure_ascii=False)


class OpenClawRuntimeManager:
    """Bootstraps the OpenClaw gateway inside a live ROCK sandbox."""

    requires_sandbox = True

    def __init__(self, config: dict) -> None:
        self._gateway_token = config.get("gateway_token", "OPENCLAW")
        self._gateway_model = config.get("model", "openclaw")
        self._gateway_port = int(config.get("gateway_port", 8080))
        self._rock_agent_config_path = str(self._resolve_config_path(config.get("rock_agent_config_path", ""))) if config.get("rock_agent_config_path") else ""
        self._openclaw_config_path = str(self._resolve_config_path(config.get("openclaw_config_path", ""))) if config.get("openclaw_config_path") else ""
        self._gateway_startup_timeout = int(config.get("gateway_startup_timeout", 180))
        self._gateway_warmup_enabled = bool(config.get("gateway_warmup_enabled", False))
        self._gateway_warmup_timeout = int(config.get("gateway_warmup_timeout", 180))
        self._gateway_warmup_initial_delay = float(config.get("gateway_warmup_initial_delay", 5.0))
        self._gateway_log_path = config.get("gateway_log_path", "/tmp/gateway.log")
        self._workspace_path = config.get("workspace_path", "/root/.openclaw/workspace")
        self._remote_openclaw_config_path = config.get("remote_openclaw_config_path", "/tmp/openclaw.json")
        self._started_sandboxes: set[str] = set()
        self._agent_md_applied_sandboxes: set[str] = set()
        self._temp_dirs: list[tempfile.TemporaryDirectory] = []
        self._provider_env = {
            "OPENAI_BASE_URL": self._resolve_provider_override(config, "OPENAI_BASE_URL", "openai_base_url"),
            "OPENAI_API_KEY": self._resolve_provider_override(config, "OPENAI_API_KEY", "openai_api_key"),
            "OPENAI_MODEL_NAME": self._resolve_provider_override(config, "OPENAI_MODEL_NAME", "openai_model_name"),
        }
        self._temperature = config.get("temperature", None)
        self._top_p = config.get("top_p", None)
        self._max_tokens = config.get("max_tokens", None)
        self._agent_timeout_seconds = self._coerce_positive_int(
            config.get(
                "openclaw_agent_timeout",
                config.get("agent_timeout", config.get("request_timeout")),
            )
        )
        self._exec_timeout_seconds = self._coerce_positive_int(
            config.get(
                "openclaw_exec_timeout",
                config.get("exec_timeout", self._agent_timeout_seconds),
            )
        )
        self._rock_agent_run_timeout_seconds = self._coerce_positive_int(
            config.get(
                "openclaw_agent_run_timeout",
                config.get(
                    "rock_agent_run_timeout",
                    config.get("agent_run_timeout", self._agent_timeout_seconds),
                ),
            )
        )
        self._undici_stream_timeout_ms = self._resolve_undici_stream_timeout_ms(config)
        # Logprob capture via Docker-accessible MITM proxy
        self._logprob_capture: dict = config.get("_logprob_capture", {})
        self._logprob_proxy: OpenClawLogprobProxy | None = None
        self._docker_host_ip: str = str(config.get("docker_host_ip", "host.docker.internal"))

        # Agent.md customization
        self._agent_md_mode = config.get("agent_md_mode", "none")
        self._agent_md_content = config.get("agent_md_content", "")

        # Embedding configuration
        self._embedding_api_base = config.get("embedding_api_base", "")
        self._embedding_api_key = config.get("embedding_api_key", "")
        self._workspace = config.get("workspace", "")
        self._skip_bootstrap = config.get("skip_bootstrap")
        self._context_injection = str(config.get("context_injection", "") or "").strip()
        self._system_prompt_override = str(config.get("system_prompt_override", "") or "").strip()
        self._tools_profile = str(config.get("tools_profile", "") or "").strip()
        self._tools_allow = list(config.get("tools_allow", []) or [])
        self._tools_deny = list(config.get("tools_deny", []) or [])

    @staticmethod
    def _resolve_provider_override(config: dict[str, Any], primary_key: str, fallback_key: str) -> str:
        """Accept both historical uppercase and newer lowercase provider keys.

        Prefer explicit non-placeholder config values and only then retain
        unresolved placeholders so the later validation path can raise a clear
        error. Launcher-shell env fallback still happens later in
        ``_prepare_runtime_files`` so tests and runtime behavior can control it
        at the point of materialization.
        """
        unresolved = ""
        for key in (primary_key, fallback_key):
            value = str(config.get(key, "") or "").strip()
            if not value:
                continue
            if not _is_unresolved_placeholder(value):
                return value
            if not unresolved:
                unresolved = value
        return unresolved

    def _resolve_config_path(self, path_str: str) -> Path:
        """Resolve config paths stably, independent of the current working directory."""
        path = Path(path_str).expanduser()
        if path.is_absolute():
            return path.resolve()
        for candidate in [
            (PROJECT_ROOT / path).resolve(),
            path.resolve(),
        ]:
            if candidate.exists():
                return candidate
        return (PROJECT_ROOT / path).resolve()

    @staticmethod
    def _coerce_positive_int(value: Any) -> int | None:
        if value in (None, "", 0, "0"):
            return None
        try:
            coerced = int(float(value))
        except (TypeError, ValueError):
            return None
        return coerced if coerced > 0 else None

    def _resolve_undici_stream_timeout_ms(self, config: dict[str, Any]) -> int | None:
        timeout_ms = self._coerce_positive_int(
            config.get("openclaw_undici_stream_timeout_ms")
        )
        if timeout_ms is not None:
            return timeout_ms
        timeout_seconds = self._coerce_positive_int(
            config.get(
                "openclaw_undici_stream_timeout",
                self._agent_timeout_seconds,
            )
        )
        if timeout_seconds is None:
            return None
        return timeout_seconds * 1000

    @property
    def is_configured(self) -> bool:
        return bool(self._rock_agent_config_path and self._openclaw_config_path)

    def set_provider_api_base(self, api_base: str) -> None:
        self._provider_env["OPENAI_BASE_URL"] = str(api_base or "").strip()

    def set_provider_api_key(self, api_key: str) -> None:
        self._provider_env["OPENAI_API_KEY"] = str(api_key or "").strip()

    def inject_agent_md(self, sandbox: Any) -> None:
        """Inject or modify AGENTS.md in the sandbox based on mode."""
        sandbox_id = str(getattr(sandbox, "sandbox_id", ""))
        if sandbox_id and sandbox_id in self._agent_md_applied_sandboxes:
            return
        agent_md_path = "/root/AGENTS.md"
        if self._agent_md_mode == "append":
            existing = sandbox.read_text(agent_md_path)
            new_content = existing + self._agent_md_content
            sandbox.upload(agent_md_path, new_content.encode("utf-8"))
        elif self._agent_md_mode == "override":
            sandbox.upload(agent_md_path, self._agent_md_content.encode("utf-8"))
        # mode == "none": do nothing
        if sandbox_id and self._agent_md_mode != "none":
            self._agent_md_applied_sandboxes.add(sandbox_id)

    def _build_openclaw_config(self, base_config: dict | None = None) -> dict:
        """Build OpenClaw gateway config dict with optional embedding provider."""
        config: dict[str, Any] = deepcopy(base_config or {})
        agents = config.setdefault("agents", {})
        defaults = agents.setdefault("defaults", {})
        if self._embedding_api_base:
            models = config.setdefault("models", {})
            providers = models.setdefault("providers", {})
            providers["embedding"] = {
                "baseUrl": self._embedding_api_base,
                "apiKey": self._embedding_api_key or "EMPTY",
                "api": "openai",
            }
        if self._workspace:
            defaults["workspace"] = self._workspace
        if self._skip_bootstrap is not None:
            defaults["skipBootstrap"] = bool(self._skip_bootstrap)
        if self._context_injection:
            defaults["contextInjection"] = self._context_injection
        if self._system_prompt_override:
            defaults["systemPromptOverride"] = self._system_prompt_override
        if self._agent_timeout_seconds is not None:
            defaults["timeoutSeconds"] = self._agent_timeout_seconds
        if self._tools_profile:
            tools = config.setdefault("tools", {})
            tools["profile"] = self._tools_profile
        if self._tools_allow:
            tools = config.setdefault("tools", {})
            tools["allow"] = list(self._tools_allow)
        if self._tools_deny:
            tools = config.setdefault("tools", {})
            tools["deny"] = list(self._tools_deny)
        if self._exec_timeout_seconds is not None:
            tools = config.setdefault("tools", {})
            exec_cfg = tools.setdefault("exec", {})
            if not isinstance(exec_cfg, dict):
                exec_cfg = {}
                tools["exec"] = exec_cfg
            exec_cfg["timeoutSec"] = self._exec_timeout_seconds

        # Remove memory.enabled — incompatible with openclaw@2026.3.7
        memory = config.get("memory", {})
        if isinstance(memory, dict):
            memory.pop("enabled", None)
            if not memory:
                config.pop("memory", None)

        gateway = config.setdefault("gateway", {})
        gateway["port"] = self._gateway_port
        # ROCK publishes the container's 8080/tcp to a host port. Binding the
        # gateway to loopback keeps that published port unreachable from the
        # host-side evaluator, so force a non-loopback bind for sandbox runs.
        gateway["bind"] = "lan"
        gateway.pop("customBindHost", None)

        self._ensure_image_capability_metadata(config)
        self._apply_generation_params(config)
        return config

    def _openclaw_undici_timeout_patch_command(self) -> str:
        if self._undici_stream_timeout_ms is None:
            return ""
        timeout_ms = self._undici_stream_timeout_ms
        return (
            "export "
            f"OPENCLAW_UNDICI_STREAM_TIMEOUT_MS=${{OPENCLAW_UNDICI_STREAM_TIMEOUT_MS:-{timeout_ms}}}; "
            "patch_file=\"$(grep -Rsl 'opts?.timeoutMs ?? 18e5' /app/dist 2>/dev/null | head -1 || true)\"; "
            "if [ -n \"$patch_file\" ]; then "
            "sed -i \"s/const timeoutMsRaw = opts?.timeoutMs ?? 18e5;/"
            "const timeoutMsRaw = opts?.timeoutMs ?? "
            f"(Number(process.env.OPENCLAW_UNDICI_STREAM_TIMEOUT_MS || {timeout_ms}) || 18e5);/\" "
            "\"$patch_file\"; "
            "else echo '[AlphaDiana] OpenClaw undici stream timeout patch skipped: pattern not found' "
            ">> /tmp/gateway.log; fi;"
        )

    def _apply_openclaw_runtime_patches(self, generated_config: dict[str, Any]) -> None:
        patch_cmd = self._openclaw_undici_timeout_patch_command()
        if not patch_cmd:
            return
        run_cmd = str(generated_config.get("run_cmd", "") or "").strip()
        if not run_cmd:
            return
        if (
            "opts?.timeoutMs ?? (Number(process.env.OPENCLAW_UNDICI_STREAM_TIMEOUT_MS"
            in run_cmd
        ):
            return
        generated_config["run_cmd"] = f"{patch_cmd} {run_cmd}"

    def _apply_generation_params(self, config: dict[str, Any]) -> None:
        """Apply common sampling controls to the configured OpenClaw model."""
        agents = config.setdefault("agents", {})
        defaults = agents.setdefault("defaults", {})
        model_defaults = defaults.setdefault("models", {})

        primary_model = ""
        model_cfg = defaults.get("model")
        if isinstance(model_cfg, dict):
            primary_model = str(model_cfg.get("primary", "") or "").strip()
        if not primary_model:
            model_name = str(self._provider_env.get("OPENAI_MODEL_NAME", "") or "").strip()
            if model_name:
                primary_model = f"local/{model_name}"
        if not primary_model:
            return

        target_cfg = model_defaults.setdefault(primary_model, {})
        if not isinstance(target_cfg, dict):
            target_cfg = {}
            model_defaults[primary_model] = target_cfg
        params = target_cfg.setdefault("params", {})
        if not isinstance(params, dict):
            params = {}
            target_cfg["params"] = params

        if self._temperature is not None:
            params["temperature"] = self._temperature
        if self._top_p is not None:
            params["topP"] = self._top_p
        if self._max_tokens is not None:
            try:
                max_tokens = int(self._max_tokens)
            except (TypeError, ValueError):
                max_tokens = 0
            if max_tokens > 0:
                params["maxTokens"] = max_tokens

    def _ensure_image_capability_metadata(self, config: dict[str, Any]) -> None:
        """Mark the configured OpenAI-compatible model as vision-capable.

        OpenClaw only preserves prompt images when the resolved model metadata
        advertises ``input`` including ``"image"``. Our runtime config only
        provided ``id``/``name``, so image-backed tasks were silently downgraded
        to text-only prompts inside the OpenClaw runtime.

        Declaring image support here makes the runtime retain forwarded images
        and surface backend errors if a chosen model is not actually multimodal.
        """
        target_names = {"${OPENAI_MODEL_NAME}"}
        configured_name = self._provider_env.get("OPENAI_MODEL_NAME", "").strip()
        if configured_name:
            target_names.add(configured_name)

        def _merge_input_caps(entry: dict[str, Any]) -> None:
            raw_caps = entry.get("input")
            caps: list[str] = []
            if isinstance(raw_caps, list):
                for value in raw_caps:
                    if isinstance(value, str):
                        normalized = value.strip()
                        if normalized and normalized not in caps:
                            caps.append(normalized)
            if "text" not in caps:
                caps.insert(0, "text")
            if "image" not in caps:
                caps.append("image")
            entry["input"] = caps

        models = config.setdefault("models", {})
        providers = models.setdefault("providers", {})
        for provider_cfg in providers.values():
            if not isinstance(provider_cfg, dict):
                continue
            provider_models = provider_cfg.get("models")
            if not isinstance(provider_models, list):
                continue
            for model_entry in provider_models:
                if not isinstance(model_entry, dict):
                    continue
                entry_id = str(model_entry.get("id", "") or "").strip()
                entry_name = str(model_entry.get("name", "") or "").strip()
                if entry_id in target_names or entry_name in target_names:
                    _merge_input_caps(model_entry)

    def _probe_gateway_alive(self, sandbox: Any) -> bool:
        """Quick liveness check — GET /v1/models with a short timeout."""
        try:
            import httpx
            info = self.runtime_info(sandbox)
            url = f"{info['api_base']}/models"
            headers = {"Authorization": f"bearer {self._gateway_token}"}
            resp = httpx.get(url, headers=headers, timeout=5, trust_env=False)
            return _is_ready_probe_status(resp.status_code)
        except Exception as exc:
            _logger.warning("Gateway liveness probe failed for sandbox %s: %s",
                            getattr(sandbox, "sandbox_id", "?"), exc)
            return False

    def ensure_ready(self, sandbox: Any) -> dict:
        """Ensure OpenClaw gateway is running inside the sandbox.

        Idempotent: reuses if already started for this sandbox_id.
        A liveness probe is performed before reuse to detect dead sandboxes.
        """
        sandbox_id = str(getattr(sandbox, "sandbox_id", ""))
        if sandbox_id in self._started_sandboxes:
            if self._probe_gateway_alive(sandbox):
                _progress(f"reusing existing runtime for sandbox_id={sandbox_id}")
                return self.runtime_info(sandbox)
            _logger.warning("Sandbox %s was cached but failed liveness probe; re-deploying", sandbox_id)
            self._started_sandboxes.discard(sandbox_id)

        _progress(f"preparing runtime files for sandbox_id={sandbox_id}")
        config_dir = self._prepare_runtime_files(sandbox)
        if self._agent_md_mode != "none":
            _progress("injecting AGENTS.md customizations")
            self.inject_agent_md(sandbox)
        _progress(f"installing ROCK agent from config_dir={config_dir}")
        sandbox.install_agent(config_dir=config_dir)
        _progress("starting OpenClaw gateway inside sandbox")
        sandbox.run_agent("", config_dir=config_dir)
        _progress("waiting for OpenClaw gateway readiness")
        self._wait_for_gateway(sandbox)
        if self._gateway_warmup_enabled:
            _progress("warming up OpenClaw chat completions endpoint")
            try:
                self._warmup_gateway(sandbox)
            except Exception as exc:
                _logger.warning("OpenClaw gateway warmup did not fully succeed; continuing: %s", exc)
                _progress(f"gateway warmup did not fully succeed; continuing: {exc}")
        else:
            _progress("skipping OpenClaw chat completions warmup")
        self._started_sandboxes.add(sandbox_id)
        _progress(f"OpenClaw runtime ready for sandbox_id={sandbox_id}")
        return self.runtime_info(sandbox)

    def runtime_info(self, sandbox: Any) -> dict:
        """Return connection info for the running gateway."""
        api_base = ""
        published_base = getattr(sandbox, "published_base", None)
        if callable(published_base):
            try:
                api_base = f"{published_base(self._gateway_port).rstrip('/')}/v1"
            except Exception:
                _logger.debug("Failed to resolve published OpenClaw gateway port", exc_info=True)
        if not api_base:
            api_base = sandbox.proxy_v1_base()
        return {
            "sandbox_id": str(getattr(sandbox, "sandbox_id", "")),
            "gateway_url": f"{api_base}/chat/completions",
            "api_base": api_base,
            "gateway_token": self._gateway_token,
        }

    def collect_artifacts(self, sandbox: Any) -> dict:
        """Collect gateway logs, workspace files, and sandbox metadata."""
        sandbox_id = str(getattr(sandbox, "sandbox_id", ""))
        _progress(f"collecting artifacts for sandbox_id={sandbox_id}")

        gateway_log = self._safe_read_range(sandbox, self._gateway_log_path, 1, 400)

        workspace_roots: list[str] = []
        for candidate in (
            self._workspace_path,
            * _OPENCLAW_WORKSPACE_CANDIDATES,
        ):
            candidate = str(candidate or "").strip()
            if candidate and candidate not in workspace_roots:
                workspace_roots.append(candidate)

        workspace_listing_cmd = " ; ".join(
            f'if [ -d {shlex.quote(root)} ] && [ -r {shlex.quote(root)} ]; then '
            f'find {shlex.quote(root)} -maxdepth 4 -type f; fi'
            for root in workspace_roots
        )
        workspace_listing = sandbox.execute(
            f"({workspace_listing_cmd}) | sort -u" if workspace_listing_cmd else "true"
        )
        workspace_paths = [
            line.strip()
            for line in workspace_listing.stdout.splitlines()
            if line.strip().startswith("/")
        ]
        workspace_file_contents: dict[str, str] = {}
        manifest_files: dict[str, str] = {
            "gateway_log_source": self._gateway_log_path,
            "workspace_root": next(iter(workspace_roots), self._workspace_path),
        }
        state_files: list[str] = []
        collected_snapshot_paths = list(workspace_paths)
        if workspace_paths:
            workspace_file_contents["openclaw_workspace_listing.txt"] = (
                "\n".join(dict.fromkeys(workspace_paths)) + "\n"
            )
            manifest_files["workspace_listing"] = "openclaw_workspace_listing.txt"
        for remote_path in workspace_paths:
            text = self._read_text_best_effort(sandbox, remote_path)
            if text.strip():
                workspace_file_contents[remote_path] = text

        if gateway_log.strip():
            workspace_file_contents["openclaw_gateway.log"] = gateway_log
            manifest_files["gateway_log"] = "openclaw_gateway.log"

        session_listing = sandbox.execute(
            "ls -t "
            + " ".join(
                f"{home}/agents/main/sessions/*.jsonl"
                for home in _OPENCLAW_HOME_CANDIDATES
            )
            + " 2>/dev/null | head -10"
        )
        session_paths = [
            line.strip()
            for line in session_listing.stdout.splitlines()
            if line.strip().endswith(".jsonl")
        ]
        session_alias_path = ""
        for remote_path in session_paths[:3]:
            text = workspace_file_contents.get(remote_path, "")
            if not text:
                text = self._read_text_best_effort(sandbox, remote_path)
            if not text.strip():
                continue
            workspace_file_contents[remote_path] = text
            if not session_alias_path:
                session_alias_path = "openclaw_session.jsonl"
                workspace_file_contents.setdefault(session_alias_path, text)
                state_files.append(session_alias_path)

        response_stream_alias = ""
        for remote_path in workspace_paths:
            if Path(remote_path).name != "openclaw_output.jsonl":
                continue
            text = workspace_file_contents.get(remote_path, "")
            if not text:
                text = self._read_text_best_effort(sandbox, remote_path)
            if not text.strip():
                continue
            response_stream_alias = "openclaw_output.jsonl"
            workspace_file_contents[remote_path] = text
            workspace_file_contents.setdefault(response_stream_alias, text)
            break
        if not response_stream_alias and session_alias_path:
            response_stream_alias = session_alias_path

        system_prompt_candidates = [
            f"{self._workspace_path}/AGENTS.md",
            f"{self._workspace_path}/SOUL.md",
            *(
                f"{workspace}/AGENTS.md"
                for workspace in _OPENCLAW_WORKSPACE_CANDIDATES
            ),
            *(
                f"{workspace}/SOUL.md"
                for workspace in _OPENCLAW_WORKSPACE_CANDIDATES
            ),
        ]
        system_prompt_paths: list[str] = []
        for remote_path in system_prompt_candidates:
            content = self._read_text_best_effort(sandbox, remote_path)
            if not content.strip():
                continue
            workspace_file_contents.setdefault(remote_path, content)
            system_prompt_paths.append(remote_path)

        openclaw_config_text = self._read_text_best_effort(
            sandbox,
            self._remote_openclaw_config_path,
        )
        if openclaw_config_text.strip():
            sanitized_config = _sanitize_json_text(openclaw_config_text)
            workspace_file_contents.setdefault(
                self._remote_openclaw_config_path,
                sanitized_config,
            )
            workspace_file_contents.setdefault(
                "openclaw_runtime_config.json",
                sanitized_config,
            )
            manifest_files["runtime_config"] = "openclaw_runtime_config.json"
            state_files.append("openclaw_runtime_config.json")

        sidecar_candidates: list[tuple[str, str, str]] = []
        for workspace_root in workspace_roots:
            workspace_root = str(workspace_root or "").strip()
            if workspace_root:
                sidecar_candidates.append((
                    f"{workspace_root}/.openclaw/workspace-state.json",
                    "openclaw_workspace_state.json",
                    "workspace_state",
                ))
        for home in _OPENCLAW_HOME_CANDIDATES:
            sidecar_candidates.extend([
                (
                    f"{home}/workspace-state.json",
                    "openclaw_workspace_state.json",
                    "workspace_state",
                ),
                (
                    f"{home}/agents/main/sessions.json",
                    "openclaw_sessions_index.json",
                    "session_index",
                ),
            ])
        for remote_path, alias, manifest_key in sidecar_candidates:
            if alias in workspace_file_contents:
                continue
            content = self._read_text_best_effort(sandbox, remote_path)
            if not content.strip():
                continue
            workspace_file_contents[remote_path] = content
            workspace_file_contents[alias] = content
            manifest_files[manifest_key] = alias
            state_files.append(alias)
            if remote_path not in collected_snapshot_paths:
                collected_snapshot_paths.append(remote_path)
        artifact_collection_history = getattr(sandbox, "command_history", [])
        sandbox_metadata = sandbox.metadata() if hasattr(sandbox, "metadata") else {}
        if isinstance(sandbox_metadata, dict) and "command_history" in sandbox_metadata:
            sandbox_metadata["artifact_collection_history"] = sandbox_metadata.pop("command_history")

        artifact_manifest = {
            "files": {
                **manifest_files,
                "response_stream": response_stream_alias,
                "session_trace": session_alias_path or (session_paths[0] if session_paths else ""),
                "openclaw_output": response_stream_alias,
                "openclaw_session": session_alias_path or (session_paths[0] if session_paths else ""),
                "system_prompt": system_prompt_paths[0] if system_prompt_paths else "",
            },
            "workspace_snapshot_paths": collected_snapshot_paths,
            "session_paths": session_paths,
            "system_prompt_paths": system_prompt_paths,
            # These timings are for artifact-collection shell commands only.
            "artifact_collection_history": artifact_collection_history,
        }
        if state_files:
            artifact_manifest["state_files"] = list(dict.fromkeys(state_files))
        return {
            "artifact_manifest": artifact_manifest,
            "gateway_log_excerpt": gateway_log,
            "workspace_snapshot_paths": collected_snapshot_paths,
            "workspace_file_contents": workspace_file_contents,
            "sandbox_metadata": sandbox_metadata,
        }

    def _read_text_best_effort(self, sandbox: Any, remote_path: str) -> str:
        """Read a sandbox file, falling back to ``cat`` when direct reads fail."""
        path = str(remote_path or "").strip()
        if not path:
            return ""
        try:
            text = sandbox.read_text(path)
            if isinstance(text, str):
                return text
        except Exception:
            pass
        try:
            result = sandbox.execute(f"cat {shlex.quote(path)}")
        except Exception:
            return ""
        exit_code = getattr(result, "exit_code", getattr(result, "returncode", 1))
        if int(exit_code or 0) != 0:
            return ""
        stdout = getattr(result, "stdout", "")
        return stdout if isinstance(stdout, str) else ""

    def _prepare_runtime_files(self, sandbox: Any) -> Path:
        """Upload openclaw.json and generate ROCK agent config."""
        rock_agent_config = Path(self._rock_agent_config_path)
        openclaw_config = Path(self._openclaw_config_path)
        if not rock_agent_config.exists():
            raise FileNotFoundError(f"rock_agent_config_path not found: {rock_agent_config}")
        if not openclaw_config.exists():
            raise FileNotFoundError(f"openclaw_config_path not found: {openclaw_config}")

        base_openclaw_config = json.loads(openclaw_config.read_text(encoding="utf-8"))
        rendered_openclaw_config = self._build_openclaw_config(base_openclaw_config)

        generated_config = yaml.safe_load(rock_agent_config.read_text(encoding="utf-8"))
        if self._rock_agent_run_timeout_seconds is not None:
            generated_config["agent_run_timeout"] = self._rock_agent_run_timeout_seconds
        env_cfg = generated_config.setdefault("env", {})
        for key in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL_NAME"):
            # Respect explicit agent config overrides before inheriting the
            # launcher shell environment. This keeps `-o agent.config.*=...`
            # effective even when `source scripts/activate.sh` has already
            # populated OPENAI_* from repo-level defaults.
            config_val = self._provider_env.get(key, "").strip()
            if config_val and not _is_unresolved_placeholder(config_val):
                env_cfg[key] = config_val
                continue
            local_val = os.environ.get(key, "").strip()
            if local_val:
                env_cfg[key] = local_val
                continue
            current_val = str(env_cfg.get(key, ""))
            if not current_val or _is_unresolved_placeholder(current_val):
                raise RuntimeError(
                    f"OpenClaw auto-deploy requires {key} to be set either in agent.config "
                    f"or the local environment."
                )

        # Start Docker-accessible MITM logprob proxy when capture is enabled.
        # The proxy intercepts calls from the OpenClaw container to vLLM,
        # captures per-token logprob dicts, and is accessible via the host bridge IP.
        if self._logprob_capture.get("enabled"):
            upstream_base_url = env_cfg.get("OPENAI_BASE_URL", "").rstrip("/")
            if upstream_base_url:
                top_logprobs = int(self._logprob_capture.get("top_logprobs", 20))
                # Stop any previous proxy
                if self._logprob_proxy is not None:
                    try:
                        self._logprob_proxy.stop()
                    except Exception:
                        pass
                # Strip trailing /v1 — proxy re-appends the full request path (which
                # already contains /v1/...), so passing it in upstream causes double /v1.
                upstream_no_v1 = upstream_base_url
                if upstream_no_v1.endswith("/v1"):
                    upstream_no_v1 = upstream_no_v1[:-3]
                self._logprob_proxy = OpenClawLogprobProxy(upstream_no_v1, top_logprobs)
                self._logprob_proxy.start()
                proxy_url = self._logprob_proxy.proxy_url_for_docker(self._docker_host_ip)
                env_cfg["OPENAI_BASE_URL"] = proxy_url + "/v1"
                _progress(
                    f"logprob capture proxy started: "
                    f"container→proxy({proxy_url})→vLLM({upstream_base_url})"
                )

        env_cfg["OPENCLAW_GATEWAY_TOKEN"] = str(self._gateway_token or "OPENCLAW")
        if self._undici_stream_timeout_ms is not None:
            env_cfg["OPENCLAW_UNDICI_STREAM_TIMEOUT_MS"] = str(
                self._undici_stream_timeout_ms
            )

        # Propagate the resolved OPENAI_* values (including proxy URL when logprob
        # capture is active) into the openclaw.json env section.  The JSON template
        # ships with empty-string defaults that would otherwise override the values
        # already written to rock_agent_config.yaml.
        oc_env = rendered_openclaw_config.setdefault("env", {})
        for key in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL_NAME"):
            val = env_cfg.get(key, "")
            if val:
                oc_env[key] = val

        self._apply_openclaw_runtime_patches(generated_config)

        td = tempfile.TemporaryDirectory(prefix="alphadiana-openclaw-")
        self._temp_dirs.append(td)
        config_dir = Path(td.name)
        (config_dir / "openclaw.json").write_text(
            json.dumps(rendered_openclaw_config, indent=2),
            encoding="utf-8",
        )
        generated_path = config_dir / "rock_agent_config.yaml"
        generated_path.write_text(
            yaml.safe_dump(generated_config, sort_keys=False),
            encoding="utf-8",
        )
        _progress(f"generated ROCK agent config at {generated_path}")
        return config_dir

    def _wait_for_gateway(self, sandbox: Any) -> None:
        """Poll /v1/models until the gateway responds."""
        import httpx

        info = self.runtime_info(sandbox)
        url = f"{info['api_base']}/models"
        headers = {"Authorization": f"bearer {self._gateway_token}"}
        deadline = time.monotonic() + self._gateway_startup_timeout
        last_error: Exception | None = None
        attempt = 0
        while time.monotonic() < deadline:
            attempt += 1
            try:
                _progress(f"gateway probe attempt={attempt} url={url}")
                response = httpx.get(url, headers=headers, timeout=10, trust_env=False)
                if _is_ready_probe_status(response.status_code):
                    _progress(f"gateway probe succeeded with status={response.status_code}")
                    return
                _progress(f"gateway probe returned status={response.status_code}")
            except Exception as exc:
                last_error = exc
                _progress(f"gateway probe failed: {exc}")
            time.sleep(2)
        if last_error is not None:
            raise RuntimeError(f"OpenClaw gateway did not become ready: {last_error}") from last_error
        raise RuntimeError("OpenClaw gateway did not become ready before timeout")

    def _warmup_gateway(self, sandbox: Any) -> None:
        """Send a test chat completion to ensure the endpoint is live."""
        import httpx

        info = self.runtime_info(sandbox)
        url = f"{info['api_base']}/chat/completions"
        headers = {
            "Authorization": f"bearer {self._gateway_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._gateway_model,
            "messages": [
                {"role": "system", "content": "Reply briefly."},
                {"role": "user", "content": "Say READY."},
            ],
            "temperature": 0.0,
            "max_tokens": 32,
            "stream": False,
        }
        deadline = time.monotonic() + self._gateway_warmup_timeout
        last_error: Exception | None = None
        attempt = 0
        if self._gateway_warmup_initial_delay > 0:
            _progress(
                f"waiting {self._gateway_warmup_initial_delay:.1f}s before first warmup request"
            )
            time.sleep(self._gateway_warmup_initial_delay)
        while time.monotonic() < deadline:
            attempt += 1
            try:
                _progress(f"gateway warmup attempt={attempt} url={url}")
                remaining = max(1.0, deadline - time.monotonic())
                response = httpx.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=remaining,
                    trust_env=False,
                )
                body: Any
                try:
                    body = response.json()
                except Exception:
                    body = response.text
                if response.status_code == 200 and _extract_text_from_gateway_payload(body):
                    _progress("gateway warmup succeeded")
                    return
                if response.status_code == 502:
                    _progress("gateway warmup got transient 502; retrying")
                else:
                    _progress(f"gateway warmup returned status={response.status_code}")
                last_error = RuntimeError(f"warmup status={response.status_code} body={body!r}")
            except Exception as exc:
                last_error = exc
                _progress(f"gateway warmup failed: {exc}")
            time.sleep(min(6, 2 + attempt))
        if last_error is not None:
            raise RuntimeError(f"OpenClaw gateway warmup did not succeed: {last_error}") from last_error
        raise RuntimeError("OpenClaw gateway warmup did not succeed before timeout")

    def get_proxy_logprob_records(self) -> list[dict]:
        """Return per-token logprob records captured by the MITM proxy (may be empty)."""
        if self._logprob_proxy is None:
            return []
        try:
            return self._logprob_proxy.captured_records()
        except Exception as exc:
            _logger.debug("Error collecting proxy logprob records: %s", exc)
            return []

    def teardown(self) -> None:
        """Clean up temporary directories and the logprob capture proxy."""
        if self._logprob_proxy is not None:
            try:
                self._logprob_proxy.stop()
            except Exception:
                pass
            self._logprob_proxy = None
        for td in self._temp_dirs:
            try:
                td.cleanup()
            except Exception:
                pass
        self._temp_dirs.clear()

    def _safe_read_range(
        self,
        sandbox: Any,
        path: str,
        start_line: int,
        end_line: int,
    ) -> str:
        """Read a range of lines from a file, with fallback to full read."""
        try:
            return sandbox.read_text_range(path, start_line, end_line)
        except Exception:
            try:
                return sandbox.read_text(path)
            except Exception:
                return ""


class OpenClawPodmanRuntimeManager(OpenClawRuntimeManager):
    """Bootstraps the OpenClaw gateway inside a Podman-managed container."""

    requires_sandbox = False

    def __init__(self, config: dict) -> None:
        config = dict(config)
        config.setdefault("openclaw_config_path", "openclaw_deploy/openclaw.json")
        super().__init__(config)
        self._podman_gateway_config_path = str(
            self._resolve_config_path(
                config.get("podman_gateway_config_path", "openclaw_deploy/podman_gateway.yaml")
            )
        )
        self._docker_host_ip = str(
            config.get("podman_host_ip", config.get("docker_host_ip", "host.containers.internal"))
        )
        self._podman_runtime = config.get("_podman_agent_runtime") or PodmanAgentRuntime()
        self._podman_image_override = str(config.get("podman_image", config.get("image", "")) or "").strip()
        self._podman_result: PodmanAgentRuntimeResult | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self._openclaw_config_path and self._podman_gateway_config_path)

    def ensure_ready(self, sandbox: Any = None) -> dict:
        if self._podman_result is not None:
            return self.runtime_info(sandbox)
        _progress("preparing OpenClaw Podman runtime spec")
        spec = self._build_podman_spec()
        _progress(f"starting OpenClaw Podman gateway image={spec.image}")
        self._podman_result = self._podman_runtime.start(spec)
        if self._gateway_warmup_enabled:
            _progress("warming up OpenClaw Podman chat completions endpoint")
            try:
                self._warmup_podman_gateway(self._podman_result.api_base)
            except Exception as exc:
                _logger.warning("OpenClaw Podman gateway warmup did not fully succeed; continuing: %s", exc)
                _progress(f"Podman gateway warmup did not fully succeed; continuing: {exc}")
        else:
            _progress("skipping OpenClaw Podman chat completions warmup")
        return self.runtime_info(sandbox)

    def runtime_info(self, sandbox: Any = None) -> dict:
        if self._podman_result is None:
            return {
                "sandbox_id": "",
                "gateway_url": "",
                "api_base": "",
                "gateway_token": self._gateway_token,
                "metadata": {
                    "container_engine": "podman",
                    "adapter_name": "openclaw-podman",
                    "logprob_support": self._logprob_support_status(),
                },
            }
        api_base = self._podman_result.api_base
        metadata = dict(self._podman_result.metadata)
        metadata.update({
            "container_engine": "podman",
            "adapter_name": "openclaw-podman",
            "logprob_support": self._logprob_support_status(),
        })
        return {
            "sandbox_id": "",
            "gateway_url": f"{api_base.rstrip('/')}/chat/completions",
            "api_base": api_base,
            "gateway_token": self._gateway_token,
            "metadata": metadata,
            "container_engine": "podman",
            "adapter_name": "openclaw-podman",
            "image": metadata.get("image", ""),
            "container_id": metadata.get("container_id", ""),
        }

    def collect_artifacts(self, sandbox: Any = None) -> dict:
        if self._podman_result is None:
            return {
                "artifact_manifest": {},
                "gateway_log_excerpt": "",
                "workspace_snapshot_paths": [],
                "workspace_file_contents": {},
                "sandbox_metadata": {},
            }
        try:
            result = self._podman_runtime.collect_artifacts()
            self._podman_result = result
        except Exception:
            result = self._podman_result
        workspace_file_contents: dict[str, str] = {}
        manifest_files: dict[str, str] = {}
        gateway_log = result.artifacts.get(self._gateway_log_path, "") or result.logs
        if gateway_log:
            workspace_file_contents["openclaw_gateway.log"] = gateway_log
            manifest_files["gateway_log"] = "openclaw_gateway.log"
        for path, text in result.config_snapshots.items():
            workspace_file_contents[path] = text
            if path == self._remote_openclaw_config_path:
                workspace_file_contents["openclaw_runtime_config.json"] = text
                manifest_files["runtime_config"] = "openclaw_runtime_config.json"
        for path, text in result.artifacts.items():
            if path == self._gateway_log_path:
                continue
            workspace_file_contents[path] = text
        return {
            "artifact_manifest": {
                "files": manifest_files,
                "workspace_snapshot_paths": list(workspace_file_contents),
                "runtime_metadata": dict(result.metadata),
            },
            "gateway_log_excerpt": gateway_log,
            "workspace_snapshot_paths": list(workspace_file_contents),
            "workspace_file_contents": workspace_file_contents,
            "sandbox_metadata": dict(result.metadata),
        }

    def teardown(self) -> None:
        try:
            self._podman_runtime.cleanup(check=False)
        finally:
            self._podman_result = None
            super().teardown()

    def _build_podman_spec(self) -> PodmanAgentSpec:
        podman_cfg = self._load_podman_gateway_config()
        env_cfg = self._resolve_podman_env()
        openclaw_config = Path(self._openclaw_config_path)
        if not openclaw_config.exists():
            raise FileNotFoundError(f"openclaw_config_path not found: {openclaw_config}")
        base_openclaw_config = json.loads(openclaw_config.read_text(encoding="utf-8"))
        rendered_openclaw_config = self._build_openclaw_config(base_openclaw_config)
        oc_env = rendered_openclaw_config.setdefault("env", {})
        for key, value in env_cfg.items():
            if value:
                oc_env[key] = value

        image = self._podman_image_override or str(podman_cfg.get("image", "") or "").strip()
        if not image:
            raise RuntimeError("OpenClaw Podman runtime requires podman_image or podman_gateway.yaml image")
        exposed_port = int(podman_cfg.get("exposed_port", self._gateway_port) or self._gateway_port)
        safe_config_paths = tuple(podman_cfg.get("safe_config_paths") or [self._remote_openclaw_config_path])
        artifact_paths = tuple(podman_cfg.get("artifact_paths") or [self._gateway_log_path])
        run_command = str(podman_cfg.get("run_command", "") or "").strip()
        if not run_command:
            raise RuntimeError("OpenClaw Podman runtime requires podman_gateway.yaml run_command")
        return PodmanAgentSpec(
            adapter_name="openclaw-podman",
            image=image,
            workdir=str(podman_cfg.get("workdir", "/workspace") or "/workspace"),
            env=env_cfg,
            ports={exposed_port: podman_cfg.get("host_port")},
            exposed_port=exposed_port,
            startup_timeout=float(podman_cfg.get("startup_timeout", self._gateway_startup_timeout)),
            request_timeout=float(self._agent_timeout_seconds or podman_cfg.get("request_timeout", 600)),
            cleanup_timeout=float(podman_cfg.get("cleanup_timeout", 30)),
            install_commands=tuple(podman_cfg.get("install_commands") or ()),
            run_command=run_command,
            process_log_path=str(podman_cfg.get("process_log_path", self._gateway_log_path) or self._gateway_log_path),
            files=(
                RuntimeFile(
                    str(podman_cfg.get("openclaw_config_path", self._remote_openclaw_config_path) or self._remote_openclaw_config_path),
                    json.dumps(rendered_openclaw_config, indent=2),
                ),
            ),
            safe_config_paths=safe_config_paths,
            artifact_paths=artifact_paths,
            api_base_suffix="/v1",
            healthcheck=HTTPHealthcheck(
                path="/models",
                token=self._gateway_token,
                expected_statuses=(200, 404, 405),
                interval_sec=float(podman_cfg.get("healthcheck_interval_sec", 2.0)),
                request_timeout_sec=float(podman_cfg.get("healthcheck_timeout_sec", 5.0)),
            ),
            metadata={
                "adapter_name": "openclaw-podman",
                "gateway_token": self._gateway_token,
                "logprob_support": self._logprob_support_status(),
                "logprob_proxy": "openclaw" if self._logprob_capture.get("enabled") else "",
            },
        )

    def _load_podman_gateway_config(self) -> dict[str, Any]:
        path = Path(self._podman_gateway_config_path)
        if not path.exists():
            raise FileNotFoundError(f"podman_gateway_config_path not found: {path}")
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise RuntimeError(f"podman gateway config must be a mapping: {path}")
        return loaded

    def _resolve_podman_env(self) -> dict[str, str]:
        env_cfg: dict[str, str] = {}
        for key in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL_NAME"):
            config_val = self._provider_env.get(key, "").strip()
            if config_val and not _is_unresolved_placeholder(config_val):
                env_cfg[key] = config_val
                continue
            local_val = os.environ.get(key, "").strip()
            if local_val:
                env_cfg[key] = local_val
                continue
            raise RuntimeError(
                f"OpenClaw Podman runtime requires {key} to be set either in agent.config "
                f"or the local environment."
            )
        if self._logprob_capture.get("enabled"):
            upstream_base_url = env_cfg.get("OPENAI_BASE_URL", "").rstrip("/")
            if upstream_base_url:
                top_logprobs = int(self._logprob_capture.get("top_logprobs", 20))
                if self._logprob_proxy is not None:
                    try:
                        self._logprob_proxy.stop()
                    except Exception:
                        pass
                upstream_no_v1 = upstream_base_url[:-3] if upstream_base_url.endswith("/v1") else upstream_base_url
                self._logprob_proxy = OpenClawLogprobProxy(upstream_no_v1, top_logprobs)
                self._logprob_proxy.start()
                podman_host_ip = str(
                    os.environ.get(
                        "ALPHADIANA_PODMAN_HOST_IP",
                        self._docker_host_ip or "host.containers.internal",
                    )
                )
                proxy_url = self._logprob_proxy.proxy_url_for_docker(podman_host_ip)
                env_cfg["OPENAI_BASE_URL"] = proxy_url + "/v1"
                _progress(
                    f"logprob capture proxy started: "
                    f"container→proxy({proxy_url})→vLLM({upstream_base_url})"
                )
        env_cfg["OPENCLAW_GATEWAY_TOKEN"] = str(self._gateway_token or "OPENCLAW")
        if self._undici_stream_timeout_ms is not None:
            env_cfg["OPENCLAW_UNDICI_STREAM_TIMEOUT_MS"] = str(self._undici_stream_timeout_ms)
        return env_cfg

    def _warmup_podman_gateway(self, api_base: str) -> None:
        import httpx

        url = f"{api_base.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"bearer {self._gateway_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._gateway_model,
            "messages": [
                {"role": "system", "content": "Reply briefly."},
                {"role": "user", "content": "Say READY."},
            ],
            "temperature": 0.0,
            "max_tokens": 32,
            "stream": False,
        }
        deadline = time.monotonic() + self._gateway_warmup_timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                remaining = max(1.0, deadline - time.monotonic())
                response = httpx.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=remaining,
                    trust_env=False,
                )
                try:
                    body: Any = response.json()
                except Exception:
                    body = response.text
                if response.status_code == 200 and _extract_text_from_gateway_payload(body):
                    return
                last_error = RuntimeError(f"warmup status={response.status_code} body={body!r}")
            except Exception as exc:
                last_error = exc
            time.sleep(2)
        if last_error is not None:
            raise RuntimeError(f"OpenClaw Podman gateway warmup did not succeed: {last_error}") from last_error
        raise RuntimeError("OpenClaw Podman gateway warmup did not succeed before timeout")

    def _logprob_support_status(self) -> str:
        return "enabled" if self._logprob_capture.get("enabled") else "disabled"
