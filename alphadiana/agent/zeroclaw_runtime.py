"""ZeroClaw runtime manager for ROCK-proxied execution.

Starts a lightweight HTTP bridge inside a live ROCK sandbox. The bridge wraps
``zeroclaw agent -m`` and exposes minimal OpenAI-compatible endpoints through
the ROCK proxy, so benchmark execution aligns with the OpenClaw gateway flow.
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import time
from pathlib import Path
from typing import Any

import logging as _logging

from alphadiana.container_runtime import (
    HTTPHealthcheck,
    PodmanAgentRuntime,
    PodmanAgentRuntimeResult,
    PodmanAgentSpec,
    RuntimeFile,
    podman_proxy_env,
)

_logger = _logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _progress(message: str) -> None:
    print(f"[ZeroClaw] {message}", flush=True)


def _is_ready_probe_status(status_code: int) -> bool:
    return status_code in (200, 404, 405)


def _normalize_api_base(api_base: str) -> str:
    return api_base.strip().rstrip("/")


def _parse_optional_bool(value: Any) -> bool | None:
    if value in ("", None):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value!r}")


def _provider_body_overrides_from_config(config: dict[str, Any]) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    raw_extra_body = config.get("extra_body", {})
    if isinstance(raw_extra_body, dict):
        overrides.update(raw_extra_body)
    raw_chat_template_kwargs = config.get("chat_template_kwargs", None)
    if isinstance(raw_chat_template_kwargs, dict):
        merged_chat_template_kwargs = dict(overrides.get("chat_template_kwargs", {}))
        merged_chat_template_kwargs.update(raw_chat_template_kwargs)
        overrides["chat_template_kwargs"] = merged_chat_template_kwargs
    enable_thinking = _parse_optional_bool(config.get("enable_thinking", None))
    reasoning_enabled = _parse_optional_bool(config.get("reasoning_enabled", None))
    if enable_thinking is None and reasoning_enabled is not None:
        enable_thinking = reasoning_enabled
    if enable_thinking is not None:
        merged_chat_template_kwargs = dict(overrides.get("chat_template_kwargs", {}))
        merged_chat_template_kwargs["enable_thinking"] = enable_thinking
        overrides["chat_template_kwargs"] = merged_chat_template_kwargs
    if reasoning_enabled is not None:
        merged_reasoning = dict(overrides.get("reasoning", {}))
        merged_reasoning["enabled"] = reasoning_enabled
        overrides["reasoning"] = merged_reasoning
    return overrides


def _resolve_zeroclaw_provider(provider: str, api_base: str) -> str:
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


class ZeroClawRuntimeManager:
    """Bootstraps a ZeroClaw bridge inside a live ROCK sandbox."""

    requires_sandbox = True

    def __init__(self, config: dict) -> None:
        self._gateway_token = config.get("gateway_token", "ZEROCLAW")
        self._gateway_model = config.get("model", "zeroclaw")
        self._bridge_port = int(config.get("bridge_port", 8080))
        self._bridge_host = str(config.get("bridge_host", "0.0.0.0") or "0.0.0.0").strip()
        self._model_api_base = str(config.get("api_base", config.get("provider_api_base", ""))).strip()
        self._model_api_key = str(config.get("api_key", config.get("provider_api_key", ""))).strip()
        self._model_name = str(config.get("model", "")).strip() or self._gateway_model
        configured_provider = str(config.get("provider", "")).strip().lower()
        self._provider = _resolve_zeroclaw_provider(configured_provider, self._model_api_base)
        self._gateway_startup_timeout = int(config.get("gateway_startup_timeout", 90))
        self._gateway_warmup_timeout = int(config.get("gateway_warmup_timeout", 180))
        self._gateway_warmup_initial_delay = float(config.get("gateway_warmup_initial_delay", 2.0))
        self._bridge_log_path = config.get("bridge_log_path", "/tmp/zeroclaw-gateway.log")
        self._remote_bridge_path = config.get("remote_bridge_path", "/tmp/zeroclaw_bridge.py")
        self._bridge_pidfile = config.get("bridge_pidfile", "/tmp/zeroclaw-bridge.pid")
        self._artifact_root = str(config.get("artifact_root", "/tmp/zeroclaw-bridge-artifacts") or "/tmp/zeroclaw-bridge-artifacts").strip()
        self._request_timeout = int(config.get("request_timeout", 1200))
        raw_provider_timeout = config.get("provider_timeout_secs", None)
        if raw_provider_timeout in ("", None):
            self._provider_timeout_secs = self._request_timeout
        else:
            self._provider_timeout_secs = int(raw_provider_timeout)
        raw_provider_max_tokens = config.get("provider_max_tokens", None)
        if raw_provider_max_tokens in ("", None):
            raw_provider_max_tokens = config.get("max_tokens", None)
        if raw_provider_max_tokens in ("", None):
            self._provider_max_tokens: int | None = None
        else:
            self._provider_max_tokens = int(raw_provider_max_tokens)
        raw_top_p = config.get("top_p", None)
        self._top_p: float | None = None if raw_top_p in ("", None) else float(raw_top_p)
        self._provider_body_overrides = _provider_body_overrides_from_config(config)
        self._reasoning_enabled = _parse_optional_bool(config.get("reasoning_enabled", None))
        raw_reasoning_effort = config.get("reasoning_effort", None)
        if raw_reasoning_effort in ("", None):
            self._reasoning_effort: str | None = None
        else:
            self._reasoning_effort = str(raw_reasoning_effort).strip()
        raw_temperature = config.get("temperature", 0.0)
        self._temperature = float(raw_temperature if raw_temperature not in ("", None) else 0.0)
        self._max_tool_iterations = int(config.get("max_tool_iterations", 100))
        self._max_actions_per_hour = int(config.get("max_actions_per_hour", 200))
        self._workspace_only = bool(config.get("workspace_only", False))
        self._disable_tools = bool(config.get("disable_tools", False))
        self._bridge_template_path = self._resolve_bridge_template_path(
            config.get("bridge_template_path", "zeroclaw_deploy/zeroclaw_bridge.py")
        )
        self._logprob_capture: dict[str, Any] = dict(config.get("_logprob_capture", {}) or {})
        self._started_sandboxes: set[str] = set()
        self._managed_sandboxes: dict[str, Any] = {}

    def _resolve_bridge_template_path(self, path_str: str) -> Path:
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

    @property
    def is_configured(self) -> bool:
        return self._bridge_template_path.exists()

    def runtime_info(self, sandbox: Any) -> dict:
        api_base = ""
        published_base = getattr(sandbox, "published_base", None)
        if callable(published_base):
            try:
                api_base = f"{published_base(self._bridge_port).rstrip('/')}/v1"
            except Exception:
                _logger.debug("Failed to resolve published ZeroClaw bridge port", exc_info=True)
        if not api_base:
            api_base = sandbox.proxy_v1_base()
        return {
            "sandbox_id": str(getattr(sandbox, "sandbox_id", "")),
            "gateway_url": f"{api_base}/chat/completions",
            "api_base": api_base,
            "gateway_token": self._gateway_token,
        }

    def _runtime_env(self) -> dict[str, str]:
        return {
            "OPENAI_BASE_URL": self._model_api_base or os.environ.get("OPENAI_BASE_URL", ""),
            "OPENAI_API_KEY": self._model_api_key or os.environ.get("OPENAI_API_KEY", ""),
            "OPENAI_MODEL_NAME": self._model_name or os.environ.get("OPENAI_MODEL_NAME", self._gateway_model),
            "OPENROUTER_API_KEY": self._model_api_key or os.environ.get("OPENROUTER_API_KEY", ""),
            "ZEROCLAW_GATEWAY_TOKEN": self._gateway_token,
            "ZEROCLAW_BRIDGE_HOST": self._bridge_host,
            "ZEROCLAW_PROVIDER": self._provider,
            "ZEROCLAW_ARTIFACT_ROOT": self._artifact_root,
            "ZEROCLAW_TEMPERATURE": str(self._temperature),
            "ZEROCLAW_TOP_P": "" if self._top_p is None else str(self._top_p),
            "ZEROCLAW_PROVIDER_REQUEST_OVERRIDES": json.dumps(
                self._provider_body_overrides,
                ensure_ascii=True,
            ),
            "ZEROCLAW_PROVIDER_TIMEOUT_SECS": str(self._provider_timeout_secs),
            "ZEROCLAW_PROVIDER_MAX_TOKENS": (
                str(self._provider_max_tokens)
                if self._provider_max_tokens is not None
                else ""
            ),
            "ZEROCLAW_REASONING_ENABLED": (
                str(self._reasoning_enabled).lower()
                if self._reasoning_enabled is not None
                else ""
            ),
            "ZEROCLAW_REASONING_EFFORT": self._reasoning_effort or "",
            "ZEROCLAW_REQUEST_TIMEOUT": str(self._request_timeout),
            "ZEROCLAW_MAX_TOOL_ITERATIONS": str(self._max_tool_iterations),
            "ZEROCLAW_MAX_ACTIONS_PER_HOUR": str(self._max_actions_per_hour),
            "ZEROCLAW_WORKSPACE_ONLY": "true" if self._workspace_only else "false",
            "ZEROCLAW_DISABLE_TOOLS": "true" if self._disable_tools else "",
        }

    @staticmethod
    def _env_prefix(env: dict[str, str]) -> str:
        items = []
        for key, value in sorted(env.items()):
            if value:
                items.append(f"{key}={shlex.quote(value)}")
        return " ".join(items)

    def _probe_gateway_alive(self, sandbox: Any) -> bool:
        try:
            import httpx

            info = self.runtime_info(sandbox)
            resp = httpx.get(
                f"{info['api_base']}/models",
                headers={"Authorization": f"bearer {self._gateway_token}"},
                timeout=5,
                trust_env=False,
            )
            return _is_ready_probe_status(resp.status_code)
        except Exception as exc:
            _logger.warning(
                "ZeroClaw bridge liveness probe failed for sandbox %s: %s",
                getattr(sandbox, "sandbox_id", "?"),
                exc,
            )
            return False

    def ensure_ready(self, sandbox: Any) -> dict:
        sandbox_id = str(getattr(sandbox, "sandbox_id", ""))
        if self._probe_gateway_alive(sandbox):
            if sandbox_id:
                self._started_sandboxes.add(sandbox_id)
                self._managed_sandboxes[sandbox_id] = sandbox
            _progress(f"reusing live runtime for sandbox_id={sandbox_id}")
            return self.runtime_info(sandbox)

        if sandbox_id in self._started_sandboxes:
            self._started_sandboxes.discard(sandbox_id)

        if not self._bridge_template_path.exists():
            raise FileNotFoundError(f"ZeroClaw bridge template not found: {self._bridge_template_path}")

        bridge_bytes = self._bridge_template_path.read_bytes()
        sandbox.upload(self._remote_bridge_path, bridge_bytes)
        chmod_result = sandbox.execute(f"chmod 755 {self._remote_bridge_path}")
        if chmod_result.exit_code != 0:
            raise RuntimeError(
                "Failed to chmod ZeroClaw bridge: "
                f"{chmod_result.stderr.strip() or chmod_result.stdout.strip()}"
            )

        version_result = sandbox.execute("command -v zeroclaw >/dev/null 2>&1 && zeroclaw --version")
        if version_result.exit_code != 0:
            raise RuntimeError(
                "ZeroClaw bridge startup requires zeroclaw preinstalled in the sandbox image."
            )

        env_prefix = self._env_prefix(self._runtime_env())
        start_command = (
            f"pkill -f {shlex.quote(self._remote_bridge_path)} >/dev/null 2>&1 || true && "
            f"{env_prefix} nohup python {shlex.quote(self._remote_bridge_path)} >> {shlex.quote(self._bridge_log_path)} 2>&1 & "
            f"echo $! > {shlex.quote(self._bridge_pidfile)}"
        ).strip()
        result = sandbox.execute(f"bash -lc {shlex.quote(start_command)}")
        if result.exit_code != 0:
            raise RuntimeError(
                "Failed to start ZeroClaw bridge: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )

        self._wait_for_gateway(sandbox)
        self._warmup_gateway(sandbox)
        self._started_sandboxes.add(sandbox_id)
        if sandbox_id:
            self._managed_sandboxes[sandbox_id] = sandbox
        _progress(f"ZeroClaw runtime ready for sandbox_id={sandbox_id}")
        return self.runtime_info(sandbox)

    def _wait_for_gateway(self, sandbox: Any) -> None:
        import httpx

        info = self.runtime_info(sandbox)
        url = f"{info['api_base']}/models"
        headers = {"Authorization": f"bearer {self._gateway_token}"}
        deadline = time.monotonic() + self._gateway_startup_timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                resp = httpx.get(url, headers=headers, timeout=10, trust_env=False)
                if _is_ready_probe_status(resp.status_code):
                    return
                last_error = RuntimeError(f"status={resp.status_code} body={resp.text[:200]!r}")
            except Exception as exc:
                last_error = exc
            time.sleep(2)
        if last_error is not None:
            raise RuntimeError(f"ZeroClaw bridge did not become ready: {last_error}") from last_error
        raise RuntimeError("ZeroClaw bridge did not become ready before timeout")

    def _warmup_gateway(self, sandbox: Any) -> None:
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
        if self._gateway_warmup_initial_delay > 0:
            time.sleep(self._gateway_warmup_initial_delay)

        deadline = time.monotonic() + self._gateway_warmup_timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                resp = httpx.post(url, headers=headers, json=payload, timeout=120, trust_env=False)
                body = resp.json()
                choices = body.get("choices", [])
                if resp.status_code == 200 and choices:
                    message = choices[0].get("message", {})
                    if isinstance(message, dict) and str(message.get("content", "")).strip():
                        return
                last_error = RuntimeError(f"warmup status={resp.status_code} body={body!r}")
            except Exception as exc:
                last_error = exc
            time.sleep(2)
        if last_error is not None:
            raise RuntimeError(f"ZeroClaw bridge warmup did not succeed: {last_error}") from last_error
        raise RuntimeError("ZeroClaw bridge warmup did not succeed before timeout")

    def collect_artifacts(self, sandbox: Any) -> dict:
        def _read_text(path: str, *, limit_lines: int | None = None) -> str:
            cat_cmd = f"cat {shlex.quote(path)}"
            if limit_lines is not None:
                cat_cmd = f"sed -n '1,{limit_lines}p' {shlex.quote(path)}"
            probe = f"test -f {shlex.quote(path)} && {cat_cmd} || true"
            try:
                result = sandbox.execute(f"bash -lc {shlex.quote(probe)}")
            except Exception:
                return ""
            if getattr(result, "exit_code", 0) == 0:
                return getattr(result, "stdout", "")
            return ""

        bridge_log = ""
        bridge_log_probe = (
            f"test -f {shlex.quote(self._bridge_log_path)} "
            f"&& sed -n '1,300p' {shlex.quote(self._bridge_log_path)} || true"
        )
        bridge_log_cmd = f"bash -lc {shlex.quote(bridge_log_probe)}"
        try:
            bridge_log_result = sandbox.execute(bridge_log_cmd)
        except Exception:
            bridge_log_result = None
        if bridge_log_result is not None and getattr(bridge_log_result, "exit_code", 0) == 0:
            bridge_log = getattr(bridge_log_result, "stdout", "")
        last_request_dir = f"{self._artifact_root.rstrip('/')}/last_request"
        workspace_file_contents = {}
        snapshot_paths: list[str] = []
        for relative_name in [
            "task.txt",
            "zeroclaw_output.txt",
            "zeroclaw_stderr.log",
            "runtime_trace.jsonl",
            "attachment_manifest.json",
            "status.json",
        ]:
            remote_path = f"{last_request_dir}/{relative_name}"
            text = _read_text(remote_path)
            if not text:
                continue
            workspace_file_contents[relative_name] = text
            snapshot_paths.append(remote_path)
        return {
            "artifact_manifest": {
                "files": {
                    "bridge_log_source": self._bridge_log_path,
                    "artifact_root": self._artifact_root,
                },
            },
            "gateway_log_excerpt": bridge_log,
            "workspace_snapshot_paths": snapshot_paths,
            "workspace_file_contents": workspace_file_contents,
            "sandbox_metadata": sandbox.metadata() if hasattr(sandbox, "metadata") else {},
        }

    def teardown(self) -> None:
        stop_command = (
            f"if [ -f {shlex.quote(self._bridge_pidfile)} ]; then "
            f"kill -{signal.SIGTERM} $(cat {shlex.quote(self._bridge_pidfile)}) >/dev/null 2>&1 || true; "
            f"rm -f {shlex.quote(self._bridge_pidfile)}; "
            f"else pkill -f {shlex.quote(self._remote_bridge_path)} >/dev/null 2>&1 || true; fi"
        )
        for sandbox in list(self._managed_sandboxes.values()):
            try:
                sandbox.execute(stop_command)
            except Exception:
                _logger.debug("Failed to stop ZeroClaw bridge during teardown", exc_info=True)
        self._managed_sandboxes.clear()
        self._started_sandboxes.clear()


class ZeroClawPodmanRuntimeManager(ZeroClawRuntimeManager):
    """Bootstraps the ZeroClaw bridge inside a Podman-managed container."""

    requires_sandbox = False

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._podman_runtime = config.get("_podman_agent_runtime") or PodmanAgentRuntime()
        self._podman_image = str(
            config.get("podman_image", config.get("image", config.get("rock_image", "zeroclaw-reasoning:0.6.9")))
            or ""
        ).strip()
        self._podman_workdir = str(config.get("podman_workdir", "/workspace") or "/workspace").strip()
        self._podman_network = str(config.get("podman_network", "") or "").strip()
        self._podman_host_ip = str(
            config.get("podman_host_ip", config.get("docker_host_ip", "host.containers.internal"))
        )
        self._podman_result: PodmanAgentRuntimeResult | None = None

    def ensure_ready(self, sandbox: Any = None) -> dict:
        if self._podman_result is not None:
            return self.runtime_info(sandbox)
        spec = self._build_podman_spec()
        _progress(f"starting ZeroClaw Podman bridge image={spec.image}")
        self._podman_result = self._podman_runtime.start(spec)
        self._warmup_podman_gateway(self._podman_result.api_base)
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
                    "adapter_name": "zeroclaw-podman",
                    "logprob_support": self._logprob_support_status(),
                },
            }
        api_base = self._podman_result.api_base
        metadata = dict(self._podman_result.metadata)
        metadata.update({
            "container_engine": "podman",
            "adapter_name": "zeroclaw-podman",
            "logprob_support": self._logprob_support_status(),
        })
        return {
            "sandbox_id": "",
            "gateway_url": f"{api_base.rstrip('/')}/chat/completions",
            "api_base": api_base,
            "gateway_token": self._gateway_token,
            "metadata": metadata,
            "container_engine": "podman",
            "adapter_name": "zeroclaw-podman",
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
        snapshot_paths: list[str] = []
        bridge_log = result.artifacts.get(self._bridge_log_path, "") or result.logs
        if bridge_log:
            workspace_file_contents["zeroclaw_bridge.log"] = bridge_log
        last_request_dir = f"{self._artifact_root.rstrip('/')}/last_request"
        for remote_path, alias in self._last_request_artifact_paths(last_request_dir):
            text = result.artifacts.get(remote_path, "")
            if not text:
                continue
            workspace_file_contents[alias] = text
            snapshot_paths.append(remote_path)
        return {
            "artifact_manifest": {
                "files": {
                    "bridge_log_source": self._bridge_log_path,
                    "artifact_root": self._artifact_root,
                    "bridge_log": "zeroclaw_bridge.log" if bridge_log else "",
                },
                "workspace_snapshot_paths": snapshot_paths,
                "runtime_metadata": dict(result.metadata),
            },
            "gateway_log_excerpt": bridge_log,
            "workspace_snapshot_paths": snapshot_paths,
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
        if not self._podman_image:
            raise RuntimeError("ZeroClaw Podman runtime requires podman_image or image")
        if not self._bridge_template_path.exists():
            raise FileNotFoundError(f"ZeroClaw bridge template not found: {self._bridge_template_path}")
        env = self._runtime_env()
        env.update(podman_proxy_env(os.environ, host_alias=self._podman_host_ip))
        last_request_dir = f"{self._artifact_root.rstrip('/')}/last_request"
        artifact_paths = [self._bridge_log_path]
        artifact_paths.extend(path for path, _ in self._last_request_artifact_paths(last_request_dir))
        return PodmanAgentSpec(
            adapter_name="zeroclaw-podman",
            image=self._podman_image,
            workdir=self._podman_workdir,
            env=env,
            network=self._podman_network or None,
            ports={self._bridge_port: None},
            exposed_port=self._bridge_port,
            startup_timeout=float(self._gateway_startup_timeout),
            request_timeout=float(self._request_timeout),
            cleanup_timeout=30.0,
            install_commands=("command -v zeroclaw >/dev/null 2>&1 && zeroclaw --version",),
            run_command=f"python {shlex.quote(self._remote_bridge_path)}",
            process_log_path=self._bridge_log_path,
            files=(RuntimeFile(self._remote_bridge_path, self._bridge_template_path.read_bytes()),),
            artifact_paths=tuple(artifact_paths),
            api_base_suffix="/v1",
            healthcheck=HTTPHealthcheck(
                path="/models",
                token=self._gateway_token,
                expected_statuses=(200, 404, 405),
                interval_sec=2.0,
                request_timeout_sec=5.0,
            ),
            metadata={
                "adapter_name": "zeroclaw-podman",
                "gateway_token": self._gateway_token,
                "logprob_support": self._logprob_support_status(),
                "artifact_root": self._artifact_root,
            },
        )

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
            "disable_tools": True,
        }
        if self._gateway_warmup_initial_delay > 0:
            time.sleep(self._gateway_warmup_initial_delay)
        deadline = time.monotonic() + self._gateway_warmup_timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                resp = httpx.post(url, headers=headers, json=payload, timeout=120, trust_env=False)
                body = resp.json()
                choices = body.get("choices", [])
                if resp.status_code == 200 and choices:
                    message = choices[0].get("message", {})
                    if isinstance(message, dict) and str(message.get("content", "")).strip():
                        return
                last_error = RuntimeError(f"warmup status={resp.status_code} body={body!r}")
            except Exception as exc:
                last_error = exc
            time.sleep(2)
        if last_error is not None:
            raise RuntimeError(f"ZeroClaw Podman bridge warmup did not succeed: {last_error}") from last_error
        raise RuntimeError("ZeroClaw Podman bridge warmup did not succeed before timeout")

    @staticmethod
    def _last_request_artifact_paths(last_request_dir: str) -> list[tuple[str, str]]:
        return [
            (f"{last_request_dir}/task.txt", "task.txt"),
            (f"{last_request_dir}/zeroclaw_output.txt", "zeroclaw_output.txt"),
            (f"{last_request_dir}/zeroclaw_stderr.log", "zeroclaw_stderr.log"),
            (f"{last_request_dir}/runtime_trace.jsonl", "runtime_trace.jsonl"),
            (f"{last_request_dir}/attachment_manifest.json", "attachment_manifest.json"),
            (f"{last_request_dir}/status.json", "status.json"),
        ]

    def _logprob_support_status(self) -> str:
        if self._logprob_capture.get("enabled"):
            return "unavailable"
        return "disabled"
