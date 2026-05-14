"""OpenCode agent wrapper with native multimodal support and SWE-bench container mode."""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from alphadiana.agent.logprob_capture import (
    apply_openai_logprob_request,
    extract_openai_logprob_records,
    finalize_logprob_capture,
    raw_token_logprob_dict_to_record,
    resolve_logprob_capture_config,
)
from alphadiana.agent.logprob_proxy import LogprobCaptureProxy
from alphadiana.agent.base import Agent, AgentResponse
from alphadiana.agent.preservation import (
    add_artifact_file_refs,
    build_event_trajectories,
    build_runtime_trace_summary,
)
from alphadiana.agent.registry import AgentRegistry
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.container_runtime import PodmanAgentRuntime, PodmanAgentSpec, PodmanCLI, PodmanError
from alphadiana.utils.attachments import iter_binary_attachments, write_attachments
from alphadiana.utils.math_answer import extract_answer_candidate, extract_boxed

logger = logging.getLogger(__name__)


def _resolve_skill_folder(raw: Any) -> str | None:
    """Resolve agent.config.skill_folder.

    - Empty/None  -> None (skill mounting disabled)
    - Absolute path -> use as-is
    - Path with "/" -> resolve relative to cwd
    - Bare name like "advanced-maths" -> alphadiana/skills/<name>/
    """
    text = str(raw or "").strip()
    if not text:
        return None
    if os.path.isabs(text):
        return text
    if "/" in text or os.path.exists(text):
        return str(Path(text).resolve())
    pkg_root = Path(__file__).resolve().parent.parent  # alphadiana/
    return str(pkg_root / "skills" / text)


_SUPPORTED_CONTROLLER_MODES = {"host", "docker", "podman"}
_DEFAULT_DOCKER_CONTROLLER_IMAGE = "alphadiana/tb2-opencode-controller:latest"
_DEFAULT_PODMAN_CONTROLLER_IMAGE = "alphadiana-opencode-podman:latest"

_EXPLICIT_ANSWER_RE = re.compile(
    r"(?:\*{0,2})(?:the\s+)?(?:final\s+)?answer(?:\*{0,2})\s*(?:[:：]|is|=)\s*(.+)",
    re.IGNORECASE,
)
_BOXED_RE = re.compile(r"\\boxed\{", re.DOTALL)

_DEFAULT_SYSTEM_PROMPT = (
    "You are an expert problem solver. When given a problem, actively use "
    "your available tools throughout your reasoning process. Use code execution "
    "to verify intermediate steps and confirm your final answer.\n\n"
    "When you have reached your final answer, you MUST present it in the following format:\n\n"
    "$$\\boxed{your answer here}$$\n\n"
    "Do not skip the boxed format. The boxed answer must appear at the very end "
    "of your response and contain only the final answer, not explanations."
)

_SWE_BENCH_SYSTEM_PROMPT = (
    "You are an expert software engineer. You will be given a GitHub issue description "
    "for a Python repository. Your task is to fix the issue by making the minimal "
    "necessary code changes.\n\n"
    "Guidelines:\n"
    "- Use your file reading and editing tools to inspect and modify the repository.\n"
    "- Make the smallest correct change needed; avoid unrelated refactoring.\n"
    "- Do not modify test files.\n"
    "- After making your changes, ensure the code is syntactically correct.\n"
    "- You do not need to run the full test suite; just fix the issue described."
)

_PRESERVED_TEXT_ARTIFACT_SUFFIXES = {
    ".json",
    ".jsonl",
    ".log",
    ".md",
    ".txt",
    ".toml",
    ".yaml",
    ".yml",
}
_SECRET_FIELD_NAMES = frozenset({
    "api_key",
    "apikey",
    "authorization",
    "token",
})


def _parse_optional_bool(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(value)


def _extract_boxed_content(text: str) -> str | None:
    """Extract content from \\boxed{...} handling nested braces."""
    match = _BOXED_RE.search(text)
    if not match:
        return None
    start = match.end()
    depth = 1
    index = start
    while index < len(text) and depth > 0:
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
        index += 1
    if depth == 0:
        return text[start:index - 1]
    return None


def _extract_patch_from_text(text: str) -> str:
    """Extract a unified diff patch from raw agent output."""
    boxed = _extract_boxed_content(text)
    if boxed and ("diff " in boxed or "---" in boxed or "+++" in boxed):
        return boxed.strip()

    diff_git_re = re.compile(
        r"^diff --git .+?(?=\n(?:diff --git |\Z))",
        re.MULTILINE | re.DOTALL,
    )
    matches = diff_git_re.findall(text)
    if matches:
        return "\n".join(match.strip() for match in matches).strip()

    fenced_re = re.compile(r"```(?:diff)?\s*\n(.*?)```", re.DOTALL)
    for match in fenced_re.finditer(text):
        block = match.group(1).strip()
        if "---" in block and "+++" in block:
            return block

    return ""


def _is_swe_bench_task(task: BenchmarkTask) -> bool:
    """Return whether *task* comes from a SWE-bench-style dataset."""
    metadata = getattr(task, "metadata", None) or {}
    return "instance_id" in metadata or "repo" in metadata


def _extract_event_texts(obj: dict[str, Any]) -> list[str]:
    """Extract user-visible assistant text from OpenCode JSON events."""
    texts: list[str] = []

    if obj.get("type") == "text":
        text = obj.get("text")
        if isinstance(text, str) and text:
            texts.append(text)

    part = obj.get("part")
    if isinstance(part, dict):
        if part.get("type") == "text":
            text = part.get("text")
            if isinstance(text, str) and text:
                texts.append(text)
        elif part.get("type") == "assistant":
            message = part.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str) and content:
                    texts.append(content)

    if obj.get("type") == "assistant":
        message = obj.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str) and content:
                texts.append(content)

    return texts


def _extract_strict_answer(text: str) -> str:
    """Extract only explicit final answers from partial OpenCode output."""
    boxed = extract_boxed(text)
    if boxed is not None:
        return boxed.strip()

    matches = list(_EXPLICIT_ANSWER_RE.finditer(text))
    if matches:
        return matches[-1].group(1).strip()

    return ""


def _build_prompt(problem: str, system_prompt: str, attachment_paths: list[Path] | None = None) -> str:
    """Build the OpenCode prompt text for a benchmark task."""
    prompt = problem
    if system_prompt.strip():
        prompt = f"{system_prompt}\n\n--- Problem ---\n{problem}"

    if attachment_paths:
        attachment_lines = "\n".join(f"- {path.name}" for path in attachment_paths)
        prompt = (
            f"{prompt}\n\n--- Attachments ---\n"
            "Use the attached files as part of the problem context before answering.\n"
            f"{attachment_lines}"
        )

    return prompt


def _derive_api_model(cli_model: str, model_name: str) -> str:
    """Derive the provider model name when the config does not set api_model."""
    if model_name:
        return model_name
    if "/" in cli_model:
        return cli_model.split("/", 1)[1]
    return cli_model


def _has_image_attachments(attachments: dict[str, Any]) -> bool:
    """Return whether a task carries image attachments."""
    for _, _, mime in iter_binary_attachments(attachments):
        if mime.lower().startswith("image/"):
            return True
    return False


def _parse_opencode_output(raw_output: str) -> tuple[str, list[dict[str, Any]], str]:
    """Parse JSON-lines OpenCode output into text, events, and session id."""
    content_parts: list[str] = []
    events: list[dict[str, Any]] = []
    session_id = ""

    for line in raw_output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            events.append(obj)
            if not session_id:
                session_id = str(obj.get("sessionID", ""))
                if not session_id and isinstance(obj.get("part"), dict):
                    session_id = str(obj["part"].get("sessionID", ""))
            content_parts.extend(_extract_event_texts(obj))
        except (json.JSONDecodeError, ValueError):
            content_parts.append(line)

    assistant_text = "\n".join(part for part in content_parts if part).strip()
    return assistant_text, events, session_id


def _text_from_logprob_records(records: list[dict]) -> str:
    """Best-effort reconstruction of streamed provider text from token records."""
    parts: list[str] = []
    for record in records or []:
        token = record.get("token")
        if isinstance(token, str) and token:
            parts.append(token)
    return "".join(parts)


def _classify_opencode_error(name: str, message: str) -> str:
    """Map OpenCode-reported failures into result-store error buckets."""
    blob = f"{name} {message}".lower()
    provider_markers = (
        "apierror",
        "badrequest",
        "authentication",
        "rate limit",
        "ratelimit",
        "openai",
        "litellm",
        "provider",
        "tool choice requires",
        "contextoverflowerror",
        "maximum context length",
        "input_tokens",
        "max context",
    )
    if any(marker in blob for marker in provider_markers):
        return "provider_error"
    return "agent_error"


def _extract_opencode_error(events: list[dict[str, Any]]) -> dict[str, str] | None:
    """Return the last OpenCode error event, if present."""
    for event in reversed(events):
        if str(event.get("type", "")).strip().lower() != "error":
            continue
        error_obj = event.get("error")
        if not isinstance(error_obj, dict):
            error_obj = {}
        name = str(error_obj.get("name", "") or "").strip()
        message = ""
        data_obj = error_obj.get("data")
        if isinstance(data_obj, dict):
            message = str(data_obj.get("message", "") or "").strip()
        if not message:
            message = str(event.get("message", "") or "").strip()
        if not message:
            message = json.dumps(error_obj, ensure_ascii=False)
        return {
            "name": name,
            "message": message,
            "error_type": _classify_opencode_error(name, message),
        }
    return None


def _read_opencode_session_trace(session_dir: Path, session_id: str) -> tuple[str, str]:
    """Read the most relevant saved OpenCode session trace."""
    if not session_dir.exists() or not session_dir.is_dir():
        return "", ""

    candidates: list[Path] = []
    if session_id:
        candidates.extend(
            sorted(
                session_dir.glob(f"{session_id}*"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        )
    if not candidates:
        candidates.extend(
            sorted(
                (path for path in session_dir.iterdir() if path.is_file()),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        )

    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if text.strip():
            return text, path.name
    return "", ""


def extract_opencode_logprob_records(
    *,
    events: list[dict[str, Any]],
    session_trace: str,
    stdout: str,
) -> list[dict]:
    """Extract OpenAI-shaped logprob records from OpenCode event/trace payloads."""
    envelope_keys = {"choices", "response", "provider_response", "payload", "data", "message", "part"}

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

    def _json_from_string(value: Any) -> Any | None:
        if not isinstance(value, str):
            return None
        text = value.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, (dict, list)) else None

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
                parsed, token_index_next = extract_openai_logprob_records(
                    node,
                    start_index=token_index,
                )
                if parsed:
                    for item in parsed:
                        records.append(
                            raw_token_logprob_dict_to_record(
                                {
                                    "token": item.get("token", ""),
                                    "logprob": item.get("logprob", 0.0),
                                    "top_logprobs": item.get("top_logprobs", []),
                                },
                                int(item.get("token_index", 0)),
                            )
                        )
                token_index = token_index_next
            for key, value in node.items():
                if key not in envelope_keys:
                    continue
                parsed_value = _json_from_string(value)
                if parsed_value is not None:
                    _scan(parsed_value)
                _scan(value)
            for key, value in node.items():
                if key in envelope_keys:
                    continue
                _scan(value)
            return
        if isinstance(node, list):
            for entry in node:
                _scan(entry)

    for event in events or []:
        _scan(event)
    for payload in _jsonl_objects(session_trace):
        _scan(payload)
    for payload in _jsonl_objects(stdout):
        _scan(payload)
    return records


def _count_json_objects(text: str) -> int:
    """Count valid JSON objects in a JSONL-like blob."""
    count = 0
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            count += 1
    return count


def _redact_secret_fields(value: Any) -> Any:
    """Redact obvious credential-like fields from structured artifacts."""
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


def _sanitize_json_artifact_text(raw_text: str) -> str:
    """Best-effort JSON sanitizer for persisted config artifacts."""
    try:
        payload = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return raw_text
    return json.dumps(
        _redact_secret_fields(payload),
        indent=2,
        ensure_ascii=False,
    )


def _summarize_local_sqlite_db(db_path: Path) -> str:
    """Return a compact JSON summary for a local SQLite artifact."""
    summary: dict[str, Any] = {
        "path": db_path.name,
        "size_bytes": db_path.stat().st_size if db_path.exists() else 0,
    }
    if not db_path.exists():
        return json.dumps(summary, indent=2, ensure_ascii=False)

    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(str(db_path))
        cursor = connection.cursor()
        tables: list[dict[str, Any]] = []
        schema: list[dict[str, Any]] = []
        for name, sql in cursor.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall():
            row_count: int | None
            try:
                row_count = cursor.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            except Exception:
                row_count = None
            tables.append({"name": name, "row_count": row_count})
            schema.append({"name": name, "sql": sql})
        summary["tables"] = tables
        summary["schema"] = schema

        preview_tables = [
            table["name"]
            for table in tables
            if table["name"] in {"message", "part", "session"}
        ]
        previews: dict[str, list[dict[str, Any]]] = {}
        for table_name in preview_tables:
            columns = [row[1] for row in cursor.execute(f"PRAGMA table_info('{table_name}')").fetchall()]
            rows = cursor.execute(f'SELECT * FROM "{table_name}" ORDER BY rowid LIMIT 10').fetchall()
            preview_rows: list[dict[str, Any]] = []
            for row in rows:
                item: dict[str, Any] = {}
                for index, column in enumerate(columns):
                    cell = row[index]
                    if isinstance(cell, bytes):
                        cell = cell.decode("utf-8", "replace")
                    if isinstance(cell, str) and len(cell) > 2000:
                        cell = cell[:2000] + "...<truncated>"
                    item[column] = cell
                preview_rows.append(item)
            previews[table_name] = preview_rows
        if previews:
            summary["previews"] = previews
    except Exception as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if connection is not None:
            connection.close()

    return json.dumps(summary, indent=2, ensure_ascii=False)


def _collect_local_opencode_artifacts(config_dir: Path, session_id: str) -> dict[str, Any]:
    """Collect stable OpenCode artifacts before the temp workdir is deleted."""
    artifact_root = config_dir
    artifact_paths = sorted(path for path in artifact_root.rglob("*") if path.is_file())

    workspace_file_contents: dict[str, str] = {}
    manifest_files: dict[str, str] = {}
    state_files: list[str] = []
    memory_files: list[str] = []

    if artifact_paths:
        workspace_file_contents["opencode_workspace_listing.txt"] = "".join(
            f"{path.relative_to(artifact_root).as_posix()}\t{path.stat().st_size}\n"
            for path in artifact_paths
        )
        manifest_files["workspace_listing"] = "opencode_workspace_listing.txt"

    config_path = artifact_root / "opencode.json"
    if config_path.exists():
        config_text = _sanitize_json_artifact_text(
            config_path.read_text(encoding="utf-8", errors="replace")
        )
        workspace_file_contents["opencode_config.json"] = config_text
        manifest_files["runtime_config"] = "opencode_config.json"
        state_files.append("opencode_config.json")

    session_dir = artifact_root / "sessions"
    session_text, session_name = _read_opencode_session_trace(session_dir, session_id)
    if session_text:
        workspace_file_contents["opencode_session.jsonl"] = session_text
        manifest_files["session_trace"] = "opencode_session.jsonl"
        state_files.append("opencode_session.jsonl")
        if session_name:
            session_alias = f"state/sessions/{session_name}"
            workspace_file_contents[session_alias] = session_text
            state_files.append(session_alias)

    db_paths = sorted(path for path in artifact_paths if path.name.startswith("opencode.db"))
    if db_paths:
        inventory_alias = "memory/opencode_db_files.json"
        workspace_file_contents[inventory_alias] = json.dumps(
            {
                "files": [
                    {
                        "path": path.relative_to(artifact_root).as_posix(),
                        "size_bytes": path.stat().st_size,
                    }
                    for path in db_paths
                ]
            },
            indent=2,
            ensure_ascii=False,
        )
        manifest_files["memory_inventory"] = inventory_alias
        memory_files.append(inventory_alias)

        primary_db = next((path for path in db_paths if path.name == "opencode.db"), None)
        if primary_db is not None:
            summary_alias = "memory/opencode.db.summary.json"
            workspace_file_contents[summary_alias] = _summarize_local_sqlite_db(primary_db)
            manifest_files["memory_summary"] = summary_alias
            memory_files.append(summary_alias)

    for path in artifact_paths:
        relative = path.relative_to(artifact_root).as_posix()
        if path == config_path:
            continue
        if relative.startswith("sessions/"):
            continue
        if path.name.startswith("opencode.db"):
            continue
        if path.suffix.lower() not in _PRESERVED_TEXT_ARTIFACT_SUFFIXES:
            continue
        alias = f"state/{relative}"
        if alias in workspace_file_contents:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if not text:
            continue
        workspace_file_contents[alias] = text
        state_files.append(alias)

    artifact_manifest: dict[str, Any] = {"files": manifest_files} if manifest_files else {}
    if state_files:
        artifact_manifest["state_files"] = list(dict.fromkeys(state_files))
    if memory_files:
        artifact_manifest["memory_files"] = list(dict.fromkeys(memory_files))
    return {
        "artifact_manifest": artifact_manifest,
        "workspace_file_contents": workspace_file_contents,
    }


class OpenCodeAgent(Agent):
    """Agent that runs OpenCode CLI and supports task-container execution.

    Docker isolation: set ``controller_mode: docker`` to run the opencode CLI
    inside a Docker controller container instead of directly on the host.
    Requires the legacy/baseline controller image.

    Podman isolation: set ``controller_mode: podman`` to run the opencode CLI
    inside the Podman-only controller image built from
    ``opencode_deploy/Containerfile.podman-controller``.

    Build with::

        podman build \\
          -f opencode_deploy/Containerfile.podman-controller \\
          -t alphadiana-opencode-podman:latest .

        docker build --network host \\
          -f docker/terminal_bench2/Dockerfile.opencode-controller \\
          -t alphadiana/tb2-opencode-controller:latest .
    """

    name = "opencode"

    def setup(self, config: dict) -> None:
        self._runtime = str(config.get("runtime", "")).strip()
        controller_mode = str(config.get("controller_mode", "host") or "host").strip().lower()
        if controller_mode not in _SUPPORTED_CONTROLLER_MODES:
            supported = ", ".join(sorted(_SUPPORTED_CONTROLLER_MODES))
            raise ValueError(
                f"Unsupported opencode controller_mode '{controller_mode}'. "
                f"Expected one of: {supported}."
            )
        self._controller_mode = controller_mode
        default_controller_image = (
            _DEFAULT_PODMAN_CONTROLLER_IMAGE
            if controller_mode == "podman"
            else _DEFAULT_DOCKER_CONTROLLER_IMAGE
        )
        self._controller_image = str(
            config.get("controller_image", default_controller_image)
            or default_controller_image
        ).strip()
        self._controller_network = str(config.get("controller_network", "host") or "host").strip()
        self._cli_model = self._resolve_setting(config, "model", "OPENAI_MODEL_NAME")
        self._api_base = self._resolve_setting(config, "api_base", "OPENAI_BASE_URL")
        self._api_key = self._resolve_setting(
            config,
            "api_key",
            "OPENAI_API_KEY",
            default="EMPTY",
        )
        self._model_name = self._resolve_setting(config, "model_name", "OPENAI_MODEL_NAME")
        if not self._cli_model and self._model_name:
            self._cli_model = f"custom/{self._model_name}"
        self._api_model = str(config.get("api_model", "")).strip() or _derive_api_model(
            self._cli_model,
            self._model_name,
        )
        self._tool_call = bool(config.get("tool_call", True))
        self._timeout = int(config.get("timeout", 1200))
        raw_temperature = config.get("temperature", None)
        self._temperature = (
            None if raw_temperature in (None, "") else float(raw_temperature)
        )
        raw_top_p = config.get("top_p", None)
        self._top_p = None if raw_top_p in (None, "") else float(raw_top_p)
        raw_max_tokens = config.get("max_tokens", None)
        self._max_tokens = None if raw_max_tokens in (None, "") else int(raw_max_tokens)
        self._enable_thinking = _parse_optional_bool(config.get("enable_thinking", None))
        self._variant = str(config.get("variant", "")).strip()
        self._agent_name = str(config.get("agent", "")).strip()
        self._agent_md_path = str(config.get("agent_md_path", "")).strip()
        self._agent_md_content = str(config.get("agent_md_content", "")).strip()
        self._print_logs = bool(config.get("print_logs", False))
        self._log_level = str(config.get("log_level", "")).strip()
        self._system_prompt = config.get("system_prompt", _DEFAULT_SYSTEM_PROMPT)
        self._skill_folder = _resolve_skill_folder(config.get("skill_folder", ""))
        self._opencode_bin = config.get("opencode_bin", "opencode")
        self._streaming = config.get("streaming") if "streaming" in config else None
        self._logprob_capture = resolve_logprob_capture_config(config)
        self._config = dict(config)
        self._runtime_manager = None
        self._podman_runtime = config.get("_podman_runtime") or PodmanCLI()
        self._last_controller_metadata: dict[str, Any] = {}

        if not self._agent_name:
            if self._agent_md_path:
                self._agent_name = Path(self._agent_md_path).stem
            elif self._agent_md_content:
                self._agent_name = "custom-agent"

        if self._runtime == "swebench_container":
            from alphadiana.agent.opencode_container_runtime import OpenCodeContainerRuntimeManager

            self._runtime_manager = OpenCodeContainerRuntimeManager(config)

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

    def solve(self, task: BenchmarkTask, sandbox: Any = None) -> AgentResponse:
        if self._runtime == "swebench_container":
            if sandbox is None:
                raise RuntimeError(
                    "OpenCode runtime='swebench_container' requires a sandbox session"
                )
            return self._solve_in_container(task, sandbox)
        return self._solve_cli(task)

    def _solve_in_container(self, task: BenchmarkTask, sandbox: Any) -> AgentResponse:
        """Run OpenCode inside the SWE-bench task container and extract a patch."""
        assert self._runtime_manager is not None

        model_name = self._model_name or self._api_model
        if not model_name:
            if "/" in self._cli_model:
                model_name = self._cli_model.split("/", 1)[1]
            else:
                model_name = self._cli_model

        system_prompt = str(self._config.get("system_prompt", _SWE_BENCH_SYSTEM_PROMPT))
        if system_prompt.strip():
            problem = f"{system_prompt}\n\n--- Issue ---\n{task.problem}"
        else:
            problem = task.problem

        start = time.time()
        result = self._runtime_manager.run_task(
            sandbox,
            problem,
            model_name=model_name,
            task_id=task.task_id,
        )
        wall_time = time.time() - start

        raw_stdout = result.get("stdout", "")
        stderr = result.get("stderr", "")
        exit_code = int(result.get("exit_code", -1))
        patch_from_git = str(result.get("patch", "") or "")
        request_messages = [{"role": "user", "content": problem}]

        assistant_text, events, session_id = _parse_opencode_output(raw_stdout)
        full_content = assistant_text or raw_stdout
        trajectory, reasoning_trajectory = build_event_trajectories(
            request_messages,
            events,
            final_output=full_content,
        )

        if patch_from_git:
            answer = patch_from_git
        else:
            answer = _extract_patch_from_text(full_content)
            if not answer:
                logger.warning(
                    "No patch found via git diff or text extraction for task %s",
                    task.task_id,
                )

        if exit_code not in (0, 124) and not answer:
            logger.warning(
                "OpenCode exited with code %d for task %s. stderr: %s",
                exit_code,
                task.task_id,
                stderr[:500],
            )

        artifacts: dict[str, Any] = {}
        try:
            artifacts = self._runtime_manager.collect_artifacts(sandbox)
        except Exception as exc:
            logger.debug("Failed to collect OpenCode container artifacts: %s", exc)

        preserved_workspace_files = dict(artifacts.get("workspace_file_contents", {}) or {})
        preserved_workspace_files.setdefault("prompt.txt", problem)
        preserved_workspace_files.setdefault("opencode_output.jsonl", raw_stdout)
        if stderr:
            preserved_workspace_files.setdefault("opencode_stderr.log", stderr)

        artifact_manifest = add_artifact_file_refs(
            artifacts.get("artifact_manifest", {}),
            response_stream="opencode_output.jsonl",
            session_trace=(
                "opencode_session.jsonl"
                if "opencode_session.jsonl" in preserved_workspace_files
                else None
            ),
            stderr_log="opencode_stderr.log" if stderr else None,
            prompt_text="prompt.txt",
        )
        response_json = build_runtime_trace_summary(
            output_text=full_content,
            stderr_text=stderr.strip(),
            records=events,
            extra={
                "exit_code": exit_code,
                "patch_source": "git_diff" if patch_from_git else "text_extraction",
                "session_id": session_id,
            },
        )

        error_info = _extract_opencode_error(events)
        response_metadata = {
            "runtime": "swebench_container",
            "returncode": exit_code,
            "exit_code": exit_code,
            "stderr": stderr[:2000] if stderr else "",
            "num_events": len(events),
            "logprob_probe_event_count": len(events),
            "logprob_probe_session_json_count": 0,
            "logprob_probe_stdout_json_count": 0,
            "logprob_probe_record_count": 0,
            "session_id": session_id,
            "patch_source": "git_diff" if patch_from_git else "text_extraction",
            "transport": "opencode_cli_container",
            **artifacts,
        }
        proxy_logprob_records = list(result.get("logprob_records") or [])
        proxy_logprob_metadata = dict(result.get("logprob_proxy_metadata") or {})
        if proxy_logprob_metadata:
            response_metadata.update(proxy_logprob_metadata)
            response_metadata["logprob_probe_proxy_count"] = len(proxy_logprob_records)
            response_metadata["logprob_source"] = "provider_proxy" if proxy_logprob_records else ""
        session_trace_text = (artifacts.get("workspace_file_contents", {}) or {}).get(
            "opencode_session.jsonl", ""
        )
        logprob_probe_session_json_count = _count_json_objects(session_trace_text)
        logprob_probe_stdout_json_count = _count_json_objects(raw_stdout)
        logprob_records = extract_opencode_logprob_records(
            events=events,
            session_trace=session_trace_text,
            stdout=raw_stdout,
        )
        if not logprob_records and proxy_logprob_records:
            logprob_records = proxy_logprob_records
            response_metadata["logprob_source"] = "provider_proxy"
        elif logprob_records:
            response_metadata["logprob_source"] = "opencode_artifacts"
        response_metadata["logprob_probe_session_json_count"] = logprob_probe_session_json_count
        response_metadata["logprob_probe_stdout_json_count"] = logprob_probe_stdout_json_count
        response_metadata["logprob_probe_record_count"] = len(logprob_records)
        token_entropy_stats, response_metadata = finalize_logprob_capture(
            harness="opencode",
            enabled=self._logprob_capture["enabled"],
            records=logprob_records,
            metadata=response_metadata,
        )
        if error_info:
            response_metadata.update({
                "opencode_error_name": error_info["name"],
                "opencode_error_message": error_info["message"],
            })
            response_json = {
                **response_json,
                "opencode_error_name": error_info["name"],
                "opencode_error_message": error_info["message"],
            }

        response = AgentResponse(
            answer=None if error_info else answer,
            trajectory=trajectory,
            reasoning_trajectory=reasoning_trajectory,
            raw_output=full_content,
            wall_time_sec=wall_time,
            token_entropy_stats=token_entropy_stats,
            metadata=response_metadata,
            system_prompt=system_prompt,
            request_messages=request_messages,
            response_json=response_json,
            artifact_manifest=artifact_manifest,
            workspace_file_contents=preserved_workspace_files,
            workspace_snapshot_paths=list(artifacts.get("workspace_snapshot_paths", []) or []),
            sandbox_metadata=artifacts.get("sandbox_metadata", {}),
        )
        if error_info:
            exc = RuntimeError(error_info["message"])
            setattr(exc, "partial_response", response)
            setattr(exc, "error_type", error_info["error_type"])
            setattr(exc, "response_body", full_content)
            raise exc
        return response

    def _run_in_docker(
        self,
        cmd: list[str],
        workdir: str,
        env: dict[str, str],
    ) -> tuple[str, str, int]:
        """Run opencode inside a Docker container for host isolation."""
        uid = os.getuid()
        gid = os.getgid()
        container_home = Path(workdir) / ".controller-home"
        container_home.mkdir(parents=True, exist_ok=True)
        container_name = f"alphadiana-opencode-{os.getpid()}-{time.time_ns()}"
        docker_cmd = [
            "docker", "run", "--rm",
            "--name", container_name,
            "--label", "alphadiana.component=opencode-controller",
            "--label", f"alphadiana.workdir={workdir}",
            f"--network={self._controller_network}",
            f"--user={uid}:{gid}",
            "-v", f"{workdir}:{workdir}",
        ]
        if self._skill_folder:
            _skill_name = Path(self._skill_folder).name
            _dst = f"{workdir}/skills/{_skill_name}"
            os.makedirs(f"{workdir}/skills", exist_ok=True)
            if not os.path.exists(_dst):
                shutil.copytree(self._skill_folder, _dst)
            logger.info(
                "Copied skill folder %s -> %s (real files so opencode read tool indexes them)",
                self._skill_folder, _dst,
            )
        docker_cmd += [
            "-w", workdir,
            "-e", f"HOME={container_home}",
        ]
        for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "XDG_CONFIG_HOME"):
            if key in env:
                docker_cmd.extend(["-e", f"{key}={env[key]}"])
        docker_cmd.append(self._controller_image)
        # Replace host opencode binary with container entrypoint
        docker_cmd.extend(["node", "/usr/lib/node_modules/opencode-ai/bin/opencode"])
        docker_cmd.extend(cmd[1:])  # skip the host 'opencode' binary

        logger.info("Running opencode in Docker: %s", self._controller_image)
        process: subprocess.Popen[str] | None = None
        try:
            process = subprocess.Popen(
                docker_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                start_new_session=True,
            )
            raw_output, stderr = process.communicate(timeout=self._timeout)
            return raw_output, stderr, process.returncode
        except subprocess.TimeoutExpired:
            logger.warning("OpenCode Docker timed out after %ds", self._timeout)
            raw_output = ""
            stderr = f"Timeout after {self._timeout}s"
            self._force_remove_docker_container(container_name)
            if process is not None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    raw_output, timed_out_stderr = process.communicate(timeout=5)
                    stderr = timed_out_stderr or stderr
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    raw_output, timed_out_stderr = process.communicate()
                    stderr = timed_out_stderr or stderr
                except ProcessLookupError:
                    raw_output, timed_out_stderr = process.communicate()
                    stderr = timed_out_stderr or stderr
            return raw_output, stderr, -1

    def _run_in_podman(
        self,
        cmd: list[str],
        workdir: str,
        env: dict[str, str],
    ) -> tuple[str, str, int]:
        """Run opencode inside a Podman controller container."""
        container_home = Path(workdir) / ".controller-home"
        container_home.mkdir(parents=True, exist_ok=True)
        self._last_controller_metadata = {
            "container_engine": "podman",
            "controller_mode": "podman",
            "controller_image": self._controller_image,
            "controller_container_id": "",
            "container_id": "",
            "container_logs_collected": False,
            "container_cleanup_attempted": False,
            "container_cleanup_status": "",
        }
        if self._skill_folder:
            _skill_name = Path(self._skill_folder).name
            _dst = f"{workdir}/skills/{_skill_name}"
            os.makedirs(f"{workdir}/skills", exist_ok=True)
            if not os.path.exists(_dst):
                shutil.copytree(self._skill_folder, _dst)
            logger.info(
                "Copied skill folder %s -> %s (real files so opencode read tool indexes them)",
                self._skill_folder, _dst,
            )
        container_id = ""
        agent_runtime = PodmanAgentRuntime(runtime=self._podman_runtime)
        try:
            spec = PodmanAgentSpec(
                adapter_name="opencode-podman",
                image=self._controller_image,
                run_command="",
                exposed_port=None,
                workdir=workdir,
                env={"HOME": str(container_home)},
                network=self._controller_network,
                request_timeout=float(self._timeout),
                startup_timeout=min(max(float(self._timeout), 30.0), 120.0),
                cleanup_timeout=30.0,
                name_prefix="alphadiana-opencode",
                extra_args=[
                    "--label", "alphadiana.component=opencode-controller",
                    "--label", f"alphadiana.workdir={workdir}",
                    "-v", f"{workdir}:{workdir}",
                ],
                metadata={
                    "controller_mode": "podman",
                    "controller_image": self._controller_image,
                    "logprob_support": "enabled" if self._logprob_capture["enabled"] else "disabled",
                },
            )
            runtime_result = agent_runtime.start(spec)
            container_id = runtime_result.container_id
            self._last_controller_metadata.update({
                **runtime_result.metadata,
                "controller_container_id": container_id,
                "container_id": container_id,
            })
            exec_env = {"HOME": str(container_home)}
            for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "XDG_CONFIG_HOME"):
                if key in env:
                    exec_env[key] = env[key]
            exec_result = agent_runtime.exec(
                ["node", "/usr/lib/node_modules/opencode-ai/bin/opencode", *cmd[1:]],
                env=exec_env,
                workdir=workdir,
                timeout=self._timeout,
                check=False,
            )
            return exec_result.stdout, exec_result.stderr, exec_result.returncode
        except PodmanError as exc:
            if exc.result.returncode == 124:
                logger.warning("OpenCode Podman timed out after %ds", self._timeout)
                return exc.result.stdout, exc.result.stderr or f"Timeout after {self._timeout}s", -1
            raise
        finally:
            if container_id:
                try:
                    artifacts = agent_runtime.collect_artifacts()
                    self._last_controller_metadata.update(artifacts.metadata)
                    self._last_controller_metadata["controller_container_id"] = container_id
                    self._last_controller_metadata["container_id"] = container_id
                    self._last_controller_metadata["container_log_excerpt"] = artifacts.logs[:4000]
                    self._last_controller_metadata["container_logs_collected"] = True
                except Exception as exc:
                    self._last_controller_metadata["container_log_error"] = str(exc)[:500]
                    self._last_controller_metadata["container_logs_collected"] = False
                self._last_controller_metadata["container_cleanup_attempted"] = True
                try:
                    agent_runtime.cleanup(check=False)
                    self._last_controller_metadata["container_cleanup_status"] = "removed"
                except Exception as exc:
                    self._last_controller_metadata["container_cleanup_status"] = "failed"
                    self._last_controller_metadata["container_cleanup_error"] = str(exc)[:500]

    def _force_remove_docker_container(self, container_name: str) -> None:
        """Stop an OpenCode controller container that outlived the docker client."""
        try:
            result = subprocess.run(
                ["docker", "rm", "-f", container_name],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
        except Exception as exc:  # pragma: no cover - defensive cleanup path
            logger.warning("Failed to remove timed-out OpenCode container %s: %s", container_name, exc)
            return
        if result.returncode != 0 and "No such container" not in result.stderr:
            logger.warning(
                "Failed to remove timed-out OpenCode container %s: %s",
                container_name,
                result.stderr.strip()[:500],
            )

    def _solve_cli(self, task: BenchmarkTask) -> AgentResponse:
        """Run tasks through the OpenCode CLI (host or Docker, multimodal-aware)."""
        start = time.time()
        session_trace = ""
        proxy_logprob_records: list[dict] = []
        preserved_local_artifacts: dict[str, Any] = {}
        attachment_artifacts: list[dict[str, str]] = []

        with tempfile.TemporaryDirectory(prefix="opencode-task-") as workdir:
            workdir_path = Path(workdir)
            container_home = workdir_path / ".controller-home"
            config_root = workdir_path / "xdg-config"
            config_dir = config_root / "opencode"
            config_dir.mkdir(parents=True, exist_ok=True)

            provider_model_name = self._model_name or self._api_model
            if not provider_model_name:
                provider_model_name = _derive_api_model(self._cli_model, self._model_name)
            cli_model = f"custom/{provider_model_name}"
            model_spec: dict[str, Any] = {
                "name": provider_model_name,
                "tool_call": self._tool_call,
            }
            if _has_image_attachments(task.attachments):
                model_spec["attachment"] = True
                model_spec["modalities"] = {
                    "input": ["text", "image"],
                    "output": ["text"],
                }
            chat_template_kwargs: dict[str, Any] = {}
            if self._enable_thinking is not None:
                chat_template_kwargs["enable_thinking"] = self._enable_thinking
            provider_config = {
                "$schema": "https://opencode.ai/config.json",
                "provider": {
                    "custom": {
                        "api": "openai",
                        "name": "Custom Provider",
                        "options": {
                            "apiKey": self._api_key,
                            "baseURL": self._api_base,
                            "timeout": self._timeout * 1000,
                            **(
                                {"temperature": self._temperature}
                                if self._temperature is not None
                                else {}
                            ),
                            **(
                                {"top_p": self._top_p}
                                if self._top_p is not None
                                else {}
                            ),
                            **(
                                {"max_tokens": self._max_tokens}
                                if self._max_tokens is not None
                                else {}
                            ),
                            **(
                                {"streaming": bool(self._streaming)}
                                if self._streaming is not None
                                else {}
                            ),
                            **(
                                {"chat_template_kwargs": chat_template_kwargs}
                                if chat_template_kwargs
                                else {}
                            ),
                        },
                        "models": {provider_model_name: model_spec},
                    }
                },
                "model": cli_model,
                "small_model": cli_model,
            }
            apply_openai_logprob_request(
                provider_config["provider"]["custom"]["options"],
                self._logprob_capture,
            )
            # Streaming stays as configured — the proxy intercepts SSE chunks to extract
            # logprobs in real time, so we don't need to disable streaming.
            proxy: LogprobCaptureProxy | None = None
            effective_api_base = self._api_base
            request_overrides = {}
            logprob_proxy_client_timeout: float | None = None
            if self._logprob_capture["enabled"]:
                # Strip any path suffix (e.g. "/v1") from api_base before handing to proxy —
                # OpenCode will append its own "/v1/chat/completions" to the proxy URL,
                # and the proxy forwards that full path to upstream unchanged.
                upstream_base = self._api_base.rstrip("/")
                if upstream_base.endswith("/v1"):
                    upstream_base = upstream_base[:-3]
                if self._temperature is not None:
                    request_overrides["temperature"] = self._temperature
                if self._top_p is not None:
                    request_overrides["top_p"] = self._top_p
                if self._max_tokens is not None:
                    request_overrides["max_tokens"] = self._max_tokens
                if chat_template_kwargs:
                    request_overrides["chat_template_kwargs"] = chat_template_kwargs
                logprob_proxy_client_timeout = max(120.0, float(self._timeout))
                proxy = LogprobCaptureProxy(
                    upstream_base,
                    self._logprob_capture["top_logprobs"],
                    client_timeout=logprob_proxy_client_timeout,
                    request_overrides=request_overrides or None,
                )
                proxy.start()
                effective_api_base = proxy.proxy_url + "/v1"
                provider_config["provider"]["custom"]["options"]["baseURL"] = effective_api_base
                provider_config["provider"]["custom"]["options"]["apiKey"] = self._api_key
            (config_dir / "opencode.json").write_text(json.dumps(provider_config, indent=2))

            if self._agent_name and (self._agent_md_path or self._agent_md_content):
                agent_dir = config_dir / "agent"
                agent_dir.mkdir(parents=True, exist_ok=True)
                if self._agent_md_path:
                    agent_path = Path(self._agent_md_path).expanduser()
                    if not agent_path.is_absolute():
                        agent_path = (Path.cwd() / agent_path).resolve()
                    agent_text = agent_path.read_text()
                else:
                    agent_text = self._agent_md_content
                (agent_dir / f"{self._agent_name}.md").write_text(agent_text)

            attachment_paths = write_attachments(workdir_path / "attachments", task.attachments)
            attachment_artifacts = [
                {
                    "filename": path.name,
                    "path": f"attachments/{path.name}",
                    "base64_artifact": f"attachments/{path.name}.base64",
                    "base64": base64.b64encode(path.read_bytes()).decode("ascii"),
                }
                for path in attachment_paths
            ]
            prompt = _build_prompt(task.problem, self._system_prompt, attachment_paths)
            request_messages: list[dict[str, Any]] = []
            if str(self._system_prompt).strip():
                request_messages.append({"role": "system", "content": self._system_prompt})
            request_messages.append({"role": "user", "content": task.problem})

            env = os.environ.copy()
            env["OPENAI_API_KEY"] = self._api_key
            env["OPENAI_BASE_URL"] = effective_api_base
            env["XDG_CONFIG_HOME"] = str(config_root)
            for var in (
                "ALL_PROXY",
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "all_proxy",
                "http_proxy",
                "https_proxy",
                "OPENAI_MODEL_NAME",
            ):
                env.pop(var, None)

            cmd = [
                self._opencode_bin,
                "run",
                "--format",
                "json",
                "--dir",
                workdir,
                "--title",
                task.task_id,
            ]
            if cli_model:
                cmd.extend(["--model", cli_model])
            if self._variant:
                cmd.extend(["--variant", self._variant])
            if self._agent_name:
                cmd.extend(["--agent", self._agent_name])
            if self._print_logs:
                cmd.append("--print-logs")
            if self._log_level:
                cmd.extend(["--log-level", self._log_level])
            for attachment_path in attachment_paths:
                cmd.extend(["--file", str(attachment_path)])
            cmd.append("--")
            cmd.append(prompt)

            logger.info("Running opencode for task %s (timeout=%ds, mode=%s)",
                        task.task_id, self._timeout, self._controller_mode)

            self._last_controller_metadata = {}
            if self._controller_mode == "docker":
                raw_output, stderr, returncode = self._run_in_docker(cmd, workdir, env)
            elif self._controller_mode == "podman":
                raw_output, stderr, returncode = self._run_in_podman(cmd, workdir, env)
            else:
                process: subprocess.Popen[str] | None = None
                try:
                    process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        stdin=subprocess.DEVNULL,
                        text=True,
                        env=env,
                        cwd=workdir,
                        start_new_session=True,
                    )
                    raw_output, stderr = process.communicate(timeout=self._timeout)
                    returncode = process.returncode
                except subprocess.TimeoutExpired:
                    logger.warning("OpenCode timed out for task %s after %ds", task.task_id, self._timeout)
                    raw_output = ""
                    stderr = f"Timeout after {self._timeout}s"
                    returncode = -1
                    if process is not None:
                        try:
                            os.killpg(process.pid, signal.SIGTERM)
                            raw_output, timed_out_stderr = process.communicate(timeout=5)
                            stderr = timed_out_stderr or stderr
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, signal.SIGKILL)
                            raw_output, timed_out_stderr = process.communicate()
                            stderr = timed_out_stderr or stderr
                        except ProcessLookupError:
                            raw_output, timed_out_stderr = process.communicate()
                            stderr = timed_out_stderr or stderr
            if proxy is not None:
                proxy_logprob_records = proxy.captured_records()
                proxy.stop()

            _, _, session_id = _parse_opencode_output(raw_output)
            preserved_local_artifacts = _collect_local_opencode_artifacts(config_dir, session_id)
            session_trace = preserved_local_artifacts.get("workspace_file_contents", {}).get("opencode_session.jsonl", "")

        wall_time = time.time() - start
        assistant_text, events, session_id = _parse_opencode_output(raw_output)
        transport = (
            "opencode_cli_container"
            if self._controller_mode == "docker"
            else ("opencode_cli_podman" if self._controller_mode == "podman" else "opencode_cli")
        )

        error_info = _extract_opencode_error(events)
        response_metadata = {
            "returncode": returncode,
            "stderr": stderr[:2000] if stderr else "",
            "num_events": len(events),
            "logprob_probe_event_count": len(events),
            "logprob_probe_session_json_count": 0,
            "logprob_probe_stdout_json_count": 0,
            "logprob_probe_record_count": 0,
            "session_id": session_id,
            "num_attachments": len(attachment_paths),
            "controller_mode": self._controller_mode,
            "transport": transport,
            "logprob_support": "enabled" if self._logprob_capture["enabled"] else "disabled",
        }
        if self._controller_mode == "podman":
            response_metadata.update(self._last_controller_metadata)
        if request_overrides:
            response_metadata["logprob_proxy_request_overrides"] = request_overrides
        if logprob_proxy_client_timeout is not None:
            response_metadata["logprob_proxy_client_timeout"] = logprob_proxy_client_timeout
        logprob_probe_session_json_count = _count_json_objects(session_trace)
        logprob_probe_stdout_json_count = _count_json_objects(raw_output)
        logprob_records = extract_opencode_logprob_records(
            events=events,
            session_trace=session_trace,
            stdout=raw_output,
        )
        if not logprob_records and proxy_logprob_records:
            logprob_records = proxy_logprob_records
        response_metadata["logprob_probe_session_json_count"] = logprob_probe_session_json_count
        response_metadata["logprob_probe_stdout_json_count"] = logprob_probe_stdout_json_count
        response_metadata["logprob_probe_record_count"] = len(logprob_records)
        response_metadata["logprob_probe_proxy_count"] = len(proxy_logprob_records)
        token_entropy_stats, response_metadata = finalize_logprob_capture(
            harness="opencode",
            enabled=self._logprob_capture["enabled"],
            records=logprob_records,
            metadata=response_metadata,
        )
        partial_output = _text_from_logprob_records(logprob_records)
        if partial_output.strip():
            response_metadata.update({
                "partial_output_source": "logprob_records",
                "partial_output_chars": len(partial_output),
                "opencode_event_text_chars": len(assistant_text),
                "partial_output_used_for_raw_output": False,
            })

        full_content = assistant_text or raw_output
        if (
            partial_output.strip()
            and (returncode != 0 or error_info)
            and (
                not assistant_text.strip()
                or len(partial_output.strip()) > len(full_content.strip())
            )
        ):
            full_content = partial_output
            response_metadata["partial_output_used_for_raw_output"] = True

        trajectory, reasoning_trajectory = build_event_trajectories(
            request_messages,
            events,
            final_output=full_content,
        )

        if _is_swe_bench_task(task):
            answer = _extract_patch_from_text(full_content)
        elif returncode == -1:
            answer = _extract_strict_answer(full_content)
        else:
            answer = extract_answer_candidate(full_content)

        if returncode != 0 and not answer:
            logger.warning(
                "OpenCode returned non-zero exit code %d for task %s. stderr: %s",
                returncode,
                task.task_id,
                stderr[:500],
            )
        timeout_info = None
        if returncode == -1 and error_info is None:
            timeout_info = {
                "name": "OpenCodeTimeout",
                "message": "The operation timed out.",
                "error_type": "timeout",
            }
            answer = None

        failure_info = error_info
        if returncode != 0 and returncode != -1 and failure_info is None:
            failure_info = {
                "name": "OpenCodeNonZeroExit",
                "message": f"OpenCode exited with return code {returncode}.",
                "error_type": "agent_error",
            }

        workspace_file_contents: dict[str, str] = dict(
            preserved_local_artifacts.get("workspace_file_contents", {}) or {}
        )
        workspace_file_contents.update({
            "prompt.txt": prompt,
            "opencode_output.jsonl": raw_output,
        })
        if partial_output.strip():
            workspace_file_contents["opencode_partial_output.txt"] = partial_output
        if self._controller_mode == "podman" and response_metadata.get("container_logs_collected"):
            workspace_file_contents["opencode_podman_container.log"] = str(
                response_metadata.get("container_log_excerpt") or ""
            )
        if stderr:
            workspace_file_contents["opencode_stderr.log"] = stderr
        if attachment_paths:
            for item in attachment_artifacts:
                workspace_file_contents[item["base64_artifact"]] = item["base64"]
            workspace_file_contents["attachment_manifest.json"] = json.dumps(
                {
                    "attachments": [
                        {
                            "filename": item["filename"],
                            "path": item["path"],
                            "base64_artifact": item["base64_artifact"],
                        }
                        for item in attachment_artifacts
                    ]
                },
                indent=2,
                ensure_ascii=False,
            )
        artifact_manifest = add_artifact_file_refs(
            preserved_local_artifacts.get("artifact_manifest", {}),
            response_stream="opencode_output.jsonl",
            opencode_output="opencode_output.jsonl",
            partial_output=(
                "opencode_partial_output.txt"
                if "opencode_partial_output.txt" in workspace_file_contents
                else None
            ),
            session_trace=(
                "opencode_session.jsonl"
                if "opencode_session.jsonl" in workspace_file_contents
                else None
            ),
            opencode_session=(
                "opencode_session.jsonl"
                if "opencode_session.jsonl" in workspace_file_contents
                else None
            ),
            podman_container_log=(
                "opencode_podman_container.log"
                if "opencode_podman_container.log" in workspace_file_contents
                else None
            ),
            stderr_log="opencode_stderr.log" if stderr else None,
            prompt_text="prompt.txt",
            attachment_manifest="attachment_manifest.json" if attachment_paths else None,
        )
        response_json = build_runtime_trace_summary(
            output_text=full_content,
            stderr_text=stderr.strip(),
            records=events,
            extra={
                "returncode": returncode,
                "session_id": session_id,
                "transport": transport,
                "partial_output_source": response_metadata.get("partial_output_source", ""),
                "partial_output_chars": response_metadata.get("partial_output_chars", 0),
                "partial_output_used_for_raw_output": response_metadata.get(
                    "partial_output_used_for_raw_output",
                    False,
                ),
            },
        )
        status_info = failure_info or timeout_info
        if status_info:
            response_metadata.update({
                "opencode_error_name": status_info["name"],
                "opencode_error_message": status_info["message"],
            })
            response_json = {
                **response_json,
                "opencode_error_name": status_info["name"],
                "opencode_error_message": status_info["message"],
            }
        if timeout_info:
            response_metadata.update({
                "opencode_timeout_scored_zero": True,
                "opencode_timeout_seconds": self._timeout,
            })
            response_json = {
                **response_json,
                "opencode_timeout_scored_zero": True,
                "opencode_timeout_seconds": self._timeout,
            }

        response = AgentResponse(
            answer=None if failure_info else answer,
            trajectory=trajectory,
            reasoning_trajectory=reasoning_trajectory,
            raw_output=full_content,
            wall_time_sec=wall_time,
            token_entropy_stats=token_entropy_stats,
            metadata=response_metadata,
            request_messages=request_messages,
            response_json=response_json,
            artifact_manifest=artifact_manifest,
            workspace_file_contents=workspace_file_contents,
            system_prompt=str(self._system_prompt),
            finish_reason="timeout" if timeout_info and not failure_info else "",
        )
        if failure_info:
            exc = RuntimeError(failure_info["message"])
            setattr(exc, "partial_response", response)
            setattr(exc, "error_type", failure_info["error_type"])
            setattr(exc, "response_body", full_content)
            raise exc
        return response

    def teardown(self) -> None:
        pass


AgentRegistry.register("opencode", OpenCodeAgent)
