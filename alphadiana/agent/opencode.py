"""OpenCode agent wrapper with native multimodal support and SWE-bench container mode."""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from alphadiana.agent.base import Agent, AgentResponse
from alphadiana.agent.preservation import (
    add_artifact_file_refs,
    build_event_trajectories,
    build_runtime_trace_summary,
)
from alphadiana.agent.registry import AgentRegistry
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.utils.attachments import iter_binary_attachments, write_attachments
from alphadiana.utils.math_answer import extract_answer_candidate, extract_boxed

logger = logging.getLogger(__name__)

_SUPPORTED_CONTROLLER_MODES = {"host", "docker"}

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
    inside a container instead of directly on the host.  Requires the controller
    image (default ``alphadiana/tb2-opencode-controller:latest``).

    Build with::

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
        self._controller_image = str(
            config.get("controller_image", "alphadiana/tb2-opencode-controller:latest")
            or "alphadiana/tb2-opencode-controller:latest"
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
        self._variant = str(config.get("variant", "")).strip()
        self._agent_name = str(config.get("agent", "")).strip()
        self._agent_md_path = str(config.get("agent_md_path", "")).strip()
        self._agent_md_content = str(config.get("agent_md_content", "")).strip()
        self._print_logs = bool(config.get("print_logs", False))
        self._log_level = str(config.get("log_level", "")).strip()
        self._system_prompt = config.get("system_prompt", _DEFAULT_SYSTEM_PROMPT)
        self._opencode_bin = config.get("opencode_bin", "opencode")
        self._streaming = config.get("streaming") if "streaming" in config else None
        self._config = dict(config)
        self._runtime_manager = None

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
            "session_id": session_id,
            "patch_source": "git_diff" if patch_from_git else "text_extraction",
            "transport": "opencode_cli_container",
            **artifacts,
        }
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
        docker_cmd = [
            "docker", "run", "--rm",
            f"--network={self._controller_network}",
            f"--user={uid}:{gid}",
            "-v", f"{workdir}:{workdir}",
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

    def _solve_cli(self, task: BenchmarkTask) -> AgentResponse:
        """Run tasks through the OpenCode CLI (host or Docker, multimodal-aware)."""
        start = time.time()
        preserved_local_artifacts: dict[str, Any] = {}

        with tempfile.TemporaryDirectory(prefix="opencode-task-") as workdir:
            workdir_path = Path(workdir)
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
                                {"streaming": bool(self._streaming)}
                                if self._streaming is not None
                                else {}
                            ),
                        },
                        "models": {provider_model_name: model_spec},
                    }
                },
                "model": cli_model,
                "small_model": cli_model,
            }
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
            prompt = _build_prompt(task.problem, self._system_prompt, attachment_paths)
            request_messages: list[dict[str, Any]] = []
            if str(self._system_prompt).strip():
                request_messages.append({"role": "system", "content": self._system_prompt})
            request_messages.append({"role": "user", "content": task.problem})

            env = os.environ.copy()
            env["OPENAI_API_KEY"] = self._api_key
            env["OPENAI_BASE_URL"] = self._api_base
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

            if self._controller_mode == "docker":
                raw_output, stderr, returncode = self._run_in_docker(cmd, workdir, env)
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

            _, _, session_id = _parse_opencode_output(raw_output)
            preserved_local_artifacts = _collect_local_opencode_artifacts(config_dir, session_id)

        wall_time = time.time() - start
        assistant_text, events, session_id = _parse_opencode_output(raw_output)
        full_content = assistant_text or raw_output
        trajectory, reasoning_trajectory = build_event_trajectories(
            request_messages,
            events,
            final_output=full_content,
        )
        transport = (
            "opencode_cli_container"
            if self._controller_mode == "docker"
            else "opencode_cli"
        )

        if _is_swe_bench_task(task):
            answer = _extract_patch_from_text(full_content)
        elif returncode == -1:
            answer = _extract_strict_answer(assistant_text)
        else:
            answer = extract_answer_candidate(full_content)

        if returncode != 0 and not answer:
            logger.warning(
                "OpenCode returned non-zero exit code %d for task %s. stderr: %s",
                returncode,
                task.task_id,
                stderr[:500],
            )

        workspace_file_contents: dict[str, str] = dict(
            preserved_local_artifacts.get("workspace_file_contents", {}) or {}
        )
        workspace_file_contents.update({
            "prompt.txt": prompt,
            "opencode_output.jsonl": raw_output,
        })
        if stderr:
            workspace_file_contents["opencode_stderr.log"] = stderr
        if attachment_paths:
            workspace_file_contents["attachment_manifest.json"] = json.dumps(
                {
                    "attachments": [
                        {
                            "filename": path.name,
                            "path": f"attachments/{path.name}",
                        }
                        for path in attachment_paths
                    ]
                },
                indent=2,
                ensure_ascii=False,
            )
        artifact_manifest = add_artifact_file_refs(
            preserved_local_artifacts.get("artifact_manifest", {}),
            response_stream="opencode_output.jsonl",
            session_trace=(
                "opencode_session.jsonl"
                if "opencode_session.jsonl" in workspace_file_contents
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
            },
        )

        error_info = _extract_opencode_error(events)
        response_metadata = {
            "returncode": returncode,
            "stderr": stderr[:2000] if stderr else "",
            "num_events": len(events),
            "session_id": session_id,
            "num_attachments": len(attachment_paths),
            "controller_mode": self._controller_mode,
            "transport": transport,
        }
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
            metadata=response_metadata,
            request_messages=request_messages,
            response_json=response_json,
            artifact_manifest=artifact_manifest,
            workspace_file_contents=workspace_file_contents,
            system_prompt=str(self._system_prompt),
        )
        if error_info:
            exc = RuntimeError(error_info["message"])
            setattr(exc, "partial_response", response)
            setattr(exc, "error_type", error_info["error_type"])
            setattr(exc, "response_body", full_content)
            raise exc
        return response

    def teardown(self) -> None:
        pass


AgentRegistry.register("opencode", OpenCodeAgent)
