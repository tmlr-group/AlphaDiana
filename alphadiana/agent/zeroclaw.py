"""ZeroClaw agent wrapper for standard AlphaDiana benchmark runs."""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

from alphadiana.agent.logprob_capture import (
    extract_openai_logprob_records,
    finalize_logprob_capture,
    resolve_logprob_capture_config,
)
from alphadiana.agent.base import Agent, AgentResponse
from alphadiana.agent.preservation import (
    add_artifact_file_refs,
    build_event_trajectories,
    build_runtime_trace_summary,
    build_text_step_trajectories,
    parse_jsonl_records,
)
from alphadiana.agent.registry import AgentRegistry
from alphadiana.agent.zeroclaw_runtime import _resolve_zeroclaw_provider
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.utils.math_answer import extract_answer_candidate

logger = logging.getLogger(__name__)

_DEFAULT_SYSTEM_PROMPT = (
    "You are an expert problem solver. Use the available tools when they help you "
    "verify steps or compute intermediate results. When you have reached your "
    "final answer, you MUST present it at the very end in the form "
    "$$\\boxed{your answer here}$$."
)

_DEFAULT_ALLOWED_COMMANDS = [
    "git",
    "npm",
    "cargo",
    "ls",
    "cat",
    "grep",
    "find",
    "echo",
    "pwd",
    "wc",
    "head",
    "tail",
    "date",
    "python",
    "python3",
    "bash",
    "sh",
    "sed",
    "awk",
    "mkdir",
    "mv",
    "cp",
    "rm",
]

_UNSUPPORTED_SINGLE_PATH_KEYS = (
    "disable_tools",
    "multimodal_via_proxy",
    "use_gateway_in_sandbox",
    "gateway_api_base",
    "gateway_pool",
    "gateway_token",
    "rock_sandbox_url",
    "sandbox_id",
)

_MIME_EXTENSION_MAP = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "application/pdf": ".pdf",
    "text/plain": ".txt",
}

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
LOG_LINE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T[0-9:.]+Z\s+(INFO|WARN|ERROR)\b")
_RUNTIME_LOG_PREFIXES = (
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
_DIFF_GIT_RE = re.compile(
    r"^diff --git .+?(?=\n(?:diff --git |\Z))",
    re.MULTILINE | re.DOTALL,
)


def _extract_answer(text: str) -> str:
    return extract_answer_candidate(text)


def _parse_optional_bool(value: Any) -> bool | None:
    if value in (None, ""):
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


def _sanitize_cli_output(raw_output: str) -> tuple[str, list[str]]:
    cleaned_lines: list[str] = []
    dropped_runtime_logs: list[str] = []
    for raw_line in raw_output.splitlines():
        line = ANSI_RE.sub("", raw_line).rstrip()
        if _is_runtime_log_line(line):
            dropped_runtime_logs.append(line)
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip(), dropped_runtime_logs


def _quote_toml(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _classify_cli_error_output(text: str) -> str:
    normalized = str(text or "").strip().lower()
    if "proxy_error" in normalized:
        return "proxy_error"
    provider_markers = (
        "all providers/models failed",
        "provider",
        "rate_limited",
        "rate limited",
        "too many requests",
        "api error",
        "bad gateway",
        "authentication",
        "unauthorized",
        "forbidden",
        "capability=vision",
        "contextoverflowerror",
        "vllmvalidationerror",
        "maximum context length",
        "input_tokens",
        "max context",
    )
    if any(marker in normalized for marker in provider_markers):
        return "provider_error"
    return "cli_error"


def _extract_patch_from_text(text: str) -> str:
    """Extract a unified diff patch from free-form agent output."""
    candidate = _extract_answer(text)
    if candidate and ("diff --git" in candidate or ("---" in candidate and "+++" in candidate)):
        return candidate.strip()

    matches = _DIFF_GIT_RE.findall(text)
    if matches:
        return "\n".join(match.strip() for match in matches).strip()

    fenced_re = re.compile(r"```(?:diff)?\s*\n(.*?)```", re.DOTALL)
    for match in fenced_re.finditer(text):
        block = match.group(1).strip()
        if "diff --git" in block or ("---" in block and "+++" in block):
            return block
    return ""


def _is_swe_bench_task(task: BenchmarkTask) -> bool:
    """Return whether *task* comes from a SWE-bench-style dataset."""
    metadata = getattr(task, "metadata", None) or {}
    return "instance_id" in metadata or "repo" in metadata


def extract_zeroclaw_logprob_records(
    *,
    runtime_records: list[dict[str, Any]],
    runtime_trace: str,
    raw_output: str,
    raw_stderr: str,
) -> list[dict]:
    """Extract OpenAI-shaped provider logprobs from ZeroClaw runtime artifacts."""

    def _jsonl_objects(text: str) -> list[dict[str, Any]]:
        objects: list[dict[str, Any]] = []
        for raw_line in (text or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                objects.append(payload)
        return objects

    records: list[dict] = []
    token_index = 0
    seen_payloads: set[str] = set()

    def _scan(node: Any) -> None:
        nonlocal token_index
        if isinstance(node, dict):
            payload_fingerprint = ""
            if "choices" in node:
                try:
                    payload_fingerprint = json.dumps(node, sort_keys=True, ensure_ascii=False)
                except (TypeError, ValueError):
                    payload_fingerprint = ""
            should_extract = not payload_fingerprint or payload_fingerprint not in seen_payloads
            if payload_fingerprint:
                seen_payloads.add(payload_fingerprint)
            if should_extract:
                parsed, token_index = extract_openai_logprob_records(
                    node,
                    start_index=token_index,
                )
                records.extend(parsed)
            for value in node.values():
                _scan(value)
            return
        if isinstance(node, list):
            for entry in node:
                _scan(entry)

    for record in runtime_records or []:
        _scan(record)
    for payload in _jsonl_objects(runtime_trace):
        _scan(payload)
    for payload in _jsonl_objects(raw_output):
        _scan(payload)
    for payload in _jsonl_objects(raw_stderr):
        _scan(payload)
    return records


class ZeroClawAgent(Agent):
    """Run ZeroClaw in a live sandbox/container using its native CLI path."""

    name = "zeroclaw"

    def setup(self, config: dict) -> None:
        unsupported = [key for key in _UNSUPPORTED_SINGLE_PATH_KEYS if key in config]
        if unsupported:
            unsupported_csv = ", ".join(sorted(unsupported))
            raise ValueError(
                "ZeroClawAgent now supports only the native sandbox CLI path. "
                f"Unsupported config keys: {unsupported_csv}"
            )

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
        self._logprob_capture = resolve_logprob_capture_config(config)
        raw_temperature = config.get("temperature", 0.0)
        self._temperature = float(raw_temperature if raw_temperature not in ("", None) else 0.0)
        runtime_trace_mode = str(config.get("runtime_trace_mode", "none") or "none").strip()
        if self._logprob_capture["enabled"] and runtime_trace_mode.lower() in {"", "none"}:
            runtime_trace_mode = "all"
        self._runtime_trace_mode = runtime_trace_mode
        self._request_timeout = int(config.get("request_timeout", 1200))
        raw_provider_timeout = config.get("provider_timeout_secs", None)
        if raw_provider_timeout in ("", None):
            self._provider_timeout_secs = self._request_timeout
        else:
            self._provider_timeout_secs = int(raw_provider_timeout)
        raw_provider_max_tokens = config.get("provider_max_tokens", None)
        if raw_provider_max_tokens in ("", None):
            self._provider_max_tokens: int | None = None
        else:
            self._provider_max_tokens = int(raw_provider_max_tokens)
        self._reasoning_enabled = _parse_optional_bool(config.get("reasoning_enabled", None))
        raw_reasoning_effort = config.get("reasoning_effort", None)
        if raw_reasoning_effort in ("", None):
            self._reasoning_effort: str | None = None
        else:
            self._reasoning_effort = str(raw_reasoning_effort).strip()
        self._security_sandbox_enabled = _parse_optional_bool(
            config.get("security_sandbox_enabled", None)
        )
        raw_security_sandbox_backend = config.get("security_sandbox_backend", None)
        if raw_security_sandbox_backend in ("", None):
            self._security_sandbox_backend: str | None = None
        else:
            self._security_sandbox_backend = str(raw_security_sandbox_backend).strip()
        self._max_tool_iterations = int(config.get("max_tool_iterations", 100))
        self._max_actions_per_hour = int(config.get("max_actions_per_hour", 200))
        self._workspace_only = bool(config.get("workspace_only", False))
        configured_allowed_commands = config.get("allowed_commands")
        configured_extra_allowed_commands = config.get("extra_allowed_commands")
        self._allowed_commands = self._resolve_allowed_commands(
            configured_allowed_commands,
            configured_extra_allowed_commands,
        )
        self._binary_source_image = str(
            config.get(
                "binary_source_image",
                os.environ.get("ZEROCLAW_BINARY_SOURCE_IMAGE")
                or os.environ.get("SWEBENCH_ZEROCLAW_RUNTIME_IMAGE")
                or "zeroclaw-reasoning:0.6.9",
            )
            or ""
        ).strip()
        self._binary_source_path = str(
            config.get("binary_source_path", "/usr/local/bin/zeroclaw") or "/usr/local/bin/zeroclaw"
        ).strip()
        self._host_binary_cache: bytes | None = None
        configured_provider = str(config.get("provider", "")).strip().lower()
        self._provider = _resolve_zeroclaw_provider(configured_provider, self._provider_api_base)
        self._system_prompt = str(config.get("system_prompt", _DEFAULT_SYSTEM_PROMPT)).strip()
        self._install_command = str(config.get("install_command", "")).strip()
        self._timeout_command = str(config.get("timeout_command", "timeout")).strip() or "timeout"
        raw_env = config.get("env", {})
        self._env = {
            str(key): str(value)
            for key, value in raw_env.items()
            if value is not None
        } if isinstance(raw_env, dict) else {}

    @staticmethod
    def _resolve_allowed_commands(
        allowed_commands: Any,
        extra_allowed_commands: Any,
    ) -> list[str]:
        commands: list[str] = []

        def _extend(raw: Any) -> None:
            if raw is None:
                return
            if isinstance(raw, (list, tuple, set)):
                items = raw
            else:
                items = str(raw).replace("\n", ",").split(",")
            for item in items:
                text = str(item).strip()
                if text and text not in commands:
                    commands.append(text)

        if allowed_commands is None:
            _extend(_DEFAULT_ALLOWED_COMMANDS)
        else:
            _extend(allowed_commands)
        _extend(extra_allowed_commands)
        return commands

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
            if not data:
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
                "is_image": mime.lower().startswith("image/"),
            })
        return items

    def _build_problem_text(
        self,
        task: BenchmarkTask,
        attachment_items: list[dict[str, Any]],
        *,
        image_marker_paths: list[str] | None = None,
    ) -> str:
        problem_text = str(task.problem).rstrip()
        if attachment_items:
            attachment_lines = [
                f"- {item['rel_path']}" + (f" ({item['mime']})" if item["mime"] else "")
                for item in attachment_items
            ]
            problem_text = (
                f"{problem_text}\n\n"
                "--- Workspace Attachments ---\n"
                "The following files are available relative to the working directory:\n"
                f"{chr(10).join(attachment_lines)}"
            ).rstrip()

        marker_paths = [path for path in list(image_marker_paths or []) if path]
        if marker_paths:
            marker_block = "\n".join(f"[IMAGE:{path}]" for path in marker_paths)
            problem_text = (
                f"{problem_text}\n\n"
                "--- Image Inputs ---\n"
                f"{marker_block}"
            ).rstrip()
        return problem_text

    def _build_task_context(
        self,
        task: BenchmarkTask,
        attachment_items: list[dict[str, Any]],
        *,
        image_marker_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        problem_text = self._build_problem_text(
            task,
            attachment_items,
            image_marker_paths=image_marker_paths,
        )
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
                    "filename": item["filename"],
                    "mime": item["mime"],
                    "size_bytes": len(item["data"]),
                }
                for item in attachment_items
            ]
        }

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

    def _apply_text_fallback_trajectory(
        self,
        *,
        request_messages: list[dict[str, Any]],
        output_text: str,
        current_trajectory: list[dict[str, Any]],
        current_reasoning: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        fallback_trajectory, fallback_reasoning = build_text_step_trajectories(
            request_messages,
            output_text,
        )
        if len(fallback_trajectory) > len(current_trajectory):
            current_trajectory = fallback_trajectory
        if len(fallback_reasoning) > len(current_reasoning):
            current_reasoning = fallback_reasoning
        return current_trajectory, current_reasoning

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
        answer_override: str | None = None,
    ) -> AgentResponse:
        sanitized_output, dropped_runtime_logs = _sanitize_cli_output(raw_output)
        combined_output = sanitized_output or raw_stderr or runtime_trace
        runtime_records = parse_jsonl_records(runtime_trace)
        trajectory, reasoning_trajectory = build_event_trajectories(
            list(request_messages or []),
            runtime_records,
            final_output=combined_output,
        )
        if not runtime_records:
            trajectory, reasoning_trajectory = self._apply_text_fallback_trajectory(
                request_messages=list(request_messages or []),
                output_text=combined_output,
                current_trajectory=trajectory,
                current_reasoning=reasoning_trajectory,
            )
        if not trajectory:
            trajectory = [{"role": "user", "content": prompt}]
            if combined_output:
                trajectory.append({"role": "assistant", "content": combined_output})

        manifest = self._merge_artifact_manifest(
            artifact_manifest,
            self._attachment_manifest(attachment_items),
        )
        manifest = add_artifact_file_refs(
            manifest,
            response_stream=(
                "runtime_trace.jsonl"
                if runtime_trace
                else ("zeroclaw_output.txt" if raw_output else None)
            ),
            zeroclaw_output="zeroclaw_output.txt" if raw_output else None,
            stdout_log="zeroclaw_output.txt" if raw_output else None,
            stderr_log="zeroclaw_stderr.log" if raw_stderr else None,
            prompt_text="task.txt",
            attachment_manifest="attachment_manifest.json" if attachment_items else None,
        )

        response_metadata = dict(metadata)
        if dropped_runtime_logs:
            response_metadata["runtime_logs_dropped_from_output"] = dropped_runtime_logs
        if sanitized_output != raw_output:
            response_metadata["raw_output_sanitized"] = True
        logprob_records = extract_zeroclaw_logprob_records(
            runtime_records=runtime_records,
            runtime_trace=runtime_trace,
            raw_output=raw_output,
            raw_stderr=raw_stderr,
        )
        token_entropy_stats, response_metadata = finalize_logprob_capture(
            harness="zeroclaw",
            enabled=self._logprob_capture["enabled"],
            records=logprob_records,
            metadata=response_metadata,
        )

        workspace_files = self._build_workspace_file_contents(
            prompt=prompt,
            raw_output=raw_output,
            raw_stderr=raw_stderr,
            runtime_trace=runtime_trace,
        )
        if attachment_items:
            workspace_files["attachment_manifest.json"] = json.dumps(
                self._attachment_manifest(attachment_items),
                indent=2,
                ensure_ascii=False,
            )

        return AgentResponse(
            answer=answer_override if answer_override is not None else (
                _extract_answer(sanitized_output) if sanitized_output else None
            ),
            trajectory=trajectory,
            reasoning_trajectory=reasoning_trajectory,
            raw_output=combined_output,
            wall_time_sec=wall_time_sec,
            token_entropy_stats=token_entropy_stats,
            workspace_file_contents=workspace_files,
            artifact_manifest=manifest,
            system_prompt=system_prompt,
            metadata=response_metadata,
            request_messages=list(request_messages or []),
            response_json=build_runtime_trace_summary(
                output_text=sanitized_output or combined_output,
                stderr_text=raw_stderr.strip(),
                records=runtime_records,
                extra={"runtime_trace_present": bool(runtime_records)},
            ),
        )

    @staticmethod
    def _raise_with_partial_response(
        message: str,
        partial_response: AgentResponse,
        *,
        error_type: str | None = None,
    ) -> None:
        exc = RuntimeError(message)
        setattr(exc, "partial_response", partial_response)
        if error_type:
            setattr(exc, "error_type", error_type)
        raise exc

    @staticmethod
    def _mark_partial_failure(
        partial_response: AgentResponse,
        *,
        failure_reason: str,
        extra_metadata: dict[str, Any] | None = None,
        response_json_extra: dict[str, Any] | None = None,
    ) -> AgentResponse:
        metadata = dict(partial_response.metadata or {})
        metadata["failure_reason"] = failure_reason
        if extra_metadata:
            metadata.update(extra_metadata)
        partial_response.metadata = metadata
        if response_json_extra:
            merged_response_json = dict(partial_response.response_json or {})
            merged_response_json.update(response_json_extra)
            partial_response.response_json = merged_response_json
        return partial_response

    def _build_config_toml(self) -> str:
        workspace_only = "true" if self._workspace_only else "false"
        allowed_commands = json.dumps(self._allowed_commands, ensure_ascii=False)
        provider_max_tokens = ""
        if self._provider_max_tokens is not None:
            provider_max_tokens = f"provider_max_tokens = {self._provider_max_tokens}\n"
        runtime_section = ""
        if self._reasoning_enabled is not None or self._reasoning_effort is not None:
            runtime_lines = ["[runtime]"]
            if self._reasoning_enabled is not None:
                runtime_lines.append(
                    f"reasoning_enabled = {str(self._reasoning_enabled).lower()}"
                )
            if self._reasoning_effort:
                runtime_lines.append(
                    f"reasoning_effort = {_quote_toml(self._reasoning_effort)}"
                )
            runtime_section = "\n".join(runtime_lines) + "\n\n"
        security_section = ""
        if (
            self._security_sandbox_enabled is not None
            or self._security_sandbox_backend is not None
        ):
            security_lines = ["[security.sandbox]"]
            if self._security_sandbox_enabled is not None:
                security_lines.append(
                    f"enabled = {str(self._security_sandbox_enabled).lower()}"
                )
            if self._security_sandbox_backend:
                security_lines.append(
                    f"backend = {_quote_toml(self._security_sandbox_backend)}"
                )
            security_section = "\n".join(security_lines) + "\n\n"
        logprob_provider_options = ""
        if self._logprob_capture["enabled"]:
            logprob_provider_options = (
                "[model_providers.custom.options]\n"
                "logprobs = true\n"
                f"top_logprobs = {self._logprob_capture['top_logprobs']}\n\n"
            )
        return (
            f"default_provider = {_quote_toml(self._provider)}\n"
            f"default_model = {_quote_toml(self._model)}\n\n"
            f"default_temperature = {self._temperature}\n"
            f"provider_timeout_secs = {self._provider_timeout_secs}\n"
            f"{provider_max_tokens}"
            "model_routes = []\n"
            "embedding_routes = []\n\n"
            "[model_providers]\n\n"
            f"{logprob_provider_options}"
            f"{runtime_section}"
            f"{security_section}"
            "[observability]\n"
            'backend = "none"\n'
            f"runtime_trace_mode = {_quote_toml(self._runtime_trace_mode)}\n"
            'runtime_trace_path = "state/runtime-trace.jsonl"\n'
            "runtime_trace_max_entries = 200\n\n"
            "[autonomy]\n"
            'level = "full"\n'
            f"workspace_only = {workspace_only}\n"
            f"allowed_commands = {allowed_commands}\n"
            'forbidden_paths = ["/etc", "/usr", "/bin", "/sbin", "/lib", "/opt", "/boot", "/dev", "/proc", "/sys"]\n'
            f"max_actions_per_hour = {self._max_actions_per_hour}\n\n"
            "max_cost_per_day_cents = 10000\n\n"
            "[agent]\n"
            f"max_tool_iterations = {self._max_tool_iterations}\n"
        )

    def _build_env(self, home_dir: str) -> dict[str, str]:
        env = {
            "HOME": home_dir,
            "PATH": f"{home_dir}/bin:/usr/local/bin:/usr/bin:/bin",
            "OPENAI_API_KEY": self._provider_api_key,
            "OPENAI_BASE_URL": self._provider_api_base,
            "OPENAI_MODEL_NAME": self._model,
            "OPENROUTER_API_KEY": self._provider_api_key,
            "ZEROCLAW_API_KEY": self._provider_api_key,
            "ZEROCLAW_PROVIDER": self._provider,
        }
        env.update(self._env)
        return env

    def _load_host_binary_from_image(self) -> bytes:
        if self._host_binary_cache is not None:
            return self._host_binary_cache
        if not self._binary_source_image:
            raise RuntimeError("No ZeroClaw binary source image configured.")
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "cat",
                self._binary_source_image,
                self._binary_source_path,
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if not result.stdout:
            raise RuntimeError(
                "Resolved ZeroClaw binary source image produced an empty binary payload."
            )
        self._host_binary_cache = result.stdout
        return result.stdout

    def _upload_host_binary(
        self,
        sandbox: Any,
        paths: dict[str, str],
    ) -> bool:
        try:
            binary_bytes = self._load_host_binary_from_image()
        except Exception as exc:
            logger.debug("Failed to read ZeroClaw binary from image %s: %s", self._binary_source_image, exc)
            return False

        mkdir_result = sandbox.execute(
            f"mkdir -p {shlex.quote(paths['bin_dir'])}"
        )
        if mkdir_result.exit_code != 0:
            logger.debug(
                "Failed to create sandbox bin dir %s: %s",
                paths["bin_dir"],
                mkdir_result.stderr.strip() or mkdir_result.stdout.strip(),
            )
            return False
        try:
            sandbox.upload(paths["zeroclaw_path"], binary_bytes)
        except Exception as exc:
            logger.debug("Failed to upload ZeroClaw binary into sandbox: %s", exc)
            return False
        chmod_result = sandbox.execute(f"chmod 755 {shlex.quote(paths['zeroclaw_path'])}")
        if chmod_result.exit_code != 0:
            logger.debug(
                "Failed to chmod uploaded ZeroClaw binary: %s",
                chmod_result.stderr.strip() or chmod_result.stdout.strip(),
            )
            return False
        return True

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

    def _ensure_sandbox_binary(
        self,
        sandbox: Any,
        env: dict[str, str],
        paths: dict[str, str],
    ) -> str:
        base_check = sandbox.execute("command -v zeroclaw >/dev/null 2>&1")
        if base_check.exit_code != 0:
            uploaded = self._upload_host_binary(sandbox, paths)
            if not uploaded and not self._install_command:
                raise RuntimeError(
                    "ZeroClaw binary not found in sandbox PATH. "
                    "Set agent.config.binary_source_image to a local runtime image with "
                    "zeroclaw installed, or provide agent.config.install_command."
                )
            if not self._install_command:
                pass
            else:
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
            "bin_dir": str(Path(home_dir) / "bin"),
            "zc_home_dir": zc_home_dir,
            "state_dir": state_dir,
            "attachments_dir": attachments_dir,
            "config_path": str(Path(zc_home_dir) / "config.toml"),
            "task_path": str(Path(workspace_dir) / "task.txt"),
            "zeroclaw_path": str(Path(home_dir) / "bin" / "zeroclaw"),
            "stdout_path": str(Path(base_dir) / "zeroclaw_output.txt"),
            "stderr_path": str(Path(base_dir) / "zeroclaw_stderr.log"),
            "runtime_trace_path": str(Path(state_dir) / "runtime-trace.jsonl"),
        }

    @staticmethod
    def _resolve_repo_workdir(sandbox: Any, root_dir: str) -> str:
        metadata_fn = getattr(sandbox, "metadata", None)
        if callable(metadata_fn):
            try:
                metadata = metadata_fn()
            except Exception:
                metadata = {}
            if isinstance(metadata, dict):
                repo_workdir = str(metadata.get("repo_workdir", "")).strip()
                if repo_workdir:
                    return repo_workdir
        return root_dir

    @staticmethod
    def _collect_repo_patch(sandbox: Any, repo_workdir: str) -> str:
        if not repo_workdir:
            return ""
        try:
            result = sandbox.execute(f"cd {shlex.quote(repo_workdir)} && git diff HEAD 2>/dev/null")
        except Exception:
            return ""
        if result.exit_code != 0:
            return ""
        return result.stdout.strip()

    def _prepare_sandbox_workspace(
        self,
        sandbox: Any,
        paths: dict[str, str],
        task_context: dict[str, Any],
    ) -> None:
        prep_command = (
            f"mkdir -p {shlex.quote(paths['workspace_dir'])} {shlex.quote(paths['zc_home_dir'])} "
            f"{shlex.quote(paths['state_dir'])} {shlex.quote(paths['attachments_dir'])} "
            f"&& ln -sfn {shlex.quote(paths['workspace_dir'])} "
            f"{shlex.quote(str(Path(paths['zc_home_dir']) / 'workspace'))}"
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
            "echo 'pwd='$(pwd) && "
            "echo 'home='${HOME} && "
            f"echo 'workspace_dir={shlex.quote(paths['workspace_dir'])}' && "
            f"echo 'task_path={shlex.quote(paths['task_path'])}' && "
            "command -v zeroclaw && "
            f"ls -la {shlex.quote(paths['base_dir'])} && "
            f"ls -la {shlex.quote(paths['zc_home_dir'])} && "
            "wc -c "
            f"{shlex.quote(paths['task_path'])} "
            f"{shlex.quote(paths['config_path'])} "
            f"{shlex.quote(paths['stdout_path'])} "
            f"{shlex.quote(paths['stderr_path'])} && "
            "printf '\\n--- task.txt ---\\n' && "
            f"sed -n '1,80p' {shlex.quote(paths['task_path'])} && "
            "printf '\\n--- config.toml ---\\n' && "
            f"sed -n '1,80p' {shlex.quote(paths['config_path'])} && "
            "printf '\\n--- stdout ---\\n' && "
            f"sed -n '1,80p' {shlex.quote(paths['stdout_path'])} && "
            "printf '\\n--- stderr ---\\n' && "
            f"sed -n '1,80p' {shlex.quote(paths['stderr_path'])} && "
            f"test -f {shlex.quote(paths['runtime_trace_path'])} && "
            "printf '\\n--- runtime_trace ---\\n' && "
            f"sed -n '1,80p' {shlex.quote(paths['runtime_trace_path'])} || true"
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

    def _collect_sandbox_diagnostics_safe(
        self,
        sandbox: Any,
        paths: dict[str, str],
        env: dict[str, str],
    ) -> str:
        try:
            return self._collect_sandbox_diagnostics(sandbox, paths, env)
        except Exception as exc:
            return f"sandbox diagnostics unavailable: {exc}"

    def _run_in_sandbox(self, task: BenchmarkTask, sandbox: Any) -> AgentResponse:
        start = time.time()
        attachment_items = self._build_attachment_items(task)
        cwd_result = sandbox.execute("pwd")
        if cwd_result.exit_code != 0:
            raise RuntimeError(
                "Failed to resolve sandbox working directory: "
                f"{cwd_result.stderr.strip() or cwd_result.stdout.strip()}"
            )
        root_dir = cwd_result.stdout.strip().splitlines()[-1]
        execution_id = str(task.metadata.get("execution_id") or f"task_{int(start)}")
        paths = self._prepare_paths(root_dir, execution_id)
        image_marker_paths = [
            str(Path(paths["attachments_dir"]) / item["filename"])
            for item in attachment_items
            if bool(item.get("is_image"))
        ]
        task_context = self._build_task_context(
            task,
            attachment_items,
            image_marker_paths=image_marker_paths,
        )
        prompt = str(task_context["prompt"])

        self._prepare_sandbox_workspace(sandbox, paths, task_context)

        env = self._build_env(paths["home_dir"])
        version_output = self._ensure_sandbox_binary(sandbox, env, paths)
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
        sanitized_output, dropped_runtime_logs = _sanitize_cli_output(raw_output)
        repo_workdir = self._resolve_repo_workdir(sandbox, root_dir)
        patch_answer: str | None = None
        patch_source = ""
        if _is_swe_bench_task(task):
            patch_from_git = self._collect_repo_patch(sandbox, repo_workdir)
            if patch_from_git:
                patch_answer = patch_from_git
                patch_source = "git_diff"
            elif sanitized_output:
                extracted_patch = _extract_patch_from_text(sanitized_output)
                if extracted_patch:
                    patch_answer = extracted_patch
                    patch_source = "raw_output_patch"
        metadata = {
            "model": self._model,
            "provider_api_base": self._provider_api_base,
            "execution_id": execution_id,
            "workspace_dir": paths["workspace_dir"],
            "repo_workdir": repo_workdir,
            "home_dir": paths["home_dir"],
            "zeroclaw_version": version_output,
            "sandbox_backend": getattr(sandbox, "name", ""),
            "transport": "zeroclaw_cli_sandbox",
        }
        if patch_source:
            metadata["patch_source"] = patch_source
        if dropped_runtime_logs:
            metadata["runtime_logs_dropped_from_output"] = dropped_runtime_logs
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
            answer_override=patch_answer,
        )
        partial_response.workspace_snapshot_paths = [
            path
            for path in [
                paths["task_path"],
                paths["config_path"],
                paths["stdout_path"],
                paths["stderr_path"],
                paths["runtime_trace_path"],
            ]
            if path
        ]
        partial_response.sandbox_metadata = (
            sandbox.metadata()
            if hasattr(sandbox, "metadata")
            else {}
        )
        partial_response.sandbox_id = str(
            partial_response.sandbox_metadata.get("sandbox_id", "")
            or getattr(sandbox, "sandbox_id", "")
            or ""
        )

        if result.exit_code == 124:
            self._mark_partial_failure(partial_response, failure_reason="timeout")
            self._raise_with_partial_response(
                f"ZeroClaw agent timed out after {self._request_timeout}s",
                partial_response,
                error_type="timeout",
            )
        if result.exit_code != 0:
            diagnostics = self._collect_sandbox_diagnostics_safe(sandbox, paths, env)
            failure_detail = (
                raw_stderr
                or sanitized_output
                or raw_output
                or result.stderr.strip()
                or f"exit code {result.exit_code}"
            )
            failure_reason = _classify_cli_error_output(failure_detail)
            self._mark_partial_failure(partial_response, failure_reason=failure_reason)
            self._raise_with_partial_response(
                "ZeroClaw agent failed in sandbox: "
                f"{failure_detail}\n"
                f"diagnostics:\n{diagnostics}",
                partial_response,
                error_type=failure_reason,
            )
        if sanitized_output.lower().startswith("error:"):
            failure_reason = _classify_cli_error_output(sanitized_output)
            self._mark_partial_failure(partial_response, failure_reason=failure_reason)
            self._raise_with_partial_response(
                sanitized_output.splitlines()[0],
                partial_response,
                error_type=failure_reason,
            )
        if raw_output and not sanitized_output and dropped_runtime_logs:
            diagnostics = self._collect_sandbox_diagnostics_safe(sandbox, paths, env)
            self._mark_partial_failure(partial_response, failure_reason="empty_response")
            self._raise_with_partial_response(
                "ZeroClaw produced no assistant output; stdout contained only runtime/provider logs.\n"
                f"diagnostics:\n{diagnostics}",
                partial_response,
                error_type="empty_response",
            )
        if not sanitized_output:
            diagnostics = self._collect_sandbox_diagnostics_safe(sandbox, paths, env)
            self._mark_partial_failure(partial_response, failure_reason="empty_response")
            self._raise_with_partial_response(
                f"ZeroClaw agent produced no output. stderr={raw_stderr}\n"
                f"diagnostics:\n{diagnostics}",
                partial_response,
                error_type="empty_response",
            )

        return partial_response

    def solve(self, task: BenchmarkTask, sandbox: Any = None) -> AgentResponse:
        if not self._model:
            raise RuntimeError("ZeroClawAgent requires agent.config.model or OPENAI_MODEL_NAME.")
        self._ensure_provider_credentials("native sandbox execution")
        if sandbox is None:
            raise RuntimeError(
                "ZeroClawAgent now requires a live sandbox/container session. "
                "Configure sandbox_name or rock_image so Runner can provide one."
            )
        return self._run_in_sandbox(task, sandbox)

    def teardown(self) -> None:
        pass


AgentRegistry.register("zeroclaw", ZeroClawAgent)
