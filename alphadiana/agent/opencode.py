"""OpenCode agent wrapper with native multimodal support and SWE-bench container mode."""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import socketserver
import subprocess
import tempfile
import threading
import time
import http.server
from pathlib import Path
from typing import Any

import httpx

from alphadiana.agent.logprob_capture import (
    apply_openai_logprob_request,
    extract_openai_logprob_records,
    finalize_logprob_capture,
    raw_token_logprob_dict_to_record,
    resolve_logprob_capture_config,
)
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


class _LogprobProxyHandler(http.server.BaseHTTPRequestHandler):
    server: "_LogprobCaptureServer"

    def _forward_headers(self, resp: httpx.Response, *, skip_length: bool = False) -> None:
        self.send_response(resp.status_code)
        for k, v in resp.headers.items():
            kl = k.lower()
            if kl in ("transfer-encoding", "content-encoding"):
                continue
            if skip_length and kl == "content-length":
                continue
            self.send_header(k, v)
        self.end_headers()

    def _extract_sse_logprobs(self, line: str) -> None:
        """Parse one SSE data line and append any logprob token dicts to the server list."""
        if not line.startswith("data: ") or line == "data: [DONE]":
            return
        try:
            data = json.loads(line[6:])
            choices = data.get("choices", [])
            if choices:
                lp_content = (choices[0].get("logprobs") or {}).get("content") or []
                if lp_content:
                    with self.server.lock:
                        self.server.streaming_logprobs.extend(lp_content)
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        path = self.path
        is_completions = "/chat/completions" in path

        if is_completions:
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                payload = {}
            payload["logprobs"] = True
            payload["top_logprobs"] = self.server.top_logprobs
            # Keep stream / stream_options unchanged — removing stream causes vLLM to
            # reject stream_options; removing both forces full buffering before OpenCode
            # gets any tokens, making long generations take 10x longer.
            body = json.dumps(payload).encode()

        fwd_url = self.server.upstream.rstrip("/") + path
        fwd_headers = {
            k: v
            for k, v in self.headers.items()
            if k.lower() not in ("host", "transfer-encoding", "content-length")
        }
        fwd_headers["Content-Length"] = str(len(body))

        try:
            with self.server.client.stream(
                "POST", fwd_url, content=body, headers=fwd_headers
            ) as resp:
                is_sse = "text/event-stream" in resp.headers.get("content-type", "")

                if resp.status_code >= 400:
                    # Buffer error body for logging, then forward
                    err_body = resp.read()
                    logger.warning(
                        "LogprobProxy upstream %s → %d sent=%s err=%s",
                        fwd_url, resp.status_code, body[:300], err_body[:300],
                    )
                    self.send_response(resp.status_code)
                    for k, v in resp.headers.items():
                        if k.lower() in ("transfer-encoding", "content-encoding"):
                            continue
                    self.send_header("Content-Length", str(len(err_body)))
                    self.end_headers()
                    self.wfile.write(err_body)
                    return

                if is_sse and is_completions:
                    # Stream SSE to client while parsing each event for logprobs.
                    # We forward raw bytes immediately so OpenCode gets tokens in real
                    # time; we also maintain a sliding line buffer to parse SSE events.
                    self._forward_headers(resp, skip_length=True)
                    sse_buf = ""
                    for raw in resp.iter_bytes(chunk_size=512):
                        self.wfile.write(raw)
                        self.wfile.flush()
                        sse_buf += raw.decode("utf-8", errors="replace")
                        while "\n" in sse_buf:
                            line, sse_buf = sse_buf.split("\n", 1)
                            self._extract_sse_logprobs(line.rstrip("\r"))
                else:
                    # Non-streaming: buffer full body, parse logprobs, forward.
                    resp_body = resp.read()
                    if is_completions:
                        try:
                            with self.server.lock:
                                self.server.raw_responses.append(json.loads(resp_body))
                        except (json.JSONDecodeError, ValueError):
                            pass
                    self.send_response(resp.status_code)
                    for k, v in resp.headers.items():
                        if k.lower() in ("transfer-encoding", "content-encoding"):
                            continue
                    self.send_header("Content-Length", str(len(resp_body)))
                    self.end_headers()
                    self.wfile.write(resp_body)
        except Exception as exc:
            try:
                self.send_error(502, str(exc))
            except Exception:
                pass

    def do_GET(self) -> None:
        fwd_url = self.server.upstream.rstrip("/") + self.path
        fwd_headers = {k: v for k, v in self.headers.items() if k.lower() != "host"}
        try:
            resp = self.server.client.get(fwd_url, headers=fwd_headers)
        except Exception as exc:
            self.send_error(502, str(exc))
            return
        resp_body = resp.content
        self.send_response(resp.status_code)
        for k, v in resp.headers.items():
            if k.lower() in ("transfer-encoding", "content-encoding"):
                continue
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(resp_body)))
        self.end_headers()
        self.wfile.write(resp_body)

    def log_message(self, *args: object) -> None:
        pass


class _LogprobCaptureServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

    def __init__(self, upstream: str, top_logprobs: int) -> None:
        super().__init__(("127.0.0.1", 0), _LogprobProxyHandler)
        self.upstream = upstream
        self.top_logprobs = top_logprobs
        self.raw_responses: list[dict] = []      # non-streaming full responses
        self.streaming_logprobs: list[dict] = [] # per-token dicts from SSE events
        self.lock = threading.Lock()
        self.client = httpx.Client(timeout=120.0)


class LogprobCaptureProxy:
    def __init__(self, upstream: str, top_logprobs: int) -> None:
        self._upstream = upstream
        self._top_logprobs = top_logprobs
        self._server: _LogprobCaptureServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._server = _LogprobCaptureServer(self._upstream, self._top_logprobs)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.client.close()

    @property
    def proxy_url(self) -> str:
        assert self._server is not None
        port = self._server.server_address[1]
        return f"http://127.0.0.1:{port}"

    def captured_records(self) -> list[dict]:
        assert self._server is not None
        records: list[dict] = []
        token_index = 0
        with self._server.lock:
            sse_tokens = list(self._server.streaming_logprobs)
            payloads = list(self._server.raw_responses)
        # Prefer SSE per-token stream (real tokens in order); fall back to buffered JSON.
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
        self._logprob_capture = resolve_logprob_capture_config(config)
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

        artifact_manifest = add_artifact_file_refs(
            artifacts.get("artifact_manifest", {}),
            response_stream="/swebench_agent/opencode/opencode_output.jsonl",
            session_trace="/swebench_agent/opencode/opencode_session.jsonl",
            stderr_log="/swebench_agent/opencode/opencode_stderr.log",
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
        session_trace = ""
        proxy_logprob_records: list[dict] = []

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
            apply_openai_logprob_request(
                provider_config["provider"]["custom"]["options"],
                self._logprob_capture,
            )
            # Streaming stays as configured — the proxy intercepts SSE chunks to extract
            # logprobs in real time, so we don't need to disable streaming.
            proxy: LogprobCaptureProxy | None = None
            effective_api_base = self._api_base
            if self._logprob_capture["enabled"]:
                # Strip any path suffix (e.g. "/v1") from api_base before handing to proxy —
                # OpenCode will append its own "/v1/chat/completions" to the proxy URL,
                # and the proxy forwards that full path to upstream unchanged.
                upstream_base = self._api_base.rstrip("/")
                if upstream_base.endswith("/v1"):
                    upstream_base = upstream_base[:-3]
                proxy = LogprobCaptureProxy(upstream_base, self._logprob_capture["top_logprobs"])
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
            if proxy is not None:
                proxy_logprob_records = proxy.captured_records()
                proxy.stop()

            _, _, session_id = _parse_opencode_output(raw_output)
            session_trace = ""
            session_dirs = [
                config_dir / "sessions",
                container_home / ".local" / "share" / "opencode" / "sessions",
                container_home / ".config" / "opencode" / "sessions",
                workdir_path / ".opencode" / "sessions",
            ]
            for session_dir in session_dirs:
                session_trace, _ = _read_opencode_session_trace(
                    session_dir,
                    session_id,
                )
                if session_trace:
                    break

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

        workspace_file_contents: dict[str, str] = {
            "prompt.txt": prompt,
            "opencode_output.jsonl": raw_output,
        }
        if session_trace:
            workspace_file_contents["opencode_session.jsonl"] = session_trace
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
            {},
            response_stream="opencode_output.jsonl",
            session_trace="opencode_session.jsonl" if session_trace else None,
            opencode_output="opencode_output.jsonl",
            opencode_session="opencode_session.jsonl" if session_trace else None,
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
            "logprob_probe_event_count": len(events),
            "logprob_probe_session_json_count": 0,
            "logprob_probe_stdout_json_count": 0,
            "logprob_probe_record_count": 0,
            "session_id": session_id,
            "num_attachments": len(attachment_paths),
            "controller_mode": self._controller_mode,
            "transport": transport,
        }
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
