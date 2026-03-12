"""OpenClaw runtime manager for ROCK-backed execution.

Bootstraps the OpenClaw gateway inside a live ROCK sandbox, waits for
readiness, performs warmup, and collects runtime artifacts (gateway logs,
workspace snapshots) after task execution.
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml


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
    """Extract assistant text from a gateway warmup response."""
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
        self._rock_agent_config_path = config.get("rock_agent_config_path", "")
        self._openclaw_config_path = config.get("openclaw_config_path", "")
        self._gateway_startup_timeout = int(config.get("gateway_startup_timeout", 180))
        self._gateway_warmup_timeout = int(config.get("gateway_warmup_timeout", 180))
        self._gateway_warmup_initial_delay = float(config.get("gateway_warmup_initial_delay", 5.0))
        self._gateway_log_path = config.get("gateway_log_path", "/root/gateway.log")
        self._workspace_path = config.get("workspace_path", "/root/.openclaw/workspace")
        self._remote_openclaw_config_path = config.get("remote_openclaw_config_path", "/tmp/openclaw.json")
        self._started_sandboxes: set[str] = set()

        # Agent.md customization
        self._agent_md_mode = config.get("agent_md_mode", "none")
        self._agent_md_content = config.get("agent_md_content", "")

        # Embedding configuration
        self._embedding_api_base = config.get("embedding_api_base", "")
        self._embedding_api_key = config.get("embedding_api_key", "")

    @property
    def is_configured(self) -> bool:
        return bool(self._rock_agent_config_path and self._openclaw_config_path)

    def inject_agent_md(self, sandbox: Any) -> None:
        """Inject or modify AGENTS.md in the sandbox based on mode."""
        agent_md_path = "/root/AGENTS.md"
        if self._agent_md_mode == "append":
            existing = sandbox.read_text(agent_md_path)
            new_content = existing + self._agent_md_content
            sandbox.upload(agent_md_path, new_content.encode("utf-8"))
        elif self._agent_md_mode == "override":
            sandbox.upload(agent_md_path, self._agent_md_content.encode("utf-8"))
        # mode == "none": do nothing

    def _build_openclaw_config(self) -> dict:
        """Build OpenClaw gateway config dict with optional embedding provider."""
        config: dict[str, Any] = {}
        providers: list[dict] = []

        if self._embedding_api_base:
            providers.append({
                "id": "embedding",
                "baseUrl": self._embedding_api_base,
                "apiKey": self._embedding_api_key or "EMPTY",
            })
        else:
            config["memory"] = {"enabled": False}

        if providers:
            config["modelProviders"] = providers

        return config

    def ensure_ready(self, sandbox: Any) -> dict:
        """Ensure OpenClaw gateway is running inside the sandbox.

        Idempotent: reuses if already started for this sandbox_id.
        """
        sandbox_id = str(getattr(sandbox, "sandbox_id", ""))
        if sandbox_id in self._started_sandboxes:
            _progress(f"reusing existing runtime for sandbox_id={sandbox_id}")
            return self.runtime_info(sandbox)

        _progress(f"preparing runtime files for sandbox_id={sandbox_id}")
        config_dir = self._prepare_runtime_files(sandbox)
        _progress(f"installing ROCK agent from config_dir={config_dir}")
        sandbox.install_agent(config_dir=config_dir)
        _progress("starting OpenClaw gateway inside sandbox")
        sandbox.run_agent("", config_dir=config_dir)
        _progress("waiting for OpenClaw gateway readiness")
        self._wait_for_gateway(sandbox)
        _progress("warming up OpenClaw chat completions endpoint")
        self._warmup_gateway(sandbox)
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

        _progress(
            f"uploading OpenClaw config {openclaw_config} to sandbox path {self._remote_openclaw_config_path}"
        )
        sandbox.upload(self._remote_openclaw_config_path, openclaw_config.read_bytes())

        generated_config = yaml.safe_load(rock_agent_config.read_text(encoding="utf-8"))
        generated_config["working_dir"] = None
        generated_config["use_deploy_working_dir_as_fallback"] = False
        generated_config["project_path"] = self._workspace_path
        generated_config["run_cmd"] = (
            f"OPENCLAW_CONFIG_PATH={self._remote_openclaw_config_path} "
            f"nohup openclaw gateway >> {self._gateway_log_path} 2>&1 &"
        )

        config_dir = Path(tempfile.mkdtemp(prefix="alphadiana-openclaw-"))
        generated_path = config_dir / "rock_agent_config.yaml"
        generated_path.write_text(
            yaml.safe_dump(generated_config, sort_keys=False),
            encoding="utf-8",
        )
        _progress(f"generated ROCK agent config at {generated_path}")
        return config_dir

    def _wait_for_gateway(self, sandbox: Any) -> None:
        """Poll /v1/models until the gateway responds."""
        import requests

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
                response = requests.get(url, headers=headers, timeout=10)
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
        import requests

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
                response = requests.post(url, headers=headers, json=payload, timeout=120)
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
