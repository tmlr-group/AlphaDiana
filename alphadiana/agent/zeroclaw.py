"""ZeroClaw agent wrapper for standard AlphaDiana benchmark runs."""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from alphadiana.agent.base import Agent, AgentResponse
from alphadiana.agent.registry import AgentRegistry
from alphadiana.agent.zeroclaw_runtime import (
    ZeroClawRuntimeManager,
    _normalize_api_base,
    _resolve_zeroclaw_provider,
)
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.utils.math_answer import extract_answer_candidate

logger = logging.getLogger(__name__)

_DEFAULT_SYSTEM_PROMPT = (
    "You are an expert problem solver. Use the available tools when they help you "
    "verify steps or compute intermediate results. When you have reached your "
    "final answer, you MUST present it at the very end in the form "
    "$$\\boxed{your answer here}$$."
)


def _extract_answer(text: str) -> str:
    return extract_answer_candidate(text)


def _quote_toml(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


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


def _extract_gateway_text(payload: Any) -> str:
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


def _should_fallback_from_gateway(exc: Exception) -> bool:
    try:
        import httpx
    except ImportError:
        httpx = None
    if httpx is not None and isinstance(exc, httpx.TransportError):
        return True
    message = str(exc).lower()
    return "http proxy failed" in message or "post proxy failed" in message


class ZeroClawAgent(Agent):
    """Run ZeroClaw against a benchmark problem.

    Supports:
      - local CLI execution
      - direct CLI execution inside a sandbox
      - OpenClaw-style ROCK proxy execution via a lightweight ZeroClaw bridge
    """

    name = "zeroclaw"

    def setup(self, config: dict) -> None:
        self._model = self._resolve_setting(config, "model", "OPENAI_MODEL_NAME")
        self._provider_api_base = self._resolve_setting(
            config,
            "provider_api_base",
            "OPENAI_BASE_URL",
            default=str(config.get("api_base", "")),
        )
        self._provider_api_key = self._resolve_setting(
            config,
            "provider_api_key",
            "OPENAI_API_KEY",
            default=str(config.get("api_key", "EMPTY")),
        )
        self._request_timeout = int(config.get("request_timeout", 1200))
        self._max_tool_iterations = int(config.get("max_tool_iterations", 100))
        self._max_actions_per_hour = int(config.get("max_actions_per_hour", 200))
        self._workspace_only = bool(config.get("workspace_only", False))
        configured_provider = str(config.get("provider", "")).strip().lower()
        self._provider = _resolve_zeroclaw_provider(configured_provider, self._provider_api_base)
        self._system_prompt = str(config.get("system_prompt", _DEFAULT_SYSTEM_PROMPT)).strip()
        self._install_command = str(config.get("install_command", "")).strip()
        self._timeout_command = str(config.get("timeout_command", "timeout")).strip() or "timeout"
        self._gateway_token = str(config.get("gateway_token", "ZEROCLAW")).strip() or "ZEROCLAW"
        self._use_gateway_in_sandbox = bool(config.get("use_gateway_in_sandbox", True))
        self._gateway_api_base = str(config.get("gateway_api_base", "")).strip()
        gateway_pool_list = config.get("gateway_pool", [])
        if not isinstance(gateway_pool_list, list):
            gateway_pool_list = []
        normalized_gateway_pool = [
            str(url).strip().rstrip("/")
            for url in gateway_pool_list
            if str(url).strip()
        ]
        if not normalized_gateway_pool and self._gateway_api_base:
            normalized_gateway_pool = [self._gateway_api_base.rstrip("/")]
        self._gateway_pool: deque[str] = deque(normalized_gateway_pool)
        self._gateway_pool_lock = threading.Lock()
        self._rock_sandbox_url = str(config.get("rock_sandbox_url", "")).strip()
        self._sandbox_id = str(config.get("sandbox_id", "")).strip()
        if not self._sandbox_id and self._gateway_api_base:
            gateway_sandbox_id, rock_sandbox_url = self._extract_sandbox_target_from_api_base(
                self._gateway_api_base
            )
            if gateway_sandbox_id:
                self._sandbox_id = gateway_sandbox_id
            if rock_sandbox_url and not self._rock_sandbox_url:
                self._rock_sandbox_url = rock_sandbox_url
        raw_env = config.get("env", {})
        self._env = {
            str(key): str(value)
            for key, value in raw_env.items()
            if value is not None
        } if isinstance(raw_env, dict) else {}
        self._runtime_manager = (
            ZeroClawRuntimeManager({
                **config,
                "model": self._model,
                "api_base": self._provider_api_base,
                "api_key": self._provider_api_key,
                "gateway_token": self._gateway_token,
            })
            if self._use_gateway_in_sandbox
            else None
        )

    @staticmethod
    def _resolve_setting(
        config: dict,
        key: str,
        env_var: str,
        *,
        default: str = "",
    ) -> str:
        value = config.get(key, default)
        if value is None:
            value = default
        if isinstance(value, str):
            stripped = value.strip()
            if stripped and stripped.upper() != "EMPTY":
                return stripped
        env_value = os.environ.get(env_var, "").strip()
        if env_value:
            return env_value
        return value if isinstance(value, str) else default

    def _build_prompt(self, task: BenchmarkTask) -> str:
        if self._system_prompt:
            return f"{self._system_prompt}\n\nProblem:\n{task.problem}"
        return task.problem

    def _build_request_messages(self, task: BenchmarkTask) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if self._system_prompt:
            messages.append({"role": "system", "content": self._system_prompt})
        messages.append({"role": "user", "content": task.problem})
        return messages

    def _build_config_toml(self) -> str:
        workspace_only = "true" if self._workspace_only else "false"
        return (
            f"default_provider = {_quote_toml(self._provider)}\n"
            f"default_model = {_quote_toml(self._model)}\n\n"
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
            f"max_actions_per_hour = {self._max_actions_per_hour}\n\n"
            "max_cost_per_day_cents = 10000\n\n"
            "[agent]\n"
            f"max_tool_iterations = {self._max_tool_iterations}\n"
        )

    def _build_env(self, home_dir: str) -> dict[str, str]:
        env = {
            "HOME": home_dir,
            "OPENAI_API_KEY": self._provider_api_key,
            "OPENAI_BASE_URL": self._provider_api_base,
            "OPENAI_MODEL_NAME": self._model,
            "OPENROUTER_API_KEY": self._provider_api_key,
            "ZEROCLAW_API_KEY": self._provider_api_key,
            "ZEROCLAW_PROVIDER": self._provider,
        }
        env.update(self._env)
        return env

    def _ensure_provider_credentials(self, context: str) -> None:
        if not self._provider_api_base:
            raise RuntimeError(
                "ZeroClawAgent requires agent.config.provider_api_base "
                f"(or api_base / OPENAI_BASE_URL) for {context}."
            )
        if not self._provider_api_key:
            raise RuntimeError(
                "ZeroClawAgent requires agent.config.provider_api_key "
                f"(or api_key / OPENAI_API_KEY) for {context}."
            )

    @staticmethod
    def _extract_sandbox_target_from_api_base(api_base: str) -> tuple[str, str]:
        if not api_base:
            return "", ""
        sandbox_id = ""
        rock_sandbox_url = ""
        match = re.search(r"/sandboxes/([A-Za-z0-9_-]+)/proxy", api_base)
        if match:
            sandbox_id = match.group(1)
        match = re.search(r"(https?://[^/]+)/apis/envs/sandbox/v1", api_base)
        if match:
            rock_sandbox_url = f"{match.group(1)}/apis/envs/sandbox/v1"
        return sandbox_id, rock_sandbox_url

    def _ensure_local_binary(self, env: dict[str, str], cwd: str) -> str:
        check = subprocess.run(
            "command -v zeroclaw >/dev/null 2>&1",
            shell=True,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
        )
        if check.returncode != 0:
            if not self._install_command:
                raise RuntimeError(
                    "ZeroClaw binary not found in PATH. Install zeroclaw locally or "
                    "set agent.config.install_command."
                )
            install = subprocess.run(
                self._install_command,
                shell=True,
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
            )
            if install.returncode != 0:
                raise RuntimeError(
                    "Failed to install zeroclaw locally: "
                    f"{install.stderr.strip() or install.stdout.strip() or self._install_command}"
                )

        version = subprocess.run(
            "zeroclaw --version",
            shell=True,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
        )
        if version.returncode != 0:
            raise RuntimeError(
                "zeroclaw --version failed: "
                f"{version.stderr.strip() or version.stdout.strip()}"
            )
        return version.stdout.strip()

    def _ensure_sandbox_binary(self, sandbox: Any, env: dict[str, str]) -> str:
        base_check = sandbox.execute("command -v zeroclaw >/dev/null 2>&1")
        if base_check.exit_code != 0:
            if not self._install_command:
                raise RuntimeError(
                    "ZeroClaw binary not found in sandbox PATH. Build a sandbox image "
                    "with zeroclaw preinstalled or set agent.config.install_command."
                )
            install_command = self._with_env_prefix(self._install_command, env)
            install_result = sandbox.execute(install_command)
            if install_result.exit_code != 0:
                raise RuntimeError(
                    "Failed to install zeroclaw in sandbox: "
                    f"{install_result.stderr.strip() or install_result.stdout.strip() or self._install_command}"
                )

        version_result = sandbox.execute(self._with_env_prefix("zeroclaw --version", env))
        if version_result.exit_code != 0:
            raise RuntimeError(
                "zeroclaw --version failed in sandbox: "
                f"{version_result.stderr.strip() or version_result.stdout.strip()}"
            )
        return version_result.stdout.strip()

    @staticmethod
    def _with_env_prefix(command: str, env: dict[str, str]) -> str:
        prefix = " ".join(f"{key}={shlex.quote(value)}" for key, value in sorted(env.items()))
        return f"{prefix} {command}" if prefix else command

    @staticmethod
    def _wrap_shell_command(command: str, env: dict[str, str]) -> str:
        env_prefix = " ".join(
            f"{key}={shlex.quote(value)}" for key, value in sorted(env.items())
        )
        if env_prefix:
            return f"env {env_prefix} bash -lc {shlex.quote(command)}"
        return f"bash -lc {shlex.quote(command)}"

    def _prepare_paths(self, root_dir: str, execution_id: str) -> dict[str, str]:
        base_dir = str(Path(root_dir) / ".alphadiana_zeroclaw" / execution_id)
        workspace_dir = str(Path(base_dir) / "workspace")
        home_dir = str(Path(base_dir) / "home")
        zc_home_dir = str(Path(home_dir) / ".zeroclaw")
        return {
            "base_dir": base_dir,
            "workspace_dir": workspace_dir,
            "home_dir": home_dir,
            "zc_home_dir": zc_home_dir,
            "config_path": str(Path(zc_home_dir) / "config.toml"),
            "task_path": str(Path(workspace_dir) / "task.txt"),
            "stdout_path": str(Path(base_dir) / "zeroclaw_output.txt"),
            "stderr_path": str(Path(base_dir) / "zeroclaw_stderr.log"),
        }

    def _prepare_sandbox_workspace(
        self,
        sandbox: Any,
        paths: dict[str, str],
        prompt: str,
    ) -> None:
        prep_command = (
            f"mkdir -p {shlex.quote(paths['workspace_dir'])} {shlex.quote(paths['zc_home_dir'])} "
            f"&& ln -sfn {shlex.quote(paths['workspace_dir'])} {shlex.quote(str(Path(paths['zc_home_dir']) / 'workspace'))}"
        )
        prep_result = sandbox.execute(prep_command)
        if prep_result.exit_code != 0:
            raise RuntimeError(
                "Failed to prepare sandbox workspace: "
                f"{prep_result.stderr.strip() or prep_result.stdout.strip()}"
            )
        sandbox.upload(paths["config_path"], self._build_config_toml().encode("utf-8"))
        sandbox.upload(paths["task_path"], prompt.encode("utf-8"))
        chmod_result = sandbox.execute(f"chmod 600 {shlex.quote(paths['config_path'])}")
        if chmod_result.exit_code != 0:
            raise RuntimeError(
                "Failed to lock down sandbox ZeroClaw config: "
                f"{chmod_result.stderr.strip() or chmod_result.stdout.strip()}"
            )

    def _read_sandbox_file(self, sandbox: Any, filename: str) -> str:
        try:
            text = sandbox.read_text(filename)
            if text:
                return text
        except Exception:
            pass
        cat_result = sandbox.execute(f"cat {shlex.quote(filename)}")
        if cat_result.exit_code == 0:
            return cat_result.stdout
        return ""

    def _build_run_command(self, paths: dict[str, str]) -> str:
        return (
            f"cd {shlex.quote(paths['workspace_dir'])} && "
            f"prompt=$(cat {shlex.quote(paths['task_path'])}) && "
            f"{self._timeout_command} {self._request_timeout} "
            f"zeroclaw agent -m \"$prompt\" "
            f"> {shlex.quote(paths['stdout_path'])} "
            f"2> {shlex.quote(paths['stderr_path'])}"
        )

    def _build_diag_command(self, paths: dict[str, str]) -> str:
        return (
            f"echo 'pwd='$(pwd) && "
            f"echo 'home='${{HOME}} && "
            f"echo 'workspace_dir={shlex.quote(paths['workspace_dir'])}' && "
            f"echo 'task_path={shlex.quote(paths['task_path'])}' && "
            f"command -v zeroclaw && "
            f"ls -la {shlex.quote(paths['base_dir'])} && "
            f"ls -la {shlex.quote(paths['zc_home_dir'])} && "
            f"wc -c "
            f"{shlex.quote(paths['task_path'])} "
            f"{shlex.quote(paths['config_path'])} "
            f"{shlex.quote(paths['stdout_path'])} "
            f"{shlex.quote(paths['stderr_path'])} && "
            f"printf '\\n--- task.txt ---\\n' && "
            f"sed -n '1,40p' {shlex.quote(paths['task_path'])} && "
            f"printf '\\n--- config.toml ---\\n' && "
            f"sed -n '1,40p' {shlex.quote(paths['config_path'])} && "
            f"printf '\\n--- stdout ---\\n' && "
            f"sed -n '1,80p' {shlex.quote(paths['stdout_path'])} && "
            f"printf '\\n--- stderr ---\\n' && "
            f"sed -n '1,80p' {shlex.quote(paths['stderr_path'])}"
        )

    def _collect_sandbox_diagnostics(
        self,
        sandbox: Any,
        paths: dict[str, str],
        env: dict[str, str],
    ) -> str:
        diag_command = self._wrap_shell_command(self._build_diag_command(paths), env)
        result = sandbox.execute(diag_command)
        detail = (result.stdout or "").strip()
        if result.stderr.strip():
            detail = f"{detail}\n{result.stderr.strip()}".strip()
        return detail

    def _run_in_sandbox(self, task: BenchmarkTask, sandbox: Any) -> AgentResponse:
        start = time.time()
        prompt = self._build_prompt(task)
        cwd_result = sandbox.execute("pwd")
        if cwd_result.exit_code != 0:
            raise RuntimeError(
                "Failed to resolve sandbox working directory: "
                f"{cwd_result.stderr.strip() or cwd_result.stdout.strip()}"
            )
        root_dir = cwd_result.stdout.strip().splitlines()[-1]
        execution_id = str(task.metadata.get("execution_id") or f"task_{int(start)}")
        paths = self._prepare_paths(root_dir, execution_id)
        self._prepare_sandbox_workspace(sandbox, paths, prompt)

        env = self._build_env(paths["home_dir"])
        version_output = self._ensure_sandbox_binary(sandbox, env)
        run_command = self._wrap_shell_command(self._build_run_command(paths), env)
        execute_long_running = getattr(sandbox, "execute_long_running", None)
        if execute_long_running is not None:
            result = execute_long_running(
                run_command,
                wait_timeout=self._request_timeout + 60,
                wait_interval=10,
            )
        else:
            result = sandbox.execute(run_command)
        raw_output = self._read_sandbox_file(sandbox, paths["stdout_path"]).strip()
        raw_stderr = self._read_sandbox_file(sandbox, paths["stderr_path"]).strip()

        if result.exit_code == 124:
            raise RuntimeError(f"ZeroClaw agent timed out after {self._request_timeout}s")
        if result.exit_code != 0:
            diagnostics = self._collect_sandbox_diagnostics(sandbox, paths, env)
            raise RuntimeError(
                "ZeroClaw agent failed in sandbox: "
                f"{raw_stderr or raw_output or result.stderr.strip() or f'exit code {result.exit_code}'}\n"
                f"diagnostics:\n{diagnostics}"
            )
        if raw_output.lower().startswith("error:"):
            raise RuntimeError(raw_output.splitlines()[0])
        if not raw_output:
            diagnostics = self._collect_sandbox_diagnostics(sandbox, paths, env)
            raise RuntimeError(
                f"ZeroClaw agent produced no output. stderr={raw_stderr}\n"
                f"diagnostics:\n{diagnostics}"
            )

        wall_time = time.time() - start
        answer = _extract_answer(raw_output)
        trajectory = [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": raw_output},
        ]
        return AgentResponse(
            answer=answer,
            trajectory=trajectory,
            raw_output=raw_output,
            wall_time_sec=wall_time,
            workspace_file_contents={
                "task.txt": prompt,
                "zeroclaw_stderr.log": raw_stderr,
            },
            system_prompt=self._system_prompt,
            metadata={
                "model": self._model,
                "provider_api_base": self._provider_api_base,
                "execution_id": execution_id,
                "workspace_dir": paths["workspace_dir"],
                "home_dir": paths["home_dir"],
                "zeroclaw_version": version_output,
                "sandbox_backend": getattr(sandbox, "name", ""),
            },
        )

    def _run_via_gateway_api_base(
        self,
        task: BenchmarkTask,
        api_base: str,
        *,
        sandbox: Any = None,
        runtime_info: dict[str, Any] | None = None,
        artifact_data: dict[str, Any] | None = None,
        gateway_mode: str,
    ) -> AgentResponse:
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError(
                "The 'httpx' package is required for ZeroClaw gateway mode. "
                "Install with: pip install httpx"
            ) from exc

        start = time.time()
        request_messages = self._build_request_messages(task)
        resolved_runtime_info = dict(runtime_info or {})
        resolved_api_base = api_base.rstrip("/")
        actual_sandbox_id, _ = self._extract_sandbox_target_from_api_base(resolved_api_base)
        if actual_sandbox_id:
            resolved_runtime_info["sandbox_id"] = actual_sandbox_id
        url = f"{resolved_api_base}/chat/completions"
        headers = {
            "Authorization": f"bearer {self._gateway_token}",
            "Content-Type": "application/json",
        }
        request_payload = {
            "model": self._model,
            "messages": request_messages,
            "temperature": 0.0,
            "stream": False,
        }

        response = httpx.post(
            url,
            headers=headers,
            json=request_payload,
            timeout=self._request_timeout + 60,
            trust_env=False,
        )
        try:
            response_json = response.json()
        except Exception:
            response_json = {"raw_body": response.text}

        if response.status_code != 200:
            raise RuntimeError(
                "ZeroClaw bridge request failed: "
                f"status={response.status_code} body={response_json!r}"
            )

        raw_output = _extract_gateway_text(response_json)
        if not raw_output:
            raise RuntimeError(f"ZeroClaw bridge returned empty content: {response_json!r}")

        answer = _extract_answer(raw_output)
        wall_time = time.time() - start
        resolved_artifact_data = artifact_data or {
            "artifact_manifest": {},
            "gateway_log_excerpt": "",
            "workspace_snapshot_paths": [],
            "workspace_file_contents": {},
            "sandbox_metadata": {},
        }
        resolved_sandbox_id = str(
            resolved_runtime_info.get("sandbox_id")
            or getattr(sandbox, "sandbox_id", "")
            or self._sandbox_id
        )
        if not resolved_sandbox_id:
            resolved_sandbox_id, _ = self._extract_sandbox_target_from_api_base(resolved_api_base)
        return AgentResponse(
            answer=answer,
            trajectory=[
                {"role": "user", "content": task.problem},
                {"role": "assistant", "content": raw_output},
            ],
            raw_output=raw_output,
            request_messages=request_messages,
            response_json=response_json if isinstance(response_json, dict) else {},
            wall_time_sec=wall_time,
            gateway_url=resolved_runtime_info.get("gateway_url", url),
            sandbox_id=resolved_sandbox_id,
            artifact_manifest=resolved_artifact_data.get("artifact_manifest", {}),
            gateway_log_excerpt=resolved_artifact_data.get("gateway_log_excerpt", ""),
            workspace_snapshot_paths=resolved_artifact_data.get("workspace_snapshot_paths", []),
            workspace_file_contents=resolved_artifact_data.get("workspace_file_contents", {}),
            sandbox_metadata=resolved_artifact_data.get("sandbox_metadata", {}),
            system_prompt=self._system_prompt,
            metadata={
                "model": self._model,
                "provider_api_base": self._provider_api_base,
                "gateway_api_base": resolved_api_base,
                "gateway_mode": gateway_mode,
                "sandbox_backend": getattr(sandbox, "name", ""),
            },
        )

    def _run_via_gateway(self, task: BenchmarkTask, sandbox: Any) -> AgentResponse:
        if self._runtime_manager is None:
            raise RuntimeError("ZeroClaw gateway mode is not configured.")
        runtime_info = self._runtime_manager.ensure_ready(sandbox)
        artifact_data = self._runtime_manager.collect_artifacts(sandbox)
        try:
            return self._run_via_gateway_api_base(
                task,
                runtime_info["api_base"],
                sandbox=sandbox,
                runtime_info=runtime_info,
                artifact_data=artifact_data,
                gateway_mode="rock-proxy",
            )
        except Exception as exc:
            if not _should_fallback_from_gateway(exc):
                raise
            logger.warning(
                "ZeroClaw ROCK proxy failed for sandbox_id=%s; falling back to direct sandbox CLI: %s",
                runtime_info.get("sandbox_id") or getattr(sandbox, "sandbox_id", ""),
                exc,
            )
            response = self._run_in_sandbox(task, sandbox)
            response.gateway_url = runtime_info.get("gateway_url", "")
            response.sandbox_id = str(
                runtime_info.get("sandbox_id")
                or getattr(sandbox, "sandbox_id", "")
            )
            response.artifact_manifest = artifact_data.get("artifact_manifest", {})
            response.gateway_log_excerpt = artifact_data.get("gateway_log_excerpt", "")
            response.workspace_snapshot_paths = artifact_data.get("workspace_snapshot_paths", [])
            response.workspace_file_contents.update(
                artifact_data.get("workspace_file_contents", {})
            )
            response.sandbox_metadata = artifact_data.get("sandbox_metadata", {})
            response.metadata["gateway_mode"] = "rock-proxy-fallback-to-cli"
            response.metadata["gateway_fallback_reason"] = str(exc)
            return response

    def _run_via_predeployed_gateway(self, task: BenchmarkTask) -> AgentResponse:
        resolved_api_base = ""
        used_pool = False
        if self._gateway_pool:
            with self._gateway_pool_lock:
                resolved_api_base = self._gateway_pool[0]
                self._gateway_pool.rotate(-1)
            used_pool = True
        elif self._gateway_api_base:
            resolved_api_base = self._gateway_api_base.rstrip("/")
        if not resolved_api_base:
            raise RuntimeError(
                "ZeroClaw gateway mode requires agent.config.gateway_api_base (or gateway_pool) "
                "when no live sandbox is provided."
            )
        return self._run_via_gateway_api_base(
            task,
            resolved_api_base,
            runtime_info={
                "sandbox_id": self._sandbox_id,
                "gateway_url": f"{resolved_api_base}/chat/completions",
                "api_base": resolved_api_base,
            },
            gateway_mode="gateway-pool" if used_pool else "gateway-api-base",
        )

    def _run_locally(self, task: BenchmarkTask) -> AgentResponse:
        start = time.time()
        prompt = self._build_prompt(task)
        execution_id = str(task.metadata.get("execution_id") or f"task_{int(start)}")

        with tempfile.TemporaryDirectory(prefix=f"alphadiana_zeroclaw_{execution_id}_") as td:
            paths = self._prepare_paths(td, execution_id)
            Path(paths["workspace_dir"]).mkdir(parents=True, exist_ok=True)
            Path(paths["zc_home_dir"]).mkdir(parents=True, exist_ok=True)
            workspace_link = Path(paths["zc_home_dir"]) / "workspace"
            if workspace_link.exists() or workspace_link.is_symlink():
                workspace_link.unlink()
            workspace_link.symlink_to(Path(paths["workspace_dir"]))
            Path(paths["config_path"]).write_text(self._build_config_toml(), encoding="utf-8")
            Path(paths["task_path"]).write_text(prompt, encoding="utf-8")

            env = os.environ.copy()
            env.update(self._build_env(paths["home_dir"]))
            version_output = self._ensure_local_binary(env, paths["workspace_dir"])

            command = self._build_run_command(paths)
            result = subprocess.run(
                command,
                shell=True,
                cwd=paths["workspace_dir"],
                env=env,
                capture_output=True,
                text=True,
            )

            raw_output = Path(paths["stdout_path"]).read_text(encoding="utf-8", errors="replace").strip() if Path(paths["stdout_path"]).exists() else ""
            raw_stderr = Path(paths["stderr_path"]).read_text(encoding="utf-8", errors="replace").strip() if Path(paths["stderr_path"]).exists() else ""

            if result.returncode == 124:
                raise RuntimeError(f"ZeroClaw agent timed out after {self._request_timeout}s")
            if result.returncode != 0:
                raise RuntimeError(
                    "ZeroClaw agent failed locally: "
                    f"{raw_stderr or raw_output or result.stderr.strip() or f'exit code {result.returncode}'}"
                )
            if raw_output.lower().startswith("error:"):
                raise RuntimeError(raw_output.splitlines()[0])
            if not raw_output:
                raise RuntimeError(f"ZeroClaw agent produced no output. stderr={raw_stderr}")

            wall_time = time.time() - start
            answer = _extract_answer(raw_output)
            trajectory = [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": raw_output},
            ]
            return AgentResponse(
                answer=answer,
                trajectory=trajectory,
                raw_output=raw_output,
                wall_time_sec=wall_time,
                workspace_file_contents={
                    "task.txt": prompt,
                    "zeroclaw_stderr.log": raw_stderr,
                },
                system_prompt=self._system_prompt,
                metadata={
                    "model": self._model,
                    "provider_api_base": self._provider_api_base,
                    "execution_id": execution_id,
                    "workspace_dir": paths["workspace_dir"],
                    "home_dir": paths["home_dir"],
                    "zeroclaw_version": version_output,
                    "sandbox_backend": "local-process",
                },
            )

    def solve(self, task: BenchmarkTask, sandbox: Any = None) -> AgentResponse:
        if not self._model:
            raise RuntimeError("ZeroClawAgent requires agent.config.model or OPENAI_MODEL_NAME.")

        if sandbox is not None:
            if (
                self._runtime_manager is not None
                and hasattr(sandbox, "proxy_v1_base")
                and self._runtime_manager.is_configured
            ):
                return self._run_via_gateway(task, sandbox)
            self._ensure_provider_credentials("direct sandbox execution")
            return self._run_in_sandbox(task, sandbox)
        if self._gateway_pool or self._gateway_api_base:
            return self._run_via_predeployed_gateway(task)
        self._ensure_provider_credentials("local execution")
        return self._run_locally(task)

    def teardown(self) -> None:
        if self._runtime_manager is not None:
            self._runtime_manager.teardown()


AgentRegistry.register("zeroclaw", ZeroClawAgent)
