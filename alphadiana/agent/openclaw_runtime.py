"""OpenClaw runtime manager for ROCK-backed execution.

Bootstraps the OpenClaw gateway inside a live ROCK sandbox, waits for
readiness, performs warmup, and collects runtime artifacts (gateway logs,
workspace snapshots) after task execution.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


import logging as _logging

_logger = _logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Hint for slow npm installs in restricted networks.
NPM_TIMEOUT_HINT = (
    "npm install is taking too long. Consider switching to a faster registry:\n"
    "  npm config set registry https://registry.npmmirror.com/\n"
    "See https://npmmirror.com/ for details."
)


def _progress(message: str) -> None:
    print(f"[OpenClaw] {message}", flush=True)


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


class OpenClawRuntimeManager:
    """Bootstraps the OpenClaw gateway inside a live ROCK sandbox."""

    def __init__(self, config: dict) -> None:
        self._gateway_token = config.get("gateway_token", "OPENCLAW")
        self._gateway_model = config.get("model", "openclaw")
        self._rock_agent_config_path = str(self._resolve_config_path(config.get("rock_agent_config_path", ""))) if config.get("rock_agent_config_path") else ""
        self._openclaw_config_path = str(self._resolve_config_path(config.get("openclaw_config_path", ""))) if config.get("openclaw_config_path") else ""
        self._gateway_startup_timeout = int(config.get("gateway_startup_timeout", 180))
        self._gateway_warmup_timeout = int(config.get("gateway_warmup_timeout", 180))
        self._gateway_warmup_initial_delay = float(config.get("gateway_warmup_initial_delay", 5.0))
        self._gateway_log_path = config.get("gateway_log_path", "/tmp/gateway.log")
        self._workspace_path = config.get("workspace_path", "/root/.openclaw/workspace")
        self._remote_openclaw_config_path = config.get("remote_openclaw_config_path", "/tmp/openclaw.json")
        self._started_sandboxes: set[str] = set()
        self._agent_md_applied_sandboxes: set[str] = set()
        self._temp_dirs: list[tempfile.TemporaryDirectory] = []

        # Agent.md customization
        self._agent_md_mode = config.get("agent_md_mode", "none")
        self._agent_md_content = config.get("agent_md_content", "")

        # Embedding configuration
        self._embedding_api_base = config.get("embedding_api_base", "")
        self._embedding_api_key = config.get("embedding_api_key", "")
        self._workspace = config.get("workspace", "")

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

    @property
    def is_configured(self) -> bool:
        return bool(self._rock_agent_config_path and self._openclaw_config_path)

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
        if self._embedding_api_base:
            models = config.setdefault("models", {})
            providers = models.setdefault("providers", {})
            providers["embedding"] = {
                "baseUrl": self._embedding_api_base,
                "apiKey": self._embedding_api_key or "EMPTY",
                "api": "openai",
            }
        if self._workspace:
            agents = config.setdefault("agents", {})
            defaults = agents.setdefault("defaults", {})
            defaults["workspace"] = self._workspace

        # Remove memory.enabled — incompatible with openclaw@2026.3.7
        memory = config.get("memory", {})
        if isinstance(memory, dict):
            memory.pop("enabled", None)
            if not memory:
                config.pop("memory", None)

        return config

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
        _progress("warming up OpenClaw chat completions endpoint")
        try:
            self._warmup_gateway(sandbox)
        except Exception as exc:
            _logger.warning("OpenClaw gateway warmup did not fully succeed; continuing: %s", exc)
            _progress(f"gateway warmup did not fully succeed; continuing: {exc}")
        self._started_sandboxes.add(sandbox_id)
        _progress(f"OpenClaw runtime ready for sandbox_id={sandbox_id}")
        return self.runtime_info(sandbox)

    def runtime_info(self, sandbox: Any) -> dict:
        """Return connection info for the running gateway."""
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

        workspace_listing = sandbox.execute(
            f"find {self._workspace_path} -maxdepth 4 -type f | sort"
        )
        workspace_paths = [
            line.strip()
            for line in workspace_listing.stdout.splitlines()
            if line.strip()
        ]
        workspace_file_contents: dict[str, str] = {}
        for remote_path in workspace_paths:
            try:
                workspace_file_contents[remote_path] = sandbox.read_text(remote_path)
            except Exception:
                continue

        artifact_manifest = {
            "files": {
                "gateway_log_source": self._gateway_log_path,
                "workspace_root": self._workspace_path,
            },
            "workspace_snapshot_paths": workspace_paths,
            "command_history": getattr(sandbox, "command_history", []),
        }
        return {
            "artifact_manifest": artifact_manifest,
            "gateway_log_excerpt": gateway_log,
            "workspace_snapshot_paths": workspace_paths,
            "workspace_file_contents": workspace_file_contents,
            "sandbox_metadata": sandbox.metadata() if hasattr(sandbox, "metadata") else {},
        }

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
        env_cfg = generated_config.setdefault("env", {})
        for key in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL_NAME"):
            local_val = os.environ.get(key, "")
            if local_val:
                env_cfg[key] = local_val
                continue
            current_val = str(env_cfg.get(key, ""))
            if not current_val or re.match(r"^\$\{.+\}$", current_val):
                raise RuntimeError(
                    f"OpenClaw auto-deploy requires {key} to be set in the local environment. "
                    f"Export it before running alphadiana."
                )

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
                response = httpx.post(url, headers=headers, json=payload, timeout=120, trust_env=False)
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

    def teardown(self) -> None:
        """Clean up temporary directories."""
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
