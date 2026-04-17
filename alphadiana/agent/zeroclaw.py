"""ZeroClaw agent wrapper for standard AlphaDiana benchmark runs."""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
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


_MIME_EXTENSION_MAP = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "application/pdf": ".pdf",
    "text/plain": ".txt",
}


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
        raw_temperature = config.get("temperature", 0.0)
        self._temperature = float(raw_temperature if raw_temperature not in ("", None) else 0.0)
        self._runtime_trace_mode = str(config.get("runtime_trace_mode", "none") or "none").strip()
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
        return str(self._build_task_context(task)["prompt"])

    def _build_request_messages(self, task: BenchmarkTask) -> list[dict[str, str]]:
        return list(self._build_task_context(task)["request_messages"])

    def _build_config_toml(self) -> str:
        workspace_only = "true" if self._workspace_only else "false"
        return (
            f"default_provider = {_quote_toml(self._provider)}\n"
            f"default_model = {_quote_toml(self._model)}\n\n"
            f"default_temperature = {self._temperature}\n"
            "model_routes = []\n"
            "embedding_routes = []\n\n"
            "[model_providers]\n\n"
            "[provider]\n\n"
            "[observability]\n"
            'backend = "none"\n'
            f"runtime_trace_mode = {_quote_toml(self._runtime_trace_mode)}\n"
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

    @staticmethod
    def _decode_attachment_mime(value: Any) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="ignore").strip()
        if isinstance(value, str):
            return value.strip()
        return ""

    @classmethod
    def _attachment_filename(cls, key: str, mime: str) -> str:
        candidate = Path(str(key)).name
        if Path(candidate).suffix:
            return candidate
        ext = _MIME_EXTENSION_MAP.get(mime, "")
        if not ext and mime:
            ext = mimetypes.guess_extension(mime) or ""
        safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", candidate).strip("._") or "attachment"
        return f"{safe_stem}{ext}"

    def _build_attachment_items(self, task: BenchmarkTask) -> list[dict[str, Any]]:
        raw_attachments = getattr(task, "attachments", {}) or {}
        if not isinstance(raw_attachments, dict):
            return []

        items: list[dict[str, Any]] = []
        for key in sorted(raw_attachments):
            if key.endswith("_mime"):
                continue
            value = raw_attachments.get(key)
            if value is None:
                continue
            if isinstance(value, bytes):
                data = value
            elif isinstance(value, str):
                data = value.encode("utf-8")
            else:
                continue
            mime = self._decode_attachment_mime(raw_attachments.get(f"{key}_mime"))
            filename = self._attachment_filename(key, mime)
            rel_path = f"attachments/{filename}"
            items.append({
                "key": str(key),
                "mime": mime,
                "filename": filename,
                "rel_path": rel_path,
                "data": data,
            })
        return items

    def _build_problem_text(self, task: BenchmarkTask, attachment_items: list[dict[str, Any]]) -> str:
        problem_text = str(task.problem).rstrip()
        if not attachment_items:
            return problem_text
        attachment_lines = [
            f"- {item['rel_path']}" + (f" ({item['mime']})" if item["mime"] else "")
            for item in attachment_items
        ]
        return (
            f"{problem_text}\n\n"
            "--- Workspace Attachments ---\n"
            "The following files are available relative to the working directory:\n"
            f"{chr(10).join(attachment_lines)}"
        ).rstrip()

    def _build_task_context(self, task: BenchmarkTask) -> dict[str, Any]:
        attachment_items = self._build_attachment_items(task)
        problem_text = self._build_problem_text(task, attachment_items)
        if self._system_prompt:
            prompt = f"{self._system_prompt}\n\nProblem:\n{problem_text}"
        else:
            prompt = problem_text

        request_messages: list[dict[str, str]] = []
        if self._system_prompt:
            request_messages.append({"role": "system", "content": self._system_prompt})
        request_messages.append({"role": "user", "content": problem_text})
        return {
            "prompt": prompt,
            "problem_text": problem_text,
            "request_messages": request_messages,
            "attachments": attachment_items,
        }

    @staticmethod
    def _attachment_manifest(attachment_items: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "attachments": [
                {
                    "key": item["key"],
                    "path": item["rel_path"],
                    "mime": item["mime"],
                    "size_bytes": len(item["data"]),
                }
                for item in attachment_items
            ]
        }

    @staticmethod
    def _serialize_gateway_attachments(attachment_items: list[dict[str, Any]]) -> list[dict[str, str]]:
        payload: list[dict[str, str]] = []
        for item in attachment_items:
            payload.append({
                "key": item["key"],
                "filename": item["filename"],
                "path": item["rel_path"],
                "mime": item["mime"],
                "data_base64": base64.b64encode(item["data"]).decode("ascii"),
            })
        return payload

    @staticmethod
    def _merge_artifact_manifest(*manifests: dict[str, Any] | None) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        for manifest in manifests:
            if not isinstance(manifest, dict):
                continue
            for key, value in manifest.items():
                if isinstance(value, dict) and isinstance(merged.get(key), dict):
                    merged[key] = {**merged[key], **value}
                elif isinstance(value, list) and isinstance(merged.get(key), list):
                    merged[key] = [*merged[key], *value]
                else:
                    merged[key] = value
        return merged

    @staticmethod
    def _read_local_text(path_str: str) -> str:
        path = Path(path_str)
        if not path.exists() or path.is_dir():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")

    def _build_workspace_file_contents(
        self,
        *,
        prompt: str,
        raw_output: str,
        raw_stderr: str,
        runtime_trace: str,
    ) -> dict[str, str]:
        files = {"task.txt": prompt}
        if raw_output:
            files["zeroclaw_output.txt"] = raw_output
        if raw_stderr:
            files["zeroclaw_stderr.log"] = raw_stderr
        if runtime_trace:
            files["runtime_trace.jsonl"] = runtime_trace
        return files

    def _build_cli_response(
        self,
        *,
        prompt: str,
        raw_output: str,
        raw_stderr: str,
        runtime_trace: str,
        attachment_items: list[dict[str, Any]],
        metadata: dict[str, Any],
        wall_time_sec: float,
        system_prompt: str,
        artifact_manifest: dict[str, Any] | None = None,
        request_messages: list[dict[str, Any]] | None = None,
    ) -> AgentResponse:
        combined_output = raw_output or raw_stderr or runtime_trace
        trajectory = [{"role": "user", "content": prompt}]
        if combined_output:
            trajectory.append({"role": "assistant", "content": combined_output})
        manifest = self._merge_artifact_manifest(
            artifact_manifest,
            self._attachment_manifest(attachment_items),
        )
        return AgentResponse(
            answer=_extract_answer(raw_output) if raw_output else None,
            trajectory=trajectory,
            raw_output=combined_output,
            wall_time_sec=wall_time_sec,
            workspace_file_contents=self._build_workspace_file_contents(
                prompt=prompt,
                raw_output=raw_output,
                raw_stderr=raw_stderr,
                runtime_trace=runtime_trace,
            ),
            artifact_manifest=manifest,
            system_prompt=system_prompt,
            metadata=metadata,
            request_messages=list(request_messages or []),
        )

    @staticmethod
    def _raise_with_partial_response(message: str, partial_response: AgentResponse) -> None:
        exc = RuntimeError(message)
        setattr(exc, "partial_response", partial_response)
        raise exc

    def _write_local_attachments(self, attachments_dir: str, attachment_items: list[dict[str, Any]]) -> None:
        if not attachment_items:
            return
        Path(attachments_dir).mkdir(parents=True, exist_ok=True)
        for item in attachment_items:
            (Path(attachments_dir) / item["filename"]).write_bytes(item["data"])

    def _upload_sandbox_attachments(
        self,
        sandbox: Any,
        attachments_dir: str,
        attachment_items: list[dict[str, Any]],
    ) -> None:
        if not attachment_items:
            return
        mkdir_result = sandbox.execute(f"mkdir -p {shlex.quote(attachments_dir)}")
        if mkdir_result.exit_code != 0:
            raise RuntimeError(
                "Failed to prepare sandbox attachment directory: "
                f"{mkdir_result.stderr.strip() or mkdir_result.stdout.strip()}"
            )
        for item in attachment_items:
            sandbox.upload(str(Path(attachments_dir) / item["filename"]), item["data"])

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
        state_dir = str(Path(workspace_dir) / "state")
        attachments_dir = str(Path(workspace_dir) / "attachments")
        return {
            "base_dir": base_dir,
            "workspace_dir": workspace_dir,
            "home_dir": home_dir,
            "zc_home_dir": zc_home_dir,
            "state_dir": state_dir,
            "attachments_dir": attachments_dir,
            "config_path": str(Path(zc_home_dir) / "config.toml"),
            "task_path": str(Path(workspace_dir) / "task.txt"),
            "stdout_path": str(Path(base_dir) / "zeroclaw_output.txt"),
            "stderr_path": str(Path(base_dir) / "zeroclaw_stderr.log"),
            "runtime_trace_path": str(Path(state_dir) / "runtime-trace.jsonl"),
        }

    def _prepare_sandbox_workspace(
        self,
        sandbox: Any,
        paths: dict[str, str],
        task_context: dict[str, Any],
    ) -> None:
        prep_command = (
            f"mkdir -p {shlex.quote(paths['workspace_dir'])} {shlex.quote(paths['zc_home_dir'])} "
            f"{shlex.quote(paths['state_dir'])} {shlex.quote(paths['attachments_dir'])} "
            f"&& ln -sfn {shlex.quote(paths['workspace_dir'])} {shlex.quote(str(Path(paths['zc_home_dir']) / 'workspace'))}"
        )
        prep_result = sandbox.execute(prep_command)
        if prep_result.exit_code != 0:
            raise RuntimeError(
                "Failed to prepare sandbox workspace: "
                f"{prep_result.stderr.strip() or prep_result.stdout.strip()}"
            )
        sandbox.upload(paths["config_path"], self._build_config_toml().encode("utf-8"))
        sandbox.upload(paths["task_path"], str(task_context["prompt"]).encode("utf-8"))
        self._upload_sandbox_attachments(
            sandbox,
            paths["attachments_dir"],
            list(task_context["attachments"]),
        )
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
        probe = f"test -f {shlex.quote(filename)} && cat {shlex.quote(filename)} || true"
        try:
            cat_result = sandbox.execute(f"bash -lc {shlex.quote(probe)}")
        except Exception:
            return ""
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
            f"sed -n '1,80p' {shlex.quote(paths['stderr_path'])} && "
            f"test -f {shlex.quote(paths['runtime_trace_path'])} && "
            f"printf '\\n--- runtime_trace ---\\n' && "
            f"sed -n '1,80p' {shlex.quote(paths['runtime_trace_path'])} || true"
        )

    def _build_gateway_partial_response(
        self,
        *,
        task_context: dict[str, Any],
        artifact_data: dict[str, Any] | None,
        metadata: dict[str, Any],
        wall_time_sec: float,
        sandbox_id: str,
        gateway_url: str,
        response_json: dict[str, Any] | None = None,
    ) -> AgentResponse:
        artifact_payload = dict(artifact_data or {})
        workspace_files = dict(artifact_payload.get("workspace_file_contents", {}) or {})
        raw_output = workspace_files.get("zeroclaw_output.txt", "")
        raw_stderr = workspace_files.get("zeroclaw_stderr.log", "")
        runtime_trace = workspace_files.get("runtime_trace.jsonl", "")
        partial_response = self._build_cli_response(
            prompt=str(task_context["prompt"]),
            raw_output=raw_output,
            raw_stderr=raw_stderr,
            runtime_trace=runtime_trace,
            attachment_items=list(task_context["attachments"]),
            metadata=metadata,
            wall_time_sec=wall_time_sec,
            system_prompt=self._system_prompt,
            artifact_manifest=artifact_payload.get("artifact_manifest", {}),
            request_messages=list(task_context["request_messages"]),
        )
        partial_response.response_json = dict(response_json or {})
        partial_response.gateway_log_excerpt = str(artifact_payload.get("gateway_log_excerpt", "") or "")
        partial_response.workspace_snapshot_paths = list(
            artifact_payload.get("workspace_snapshot_paths", []) or []
        )
        partial_response.workspace_file_contents.update(workspace_files)
        partial_response.sandbox_metadata = dict(artifact_payload.get("sandbox_metadata", {}) or {})
        partial_response.gateway_url = gateway_url
        partial_response.sandbox_id = sandbox_id
        return partial_response

    def _collect_gateway_artifacts_safe(self, sandbox: Any) -> dict[str, Any]:
        if self._runtime_manager is None:
            return {}
        try:
            return self._runtime_manager.collect_artifacts(sandbox)
        except Exception as exc:
            logger.warning("ZeroClaw gateway artifact collection failed: %s", exc)
            return {}

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
        task_context = self._build_task_context(task)
        prompt = str(task_context["prompt"])
        attachment_items = list(task_context["attachments"])
        cwd_result = sandbox.execute("pwd")
        if cwd_result.exit_code != 0:
            raise RuntimeError(
                "Failed to resolve sandbox working directory: "
                f"{cwd_result.stderr.strip() or cwd_result.stdout.strip()}"
            )
        root_dir = cwd_result.stdout.strip().splitlines()[-1]
        execution_id = str(task.metadata.get("execution_id") or f"task_{int(start)}")
        paths = self._prepare_paths(root_dir, execution_id)
        self._prepare_sandbox_workspace(sandbox, paths, task_context)

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
        runtime_trace = self._read_sandbox_file(sandbox, paths["runtime_trace_path"]).strip()
        metadata = {
            "model": self._model,
            "provider_api_base": self._provider_api_base,
            "execution_id": execution_id,
            "workspace_dir": paths["workspace_dir"],
            "home_dir": paths["home_dir"],
            "zeroclaw_version": version_output,
            "sandbox_backend": getattr(sandbox, "name", ""),
        }
        partial_response = self._build_cli_response(
            prompt=prompt,
            raw_output=raw_output,
            raw_stderr=raw_stderr,
            runtime_trace=runtime_trace,
            attachment_items=attachment_items,
            metadata=metadata,
            wall_time_sec=time.time() - start,
            system_prompt=self._system_prompt,
            artifact_manifest={
                "files": {
                    "stdout_source": paths["stdout_path"],
                    "stderr_source": paths["stderr_path"],
                    "runtime_trace_source": paths["runtime_trace_path"],
                }
            },
            request_messages=list(task_context["request_messages"]),
        )

        if result.exit_code == 124:
            self._raise_with_partial_response(
                f"ZeroClaw agent timed out after {self._request_timeout}s",
                partial_response,
            )
        if result.exit_code != 0:
            diagnostics = self._collect_sandbox_diagnostics(sandbox, paths, env)
            self._raise_with_partial_response(
                "ZeroClaw agent failed in sandbox: "
                f"{raw_stderr or raw_output or result.stderr.strip() or f'exit code {result.exit_code}'}\n"
                f"diagnostics:\n{diagnostics}",
                partial_response,
            )
        if raw_output.lower().startswith("error:"):
            self._raise_with_partial_response(raw_output.splitlines()[0], partial_response)
        if not raw_output:
            diagnostics = self._collect_sandbox_diagnostics(sandbox, paths, env)
            self._raise_with_partial_response(
                f"ZeroClaw agent produced no output. stderr={raw_stderr}\n"
                f"diagnostics:\n{diagnostics}",
                partial_response,
            )

        return partial_response

    def _run_via_gateway_api_base(
        self,
        task: BenchmarkTask,
        api_base: str,
        *,
        task_context: dict[str, Any] | None = None,
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
        resolved_task_context = task_context or self._build_task_context(task)
        request_messages = list(resolved_task_context["request_messages"])
        attachment_items = list(resolved_task_context["attachments"])
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
            "temperature": self._temperature,
            "stream": False,
        }
        if attachment_items:
            request_payload["attachments"] = self._serialize_gateway_attachments(attachment_items)

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
        workspace_files = dict(resolved_artifact_data.get("workspace_file_contents", {}) or {})
        workspace_files.setdefault("task.txt", str(resolved_task_context["prompt"]))
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
                {"role": "user", "content": resolved_task_context["problem_text"]},
                {"role": "assistant", "content": raw_output},
            ],
            raw_output=raw_output,
            request_messages=request_messages,
            response_json=response_json if isinstance(response_json, dict) else {},
            wall_time_sec=wall_time,
            gateway_url=resolved_runtime_info.get("gateway_url", url),
            sandbox_id=resolved_sandbox_id,
            artifact_manifest=self._merge_artifact_manifest(
                resolved_artifact_data.get("artifact_manifest", {}),
                self._attachment_manifest(attachment_items),
            ),
            gateway_log_excerpt=resolved_artifact_data.get("gateway_log_excerpt", ""),
            workspace_snapshot_paths=resolved_artifact_data.get("workspace_snapshot_paths", []),
            workspace_file_contents=workspace_files,
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
        task_context = self._build_task_context(task)
        gateway_url = str(runtime_info.get("gateway_url") or f"{runtime_info['api_base']}/chat/completions")
        sandbox_id = str(
            runtime_info.get("sandbox_id")
            or getattr(sandbox, "sandbox_id", "")
            or ""
        )
        try:
            response = self._run_via_gateway_api_base(
                task,
                runtime_info["api_base"],
                task_context=task_context,
                sandbox=sandbox,
                runtime_info=runtime_info,
                gateway_mode="rock-proxy",
            )
            artifact_data = self._collect_gateway_artifacts_safe(sandbox)
            response.artifact_manifest = self._merge_artifact_manifest(
                response.artifact_manifest,
                artifact_data.get("artifact_manifest", {}),
            )
            response.gateway_log_excerpt = artifact_data.get("gateway_log_excerpt", "")
            response.workspace_snapshot_paths = artifact_data.get("workspace_snapshot_paths", [])
            response.workspace_file_contents.update(
                artifact_data.get("workspace_file_contents", {})
            )
            response.sandbox_metadata = artifact_data.get("sandbox_metadata", {})
            return response
        except Exception as exc:
            artifact_data = self._collect_gateway_artifacts_safe(sandbox)
            if not _should_fallback_from_gateway(exc):
                partial_response = self._build_gateway_partial_response(
                    task_context=task_context,
                    artifact_data=artifact_data,
                    metadata={
                        "model": self._model,
                        "provider_api_base": self._provider_api_base,
                        "gateway_api_base": runtime_info["api_base"],
                        "gateway_mode": "rock-proxy",
                        "sandbox_backend": getattr(sandbox, "name", ""),
                    },
                    wall_time_sec=0.0,
                    sandbox_id=sandbox_id,
                    gateway_url=gateway_url,
                )
                self._raise_with_partial_response(str(exc), partial_response)
            logger.warning(
                "ZeroClaw ROCK proxy failed for sandbox_id=%s; falling back to direct sandbox CLI: %s",
                runtime_info.get("sandbox_id") or getattr(sandbox, "sandbox_id", ""),
                exc,
            )
            response = self._run_in_sandbox(task, sandbox)
            response.gateway_url = gateway_url
            response.sandbox_id = sandbox_id
            response.artifact_manifest = self._merge_artifact_manifest(
                response.artifact_manifest,
                artifact_data.get("artifact_manifest", {}),
            )
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
        task_context = self._build_task_context(task)
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
        runtime_info = {
            "sandbox_id": self._sandbox_id,
            "gateway_url": f"{resolved_api_base}/chat/completions",
            "api_base": resolved_api_base,
        }
        try:
            return self._run_via_gateway_api_base(
                task,
                resolved_api_base,
                task_context=task_context,
                runtime_info=runtime_info,
                gateway_mode="gateway-pool" if used_pool else "gateway-api-base",
            )
        except Exception as exc:
            partial_response = self._build_gateway_partial_response(
                task_context=task_context,
                artifact_data=None,
                metadata={
                    "model": self._model,
                    "provider_api_base": self._provider_api_base,
                    "gateway_api_base": resolved_api_base,
                    "gateway_mode": "gateway-pool" if used_pool else "gateway-api-base",
                },
                wall_time_sec=0.0,
                sandbox_id=str(runtime_info.get("sandbox_id", "")),
                gateway_url=str(runtime_info.get("gateway_url", "")),
            )
            self._raise_with_partial_response(str(exc), partial_response)

    def _run_locally(self, task: BenchmarkTask) -> AgentResponse:
        start = time.time()
        task_context = self._build_task_context(task)
        prompt = str(task_context["prompt"])
        attachment_items = list(task_context["attachments"])
        execution_id = str(task.metadata.get("execution_id") or f"task_{int(start)}")

        with tempfile.TemporaryDirectory(prefix=f"alphadiana_zeroclaw_{execution_id}_") as td:
            paths = self._prepare_paths(td, execution_id)
            Path(paths["workspace_dir"]).mkdir(parents=True, exist_ok=True)
            Path(paths["zc_home_dir"]).mkdir(parents=True, exist_ok=True)
            Path(paths["state_dir"]).mkdir(parents=True, exist_ok=True)
            workspace_link = Path(paths["zc_home_dir"]) / "workspace"
            if workspace_link.exists() or workspace_link.is_symlink():
                workspace_link.unlink()
            workspace_link.symlink_to(Path(paths["workspace_dir"]))
            Path(paths["config_path"]).write_text(self._build_config_toml(), encoding="utf-8")
            Path(paths["task_path"]).write_text(prompt, encoding="utf-8")
            self._write_local_attachments(paths["attachments_dir"], attachment_items)

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
            runtime_trace = self._read_local_text(paths["runtime_trace_path"]).strip()
            metadata = {
                "model": self._model,
                "provider_api_base": self._provider_api_base,
                "execution_id": execution_id,
                "workspace_dir": paths["workspace_dir"],
                "home_dir": paths["home_dir"],
                "zeroclaw_version": version_output,
                "sandbox_backend": "local-process",
            }
            partial_response = self._build_cli_response(
                prompt=prompt,
                raw_output=raw_output,
                raw_stderr=raw_stderr,
                runtime_trace=runtime_trace,
                attachment_items=attachment_items,
                metadata=metadata,
                wall_time_sec=time.time() - start,
                system_prompt=self._system_prompt,
                artifact_manifest={
                    "files": {
                        "runtime_trace_source": paths["runtime_trace_path"],
                    }
                },
                request_messages=list(task_context["request_messages"]),
            )

            if result.returncode == 124:
                self._raise_with_partial_response(
                    f"ZeroClaw agent timed out after {self._request_timeout}s",
                    partial_response,
                )
            if result.returncode != 0:
                self._raise_with_partial_response(
                    "ZeroClaw agent failed locally: "
                    f"{raw_stderr or raw_output or result.stderr.strip() or f'exit code {result.returncode}'}",
                    partial_response,
                )
            if raw_output.lower().startswith("error:"):
                self._raise_with_partial_response(raw_output.splitlines()[0], partial_response)
            if not raw_output:
                self._raise_with_partial_response(
                    f"ZeroClaw agent produced no output. stderr={raw_stderr}",
                    partial_response,
                )
            return partial_response

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
