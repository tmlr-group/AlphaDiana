"""SWE-bench Docker agent.

Phase 9 introduces the Docker lifecycle and image-resolution contract used by
SWE-bench Pro runs. Direct patch generation is added in a later step.
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import os
import random
import re
import shlex
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from alphadiana.agent.base import Agent, AgentResponse
from alphadiana.agent.registry import register_agent
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.utils.rock_runtime import PREBUILT_SANDBOX_IMAGE

logger = logging.getLogger(__name__)

_DEFAULT_SYSTEM_PROMPT = (
    "You are a software engineering assistant working on a SWE-bench task. "
    "Return only a unified diff patch that solves the issue. "
    "Do not include prose, markdown fences, or explanations."
)
_THINK_TAG_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)
_FENCED_BLOCK_RE = re.compile(r"```(?:diff|patch)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_ENV_PLACEHOLDER_RE = re.compile(
    r"^\$(?:\{[A-Za-z_][A-Za-z0-9_]*\}|[A-Za-z_][A-Za-z0-9_]*)$"
)
_ASSET_ROOT = Path(__file__).resolve().parent / "swebench_assets"
_IN_CONTAINER_AGENT_TYPES = {"openclaw", "opencode", "zeroclaw"}
_REMOTE_AGENT_ROOT = "/tmp/alphadiana-swebench"
_OPENCLAW_RUNNER = _ASSET_ROOT / "run_openclaw.sh"
_OPENCLAW_CONFIG_TEMPLATE = _ASSET_ROOT / "openclaw.json"
_OPENCODE_RUNNER = _ASSET_ROOT / "run_opencode.sh"
_OPENCODE_CONFIG_TEMPLATE = _ASSET_ROOT / "opencode.json.template"
_ZEROCLAW_RUNNER = _ASSET_ROOT / "run_zeroclaw.sh"
_ZEROCLAW_CONFIG_TEMPLATE = _ASSET_ROOT / "zeroclaw.toml.template"
_OPENCLAW_RUNTIME_IMAGE = os.environ.get(
    "SWEBENCH_OPENCLAW_RUNTIME_IMAGE",
    PREBUILT_SANDBOX_IMAGE,
)
_OPENCODE_RUNTIME_IMAGE = os.environ.get(
    "SWEBENCH_OPENCODE_RUNTIME_IMAGE",
    "tmlrgroup/alphadiana:opencode",
)
_ZEROCLAW_RUNTIME_IMAGE = os.environ.get(
    "SWEBENCH_ZEROCLAW_RUNTIME_IMAGE",
    "zeroclaw-reasoning:0.6.9",
)
_RUNTIME_IMAGE_REPO = os.environ.get(
    "SWEBENCH_RUNTIME_IMAGE_REPO",
    "alphadiana-swebench-runtime",
)
_OPENCLAW_MIN_COMPLETION_TOKENS = 512
_OPENCLAW_MAX_COMPLETION_TOKENS = 8192
_OPENCLAW_LIST_SECTION_LIMIT = 20
_EDIT_FIRST_LIST_SECTION_LIMIT = 10
_OPENCODE_TARGET_HINT_LIMIT = 6
_PROMPT_HEADROOM_MARGIN_TOKENS = 64
_TRUNCATION_MARKER = "...[truncated for context budget]"


def _run(cmd: list[str], timeout: int | None = None, **kwargs: Any) -> subprocess.CompletedProcess:
    """Run a subprocess command with captured text output."""
    logger.debug("Running: %s", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kwargs)


def _is_blank_env_value(value: Any) -> bool:
    """Return True when a config/env value is blank or an unresolved placeholder."""
    if value is None:
        return True
    if isinstance(value, str):
        stripped = value.strip()
        return (
            not stripped
            or stripped.upper() == "EMPTY"
            or bool(_ENV_PLACEHOLDER_RE.fullmatch(stripped))
        )
    return False


def _split_candidate_aliases(value: Any) -> list[str]:
    """Normalize comma/newline separated model aliases while preserving order."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        items = [str(item).strip() for item in value]
    else:
        items = str(value).replace("\n", ",").split(",")
    aliases: list[str] = []
    for item in items:
        alias = str(item).strip()
        if alias and alias not in aliases:
            aliases.append(alias)
    return aliases


def _safe_artifact_fragment(value: str) -> str:
    """Convert arbitrary identifiers into filesystem-safe path fragments."""
    fragment = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value).strip()).strip("-")
    return fragment or "value"


def _extract_prefixed_value(text: str, prefix: str) -> str:
    """Extract the value from a `key: value` style text artifact."""
    for line in text.splitlines():
        if line.startswith(prefix):
            return line.split(": ", 1)[1].strip()
    return ""


@register_agent("swebench_docker")
class SWEBenchDockerAgent(Agent):
    """Agent that manages per-task SWE-bench containers.

    Config keys:
        agent_type: str — execution mode (default: "direct_llm")
        dockerhub_repo: str — image repo prefix (default: "jefzda/sweap-images")
        image: str — explicit image override (optional)
        request_timeout: int — max allowed task time in seconds
        env: dict — environment variables reserved for future container modes
        output_dir: str — local artifact directory reserved for later phases
    """

    name = "swebench_docker"

    def __init__(self) -> None:
        self._agent_type: str = "direct_llm"
        self._dockerhub_repo: str = "jefzda/sweap-images"
        self._image_override: str = ""
        self._timeout: int = 1800
        self._env: dict[str, str] = {}
        self._output_dir: str = "./swebench_artifacts"
        self._model: str = ""
        self._api_base: str = ""
        self._api_key: str = "EMPTY"
        self._temperature: float = 0.2
        self._top_p: float | None = None
        self._max_tokens: int | None = None
        self._max_completion_tokens: int | None = None
        self._max_retries: int = 3
        self._stream: bool = True
        self._resolved_max_tokens: int | None = None
        self._max_model_len: int | None = None
        self._model_context_windows: dict[str, int] = {}
        self._system_prompt: str = _DEFAULT_SYSTEM_PROMPT
        self._client: Any = None
        self._stream_options_supported: bool = True

    def setup(self, config: dict) -> None:
        self._agent_type = str(config.get("agent_type", "direct_llm")).strip() or "direct_llm"
        self._dockerhub_repo = str(
            config.get("dockerhub_repo", "jefzda/sweap-images")
        ).strip() or "jefzda/sweap-images"
        self._image_override = str(config.get("image", "")).strip()
        self._timeout = int(config.get("request_timeout", 1800))
        self._env = {
            str(key): str(value).strip()
            for key, value in dict(config.get("env", {})).items()
            if str(key).strip() and not _is_blank_env_value(value)
        }
        self._output_dir = str(config.get("output_dir", "./swebench_artifacts")).strip()
        self._model = self._resolve_setting(config, "model", "OPENAI_MODEL_NAME")
        self._api_base = self._resolve_setting(config, "api_base", "OPENAI_BASE_URL")
        self._api_key = self._resolve_setting(
            config, "api_key", "OPENAI_API_KEY", default="EMPTY"
        )
        self._temperature = float(config.get("temperature", 0.2))
        self._top_p = config.get("top_p", None)
        self._max_tokens = config.get("max_tokens", None)
        self._max_completion_tokens = config.get("max_completion_tokens", None)
        self._max_retries = int(config.get("max_retries", 3))
        self._stream = bool(config.get("stream", True))
        self._system_prompt = str(
            config.get("system_prompt", _DEFAULT_SYSTEM_PROMPT)
        ).strip() or _DEFAULT_SYSTEM_PROMPT

        logger.info(
            "SWEBenchDockerAgent configured: agent_type=%s image_override=%s dockerhub_repo=%s timeout=%ds",
            self._agent_type,
            self._image_override or "<auto>",
            self._dockerhub_repo,
            self._timeout,
        )

        if self._agent_type == "direct_llm":
            try:
                from openai import OpenAI

                self._client = OpenAI(base_url=self._api_base, api_key=self._api_key)
            except ImportError:
                self._client = None

    def solve(self, task: BenchmarkTask, sandbox: Optional[Any] = None) -> AgentResponse:
        start_time = time.monotonic()
        base_image = self._resolve_image(task)
        image = base_image
        runtime_metadata: dict[str, Any] = {}
        if self._agent_type in _IN_CONTAINER_AGENT_TYPES:
            image, runtime_metadata = self._prepare_runtime_image(base_image)
        container_id = ""
        try:
            self._docker_pull(image)
            container_id = self._docker_create(image)
            self._docker_start(container_id)
            if self._agent_type == "direct_llm":
                return self._solve_direct_llm(task, image, container_id, start_time)
            if self._agent_type == "openclaw":
                return self._solve_openclaw(
                    task,
                    base_image,
                    image,
                    container_id,
                    start_time,
                    runtime_metadata,
                )
            if self._agent_type == "opencode":
                return self._solve_opencode(
                    task,
                    base_image,
                    image,
                    container_id,
                    start_time,
                    runtime_metadata,
                )
            if self._agent_type == "zeroclaw":
                return self._solve_zeroclaw(
                    task,
                    base_image,
                    image,
                    container_id,
                    start_time,
                    runtime_metadata,
                )
            raise NotImplementedError(
                f"swebench_docker agent_type {self._agent_type!r} is not implemented yet"
            )
        finally:
            if container_id:
                self._docker_stop(container_id)
                self._docker_rm(container_id)
            wall_time = time.monotonic() - start_time
            logger.info(
                "Task %s: swebench_docker lifecycle finished in %.1fs",
                task.task_id,
                wall_time,
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
            if not _is_blank_env_value(stripped):
                return stripped
        env_value = os.environ.get(env_var, "").strip()
        if env_value:
            return env_value
        if isinstance(value, str) and not _is_blank_env_value(value):
            return value.strip()
        return default

    def _resolve_image(self, task: BenchmarkTask) -> str:
        """Resolve the Docker image for a SWE-bench task."""
        if self._image_override:
            return self._image_override

        dockerhub_tag = str(task.metadata.get("dockerhub_tag", "")).strip()
        if not dockerhub_tag:
            raise ValueError(
                "swebench_docker requires task.metadata['dockerhub_tag'] unless "
                "agent.config.image is set explicitly."
            )
        return f"{self._dockerhub_repo}:{dockerhub_tag}"

    def _docker_pull(self, image: str) -> None:
        """Pull the required image before execution."""
        if self._docker_image_exists(image):
            return
        result = _run(["docker", "pull", image], timeout=min(self._timeout, 1800))
        if result.returncode != 0:
            raise RuntimeError(f"docker pull failed for {image}: {result.stderr.strip()}")

    def _docker_image_exists(self, image: str) -> bool:
        """Return whether the image already exists locally."""
        result = _run(["docker", "image", "inspect", image], timeout=30)
        return result.returncode == 0

    def _docker_build_image(
        self,
        image: str,
        dockerfile: str,
        *,
        build_args: dict[str, str] | None = None,
    ) -> None:
        """Build a Docker image from an inline Dockerfile."""
        with tempfile.TemporaryDirectory(prefix="alphadiana-swebench-build-") as context_dir:
            cmd = ["docker", "build", "--tag", image]
            for key, value in (build_args or {}).items():
                cmd.extend(["--build-arg", f"{key}={value}"])
            cmd.extend(["-f-", context_dir])
            result = _run(
                cmd,
                timeout=min(self._timeout, 3600),
                input=dockerfile,
            )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(f"docker build failed for {image}: {detail}")

    def _docker_create(self, image: str) -> str:
        """Create a container and return its ID."""
        cmd = ["docker", "create"]
        for key, value in self._env.items():
            cmd.extend(["-e", f"{key}={value}"])
        if self._agent_type in _IN_CONTAINER_AGENT_TYPES:
            cmd.extend(["--entrypoint", "bash"])
        cmd.append(image)
        if self._agent_type in _IN_CONTAINER_AGENT_TYPES:
            cmd.extend(["-lc", "trap 'exit 0' TERM INT; while true; do sleep 3600; done"])
        result = _run(cmd, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(f"docker create failed for {image}: {result.stderr.strip()}")
        container_id = result.stdout.strip()
        if not container_id:
            raise RuntimeError(f"docker create returned an empty container id for {image}")
        return container_id

    def _docker_start(self, container_id: str) -> None:
        """Start a created container."""
        result = _run(["docker", "start", container_id], timeout=60)
        if result.returncode != 0:
            raise RuntimeError(
                f"docker start failed for {container_id}: {result.stderr.strip()}"
            )

    def _docker_stop(self, container_id: str) -> None:
        """Best-effort stop for an existing container."""
        result = _run(["docker", "stop", "-t", "10", container_id], timeout=30)
        if result.returncode != 0:
            logger.warning("docker stop failed for %s: %s", container_id, result.stderr.strip())

    def _docker_rm(self, container_id: str) -> None:
        """Best-effort forced removal for a container."""
        result = _run(["docker", "rm", "-f", container_id], timeout=30)
        if result.returncode != 0:
            logger.warning("docker rm failed for %s: %s", container_id, result.stderr.strip())

    def _docker_exec(
        self,
        container_id: str,
        args: list[str],
        *,
        timeout: int | None = None,
        check: bool = True,
        env: dict[str, str] | None = None,
        workdir: str | None = None,
    ) -> subprocess.CompletedProcess:
        """Execute a command inside a running container."""
        cmd = ["docker", "exec"]
        if workdir:
            cmd.extend(["-w", workdir])
        if env:
            for key, value in env.items():
                cmd.extend(["-e", f"{key}={value}"])
        cmd.append(container_id)
        cmd.extend(args)
        result = _run(cmd, timeout=timeout or self._timeout)
        if check and result.returncode != 0:
            raise RuntimeError(
                "docker exec failed: "
                f"{' '.join(cmd[2:])}: {result.stderr.strip() or result.stdout.strip()}"
            )
        return result

    def _docker_cp_to(self, container_id: str, src: str, dst: str) -> None:
        """Copy a local file or directory into the container."""
        result = _run(["docker", "cp", src, f"{container_id}:{dst}"], timeout=120)
        if result.returncode != 0:
            raise RuntimeError(f"docker cp to container failed: {result.stderr.strip()}")

    def _docker_cp_from(self, container_id: str, src: str, dst: str) -> None:
        """Copy a file or directory out of the container."""
        result = _run(["docker", "cp", f"{container_id}:{src}", dst], timeout=120)
        if result.returncode != 0:
            raise RuntimeError(f"docker cp from container failed: {result.stderr.strip()}")

    def _detect_repo_root(self, container_id: str) -> str:
        """Detect the git worktree root inside the SWE-bench container."""
        script = r"""
set -e
for base in "$PWD" /workspace /repo /app /project /workdir /root/project /root/repo /tmp; do
  if [ -n "$base" ] && [ -d "$base" ]; then
    root=$(git -C "$base" rev-parse --show-toplevel 2>/dev/null || true)
    if [ -n "$root" ]; then
      printf '%s\n' "$root"
      exit 0
    fi
  fi
done
gitdir=$(find /workspace /repo /app /project /workdir /root /tmp -maxdepth 4 -name .git -type d 2>/dev/null | head -n 1 || true)
if [ -n "$gitdir" ]; then
  dirname "$gitdir"
  exit 0
fi
exit 1
"""
        result = self._docker_exec(
            container_id,
            ["bash", "-lc", script],
            timeout=120,
            check=False,
        )
        repo_root = result.stdout.strip()
        if result.returncode != 0 or not repo_root:
            raise RuntimeError(
                "Failed to detect git repo root inside the SWE-bench container."
            )
        return repo_root

    @staticmethod
    def _sample_index(task: BenchmarkTask) -> int:
        """Return the normalized sample index attached by the runner."""
        raw_sample_index = task.metadata.get("sample_index", 0)
        try:
            return max(0, int(raw_sample_index))
        except (TypeError, ValueError):
            return 0

    def _prepare_local_artifacts_dir(self, task: BenchmarkTask) -> tuple[Path, int, str]:
        """Create a unique local artifact directory for this solve attempt."""
        sample_index = self._sample_index(task)
        execution_id = str(task.metadata.get("execution_id") or uuid4().hex)
        path = (
            Path(self._output_dir)
            / task.task_id
            / f"sample_{sample_index}"
            / execution_id
        )
        path.mkdir(parents=True, exist_ok=False)
        return path, sample_index, execution_id

    def _build_remote_workdir(self, task: BenchmarkTask, execution_id: str) -> str:
        """Return the remote staging directory for a task execution."""
        return f"{_REMOTE_AGENT_ROOT}/{task.task_id}/{execution_id}"

    def _build_mode_env(self) -> dict[str, str]:
        """Resolve the OpenAI-compatible env expected by in-container agent modes."""
        mode_env = dict(self._env)
        fallbacks = {
            "OPENAI_MODEL_NAME": self._model,
            "OPENAI_BASE_URL": self._api_base,
            "OPENAI_API_KEY": self._api_key,
        }
        for key, fallback in fallbacks.items():
            value = str(mode_env.get(key, "")).strip()
            if _is_blank_env_value(value) and not _is_blank_env_value(fallback):
                mode_env[key] = str(fallback).strip()
            elif _is_blank_env_value(value):
                mode_env.pop(key, None)
        required = ("OPENAI_MODEL_NAME", "OPENAI_BASE_URL", "OPENAI_API_KEY")
        missing = [key for key in required if _is_blank_env_value(mode_env.get(key, ""))]
        if missing:
            raise RuntimeError(
                "swebench_docker "
                f"{self._agent_type} mode requires env settings for {', '.join(missing)}"
            )
        return mode_env

    def _prepare_runtime_image(self, base_image: str) -> tuple[str, dict[str, Any]]:
        """Build or reuse a derived image with the requested agent runtime injected."""
        if self._agent_type == "openclaw":
            runtime_source_image = _OPENCLAW_RUNTIME_IMAGE
        elif self._agent_type == "opencode":
            runtime_source_image = _OPENCODE_RUNTIME_IMAGE
        elif self._agent_type == "zeroclaw":
            runtime_source_image = _ZEROCLAW_RUNTIME_IMAGE
        else:
            raise RuntimeError(
                f"Runtime image preparation is not supported for agent_type {self._agent_type!r}"
            )

        dockerfile = self._build_runtime_overlay_dockerfile()
        fingerprint = hashlib.sha256(
            "\n".join([
                self._agent_type,
                base_image,
                runtime_source_image,
                dockerfile,
            ]).encode("utf-8")
        ).hexdigest()[:16]
        runtime_image = f"{_RUNTIME_IMAGE_REPO}:{self._agent_type}-{fingerprint}"
        runtime_image_built = False

        if not self._docker_image_exists(runtime_image):
            self._docker_pull(base_image)
            self._docker_pull(runtime_source_image)
            self._docker_build_image(
                runtime_image,
                dockerfile,
                build_args={
                    "BASE_IMAGE": base_image,
                    "RUNTIME_IMAGE": runtime_source_image,
                },
            )
            runtime_image_built = True

        return runtime_image, {
            "base_image": base_image,
            "runtime_image": runtime_image,
            "runtime_source_image": runtime_source_image,
            "runtime_injected": True,
            "runtime_image_built": runtime_image_built,
        }

    def _build_runtime_overlay_dockerfile(self) -> str:
        """Return the overlay Dockerfile for the current in-container runtime."""
        if self._agent_type == "openclaw":
            return """
ARG RUNTIME_IMAGE
ARG BASE_IMAGE
FROM ${RUNTIME_IMAGE} AS runtime
FROM ${BASE_IMAGE}
USER root
COPY --from=runtime /app /opt/openclaw
COPY --from=runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=runtime /usr/local/bin/npm /usr/local/bin/npm
COPY --from=runtime /usr/local/bin/npx /usr/local/bin/npx
COPY --from=runtime /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sfn /opt/openclaw/openclaw.mjs /usr/local/bin/openclaw \\
 && mkdir -p /tmp/empty-bundled
""".strip()
        if self._agent_type == "opencode":
            return """
ARG RUNTIME_IMAGE
ARG BASE_IMAGE
FROM ${RUNTIME_IMAGE} AS runtime
FROM ${BASE_IMAGE}
USER root
COPY --from=runtime /usr/bin/node /usr/bin/node
COPY --from=runtime /usr/lib/node_modules/opencode-ai /usr/lib/node_modules/opencode-ai
RUN ln -sfn /usr/lib/node_modules/opencode-ai/bin/opencode /usr/bin/opencode
""".strip()
        if self._agent_type == "zeroclaw":
            return """
ARG RUNTIME_IMAGE
ARG BASE_IMAGE
FROM ${RUNTIME_IMAGE} AS runtime
FROM ${BASE_IMAGE}
USER root
COPY --from=runtime /usr/local/bin/zeroclaw /usr/local/bin/zeroclaw
""".strip()
        raise RuntimeError(
            f"Runtime overlay Dockerfile is not defined for agent_type {self._agent_type!r}"
        )

    def _build_container_prompt_parts(
        self,
        task: BenchmarkTask,
        repo_root: str,
    ) -> dict[str, Any]:
        """Build structured prompt parts for in-container agent modes."""
        return {
            "repo_root": repo_root,
            "repo": str(task.metadata.get("repo", "")).strip(),
            "base_commit": str(task.ground_truth.get("base_commit", "")).strip(),
            "relevant_tests": self._normalize_prompt_items(
                task.metadata.get("selected_test_files_to_run", [])
            ),
            "fail_to_pass": self._normalize_prompt_items(
                task.metadata.get("fail_to_pass", [])
            ),
            "pass_to_pass": self._normalize_prompt_items(
                task.metadata.get("pass_to_pass", [])
            ),
            "issue_description": task.problem.strip(),
        }

    @staticmethod
    def _normalize_prompt_items(value: Any) -> list[str]:
        """Normalize benchmark metadata into prompt bullet items."""
        if value is None:
            return []

        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped[0] in "[(":
                for parser in (json.loads, ast.literal_eval):
                    try:
                        parsed = parser(stripped)
                    except (TypeError, ValueError, SyntaxError, json.JSONDecodeError):
                        continue
                    if isinstance(parsed, (list, tuple, set)):
                        value = parsed
                        break
                    if parsed is not None:
                        value = [parsed]
                        break
                else:
                    return [stripped]
            else:
                return [stripped]

        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]

        normalized = str(value).strip()
        return [normalized] if normalized else []

    @staticmethod
    def _render_container_prompt(parts: dict[str, Any]) -> str:
        """Render structured prompt parts into the staged prompt text."""
        lines = [
            "You are working on a SWE-bench Pro task inside a checked-out git repository.",
            f"Repository path: {parts['repo_root']}",
        ]
        repo = str(parts.get("repo", "")).strip()
        if repo:
            lines.append(f"Repository: {repo}")
        base_commit = str(parts.get("base_commit", "")).strip()
        if base_commit:
            lines.append(f"Base commit: {base_commit}")
        lines.extend([
            "",
            "Read the repository, implement the fix directly in the working tree, and run targeted checks if useful.",
            "Do not print a patch manually. Stop after applying the repository changes; AlphaDiana will collect `git diff --binary`.",
        ])
        selected_tests = parts.get("relevant_tests", [])
        if selected_tests:
            lines.extend(["", "Relevant test files:"])
            lines.extend(f"- {item}" for item in selected_tests if str(item).strip())
        fail_to_pass = parts.get("fail_to_pass", [])
        if fail_to_pass:
            lines.extend(["", "Fail-to-pass targets:"])
            lines.extend(f"- {item}" for item in fail_to_pass if str(item).strip())
        pass_to_pass = parts.get("pass_to_pass", [])
        if pass_to_pass:
            lines.extend(["", "Pass-to-pass targets:"])
            lines.extend(f"- {item}" for item in pass_to_pass if str(item).strip())
        lines.extend(["", "Issue description:", str(parts.get("issue_description", "")).strip()])
        return "\n".join(lines).strip() + "\n"

    def _build_container_prompt(self, task: BenchmarkTask, repo_root: str) -> str:
        """Build the task prompt used by in-container agent modes."""
        return self._render_container_prompt(self._build_container_prompt_parts(task, repo_root))

    def _resolve_model_context_window(self, model_name: str | None = None) -> int | None:
        """Resolve the model context window from the provider's /models response."""
        target_model = (model_name or self._model or "").strip()
        if target_model and target_model in self._model_context_windows:
            return self._model_context_windows[target_model]
        if not target_model and self._max_model_len:
            return self._max_model_len

        try:
            import httpx

            api_base = self._api_base.rstrip("/")
            response = httpx.get(f"{api_base}/models", timeout=5.0)
            if response.status_code != 200:
                return None

            data = response.json().get("data", [])
            candidate: int | None = None
            for model in data:
                if not isinstance(model, dict):
                    continue
                max_len = model.get("max_model_len")
                if not isinstance(max_len, int) or max_len <= 0:
                    continue
                model_names = {
                    str(model.get("id", "")).strip(),
                    str(model.get("name", "")).strip(),
                    str(model.get("model", "")).strip(),
                }
                if target_model and target_model in model_names:
                    self._model_context_windows[target_model] = max_len
                    self._max_model_len = max_len
                    return max_len
                if candidate is None:
                    candidate = max_len
            if candidate is not None:
                if target_model:
                    self._model_context_windows[target_model] = candidate
                self._max_model_len = candidate
                return candidate
        except Exception:
            return None
        return None

    @staticmethod
    def _parse_positive_int(value: Any) -> int | None:
        """Parse a positive integer value from config/env strings."""
        if _is_blank_env_value(value):
            return None
        try:
            parsed = int(str(value).strip())
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    def _resolve_context_window(
        self,
        *,
        override_env_var: str | None = None,
        mode_env: dict[str, str] | None = None,
    ) -> int:
        """Resolve a context window from env override or provider metadata."""
        if override_env_var:
            for source in (mode_env or {}, os.environ):
                parsed = self._parse_positive_int(source.get(override_env_var))
                if parsed is not None:
                    return parsed
        model_name = ""
        if mode_env:
            model_name = str(mode_env.get("OPENAI_MODEL_NAME", "")).strip()
        return self._resolve_model_context_window(model_name or self._model) or 65536

    def _evaluate_openclaw_prompt_budget(
        self,
        *,
        parts: dict[str, Any],
        context_window: int,
        requested_completion_cap: int,
        compactions_applied: list[str],
    ) -> dict[str, Any]:
        """Estimate prompt budget and derive a safe OpenClaw completion cap."""
        prompt = self._render_container_prompt(parts)
        estimated_prompt_tokens = self._estimate_prompt_tokens(
            [{"role": "user", "content": prompt}]
        )
        raw_headroom_tokens = max(context_window - estimated_prompt_tokens, 0)
        available_headroom_after_prompt = max(
            raw_headroom_tokens - _PROMPT_HEADROOM_MARGIN_TOKENS,
            0,
        )
        completion_cap = min(
            requested_completion_cap,
            available_headroom_after_prompt,
            _OPENCLAW_MAX_COMPLETION_TOKENS,
        )
        return {
            "prompt": prompt,
            "estimated_prompt_tokens": estimated_prompt_tokens,
            "raw_headroom_tokens": raw_headroom_tokens,
            "available_headroom_after_prompt": available_headroom_after_prompt,
            "requested_completion_cap": requested_completion_cap,
            "completion_cap": completion_cap,
            "context_window": context_window,
            "compactions_applied": list(compactions_applied),
            "fits": completion_cap >= _OPENCLAW_MIN_COMPLETION_TOKENS,
        }

    def _truncate_openclaw_issue_description(
        self,
        *,
        parts: dict[str, Any],
        context_window: int,
        requested_completion_cap: int,
        compactions_applied: list[str],
    ) -> dict[str, Any]:
        """Truncate the issue description until the prompt fits the context budget."""
        original_issue = str(parts.get("issue_description", "")).strip()
        if not original_issue:
            return self._evaluate_openclaw_prompt_budget(
                parts=parts,
                context_window=context_window,
                requested_completion_cap=requested_completion_cap,
                compactions_applied=compactions_applied,
            )

        low = 0
        high = len(original_issue)
        best: dict[str, Any] | None = None
        while low <= high:
            mid = (low + high) // 2
            candidate_parts = dict(parts)
            candidate_parts["issue_description"] = (
                original_issue[:mid].rstrip() + _TRUNCATION_MARKER
            )
            evaluation = self._evaluate_openclaw_prompt_budget(
                parts=candidate_parts,
                context_window=context_window,
                requested_completion_cap=requested_completion_cap,
                compactions_applied=compactions_applied + ["truncated_issue_description"],
            )
            if evaluation["fits"]:
                best = evaluation
                low = mid + 1
            else:
                high = mid - 1
        if best is not None:
            return best
        return self._evaluate_openclaw_prompt_budget(
            parts=parts,
            context_window=context_window,
            requested_completion_cap=requested_completion_cap,
            compactions_applied=compactions_applied,
        )

    def _prepare_openclaw_prompt(
        self,
        task: BenchmarkTask,
        repo_root: str,
        mode_env: dict[str, str],
    ) -> tuple[str, dict[str, Any]]:
        """Prepare an OpenClaw prompt that fits the target model context window."""
        parts = self._build_container_prompt_parts(task, repo_root)
        prompt_profile = (
            str(mode_env.get("OPENCLAW_PROMPT_PROFILE", "edit_first")).strip()
            or "edit_first"
        )
        issue_char_limit = self._parse_positive_int(
            mode_env.get("OPENCLAW_PROBLEM_STATEMENT_MAX_CHARS")
        )
        context_window = self._resolve_context_window(
            override_env_var="OPENCLAW_CONTEXT_WINDOW",
            mode_env=mode_env,
        )
        requested_completion_cap = min(
            self._parse_positive_int(mode_env.get("OPENCLAW_COMPLETION_MAX_TOKENS"))
            or _OPENCLAW_MAX_COMPLETION_TOKENS,
            _OPENCLAW_MAX_COMPLETION_TOKENS,
        )
        compactions_applied: list[str] = []

        if prompt_profile == "edit_first":
            if parts.get("pass_to_pass"):
                parts["pass_to_pass"] = []
                compactions_applied.append("edit_first_removed_pass_to_pass")
            relevant_tests = list(parts.get("relevant_tests", []))
            fail_to_pass = list(parts.get("fail_to_pass", []))
            capped_relevant = relevant_tests[:_EDIT_FIRST_LIST_SECTION_LIMIT]
            capped_fail = fail_to_pass[:_EDIT_FIRST_LIST_SECTION_LIMIT]
            if capped_relevant != relevant_tests:
                parts["relevant_tests"] = capped_relevant
                compactions_applied.append("edit_first_capped_relevant_tests_to_10")
            if capped_fail != fail_to_pass:
                parts["fail_to_pass"] = capped_fail
                compactions_applied.append("edit_first_capped_fail_to_pass_to_10")

        if issue_char_limit is not None:
            issue_description = str(parts.get("issue_description", "")).strip()
            if len(issue_description) > issue_char_limit:
                parts["issue_description"] = (
                    issue_description[:issue_char_limit].rstrip() + _TRUNCATION_MARKER
                )
                compactions_applied.append(
                    f"capped_issue_description_chars:{issue_char_limit}"
                )

        evaluation = self._evaluate_openclaw_prompt_budget(
            parts=parts,
            context_window=context_window,
            requested_completion_cap=requested_completion_cap,
            compactions_applied=compactions_applied,
        )
        if not evaluation["fits"] and parts.get("pass_to_pass"):
            parts["pass_to_pass"] = []
            compactions_applied.append("removed_pass_to_pass")
            evaluation = self._evaluate_openclaw_prompt_budget(
                parts=parts,
                context_window=context_window,
                requested_completion_cap=requested_completion_cap,
                compactions_applied=compactions_applied,
            )

        if not evaluation["fits"]:
            relevant_tests = list(parts.get("relevant_tests", []))
            fail_to_pass = list(parts.get("fail_to_pass", []))
            capped_relevant = relevant_tests[:_OPENCLAW_LIST_SECTION_LIMIT]
            capped_fail = fail_to_pass[:_OPENCLAW_LIST_SECTION_LIMIT]
            if capped_relevant != relevant_tests or capped_fail != fail_to_pass:
                parts["relevant_tests"] = capped_relevant
                parts["fail_to_pass"] = capped_fail
                compactions_applied.append("capped_test_lists_to_20")
                evaluation = self._evaluate_openclaw_prompt_budget(
                    parts=parts,
                    context_window=context_window,
                    requested_completion_cap=requested_completion_cap,
                    compactions_applied=compactions_applied,
                )

        if not evaluation["fits"]:
            evaluation = self._truncate_openclaw_issue_description(
                parts=parts,
                context_window=context_window,
                requested_completion_cap=requested_completion_cap,
                compactions_applied=compactions_applied,
            )

        budget_metadata = {
            **evaluation,
            "mode": "openclaw",
            "repo_root": repo_root,
            "model": mode_env.get("OPENAI_MODEL_NAME", self._model),
            "needs_larger_context_model": not evaluation["fits"],
            "recommended_env": [
                "OPENCLAW_SMOKE_MODEL_NAME",
                "OPENCLAW_CONTEXT_WINDOW",
            ],
            "prompt_profile": prompt_profile,
            "problem_statement_max_chars": issue_char_limit,
        }
        return evaluation["prompt"], budget_metadata

    def _prepare_opencode_prompt(
        self,
        task: BenchmarkTask,
        repo_root: str,
        mode_env: dict[str, str],
        *,
        prompt_profile_override: str | None = None,
        strategy_name: str | None = None,
        target_file_hints: list[str] | None = None,
        primary_target_file: str = "",
        target_file_hints_source: str = "none",
    ) -> tuple[str, dict[str, Any]]:
        """Prepare an OpenCode prompt with optional edit-first compactions."""
        parts = self._build_container_prompt_parts(task, repo_root)
        prompt_profile = (
            str(
                prompt_profile_override
                or mode_env.get("OPENCODE_PROMPT_PROFILE", "edit_first")
            ).strip()
            or "edit_first"
        )
        issue_char_limit = self._parse_positive_int(
            mode_env.get("OPENCODE_PROBLEM_STATEMENT_MAX_CHARS")
        )
        compactions_applied: list[str] = []

        if prompt_profile == "edit_first":
            if parts.get("pass_to_pass"):
                parts["pass_to_pass"] = []
                compactions_applied.append("edit_first_removed_pass_to_pass")
            relevant_tests = list(parts.get("relevant_tests", []))
            fail_to_pass = list(parts.get("fail_to_pass", []))
            capped_relevant = relevant_tests[:_EDIT_FIRST_LIST_SECTION_LIMIT]
            capped_fail = fail_to_pass[:_EDIT_FIRST_LIST_SECTION_LIMIT]
            if capped_relevant != relevant_tests:
                parts["relevant_tests"] = capped_relevant
                compactions_applied.append("edit_first_capped_relevant_tests_to_10")
            if capped_fail != fail_to_pass:
                parts["fail_to_pass"] = capped_fail
                compactions_applied.append("edit_first_capped_fail_to_pass_to_10")

        if issue_char_limit is not None:
            issue_description = str(parts.get("issue_description", "")).strip()
            if len(issue_description) > issue_char_limit:
                parts["issue_description"] = (
                    issue_description[:issue_char_limit].rstrip() + _TRUNCATION_MARKER
                )
                compactions_applied.append(
                    f"capped_issue_description_chars:{issue_char_limit}"
                )

        return self._render_container_prompt(parts), {
            "prompt_profile": prompt_profile,
            "problem_statement_max_chars": issue_char_limit,
            "compactions_applied": compactions_applied,
            "strategy_name": (strategy_name or prompt_profile).strip() or prompt_profile,
            "target_file_hints": list(target_file_hints or []),
            "primary_target_file": str(primary_target_file).strip(),
            "target_file_hints_source": target_file_hints_source,
        }

    def _prepare_zeroclaw_prompt(
        self,
        task: BenchmarkTask,
        repo_root: str,
        mode_env: dict[str, str],
    ) -> tuple[str, dict[str, Any]]:
        """Prepare a ZeroClaw prompt with the same SWE-bench edit contract."""
        parts = self._build_container_prompt_parts(task, repo_root)
        prompt_profile = (
            str(mode_env.get("ZEROCLAW_PROMPT_PROFILE", "edit_first")).strip()
            or "edit_first"
        )
        issue_char_limit = self._parse_positive_int(
            mode_env.get("ZEROCLAW_PROBLEM_STATEMENT_MAX_CHARS")
        )
        compactions_applied: list[str] = []

        if prompt_profile == "edit_first":
            if parts.get("pass_to_pass"):
                parts["pass_to_pass"] = []
                compactions_applied.append("edit_first_removed_pass_to_pass")
            relevant_tests = list(parts.get("relevant_tests", []))
            fail_to_pass = list(parts.get("fail_to_pass", []))
            capped_relevant = relevant_tests[:_EDIT_FIRST_LIST_SECTION_LIMIT]
            capped_fail = fail_to_pass[:_EDIT_FIRST_LIST_SECTION_LIMIT]
            if capped_relevant != relevant_tests:
                parts["relevant_tests"] = capped_relevant
                compactions_applied.append("edit_first_capped_relevant_tests_to_10")
            if capped_fail != fail_to_pass:
                parts["fail_to_pass"] = capped_fail
                compactions_applied.append("edit_first_capped_fail_to_pass_to_10")

        if issue_char_limit is not None:
            issue_description = str(parts.get("issue_description", "")).strip()
            if len(issue_description) > issue_char_limit:
                parts["issue_description"] = (
                    issue_description[:issue_char_limit].rstrip() + _TRUNCATION_MARKER
                )
                compactions_applied.append(
                    f"capped_issue_description_chars:{issue_char_limit}"
                )

        return self._render_container_prompt(parts), {
            "prompt_profile": prompt_profile,
            "problem_statement_max_chars": issue_char_limit,
            "compactions_applied": compactions_applied,
        }

    def _read_text_if_exists(self, path: Path) -> str:
        """Read a UTF-8 text file if it exists, otherwise return an empty string."""
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")

    def _read_json_if_exists(self, path: Path) -> dict[str, Any] | None:
        """Read a JSON artifact if it exists and is parseable."""
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _copy_attempt_files(
        source_dir: Path,
        target_dir: Path,
        *,
        exclude_names: set[str] | None = None,
    ) -> None:
        """Copy top-level attempt artifacts into the root artifact directory."""
        excluded = exclude_names or set()
        for child in source_dir.iterdir():
            if child.is_file() and child.name not in excluded:
                (target_dir / child.name).write_bytes(child.read_bytes())

    def _resolve_candidate_aliases(
        self,
        mode_env: dict[str, str],
        *,
        candidates_env_key: str,
        primary_env_keys: tuple[str, ...],
    ) -> list[str]:
        """Resolve an ordered set of candidate model aliases for retries."""
        aliases = _split_candidate_aliases(mode_env.get(candidates_env_key))
        for key in primary_env_keys:
            aliases.extend(
                alias
                for alias in _split_candidate_aliases(mode_env.get(key))
                if alias not in aliases
            )
        if not aliases and self._model:
            aliases.append(self._model)
        return aliases

    @staticmethod
    def _normalize_repo_hint_path(value: str) -> str:
        """Normalize a repo-relative path hint into a stable artifact/env form."""
        path = str(value).strip().strip("'\"")
        if not path:
            return ""
        path = path.replace("\\", "/")
        path = re.sub(r"^(?:\./)+", "", path)
        for prefix in (
            "/app/",
            "/workspace/",
            "/repo/",
            "/project/",
            "/workdir/",
            "/root/project/",
            "/root/repo/",
        ):
            if path.startswith(prefix):
                path = path[len(prefix) :]
                break
        return path.lstrip("/")

    def _extract_repo_path_candidates(self, text: str) -> list[str]:
        """Extract likely repo file paths from issue text or benchmark metadata."""
        matches = re.findall(
            r"(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.[A-Za-z0-9_.-]+",
            str(text),
        )
        candidates: list[str] = []
        for match in matches:
            normalized = self._normalize_repo_hint_path(match)
            if not normalized or "://" in normalized or "node_modules/" in normalized:
                continue
            if normalized not in candidates:
                candidates.append(normalized)
        return candidates

    def _expand_test_path_to_source_hints(self, test_path: str) -> list[str]:
        """Derive likely source-file hints from a test path."""
        normalized = self._normalize_repo_hint_path(test_path)
        if not normalized:
            return []

        candidates: list[str] = []
        lower_path = normalized.lower()
        suffix = Path(normalized).suffix or ".js"
        if "database" in lower_path:
            candidates.extend(
                [
                    f"src/database/redis/main{suffix}",
                    f"src/database/mongo/main{suffix}",
                ]
            )
        if "/user/" in f"/{lower_path}" and (
            "email" in lower_path or "validation" in lower_path
        ):
            candidates.append(f"src/user/email{suffix}")

        unique_candidates: list[str] = []
        for candidate in candidates:
            normalized_candidate = self._normalize_repo_hint_path(candidate)
            if (
                not normalized_candidate
                or normalized_candidate.startswith(("test/", "tests/"))
                or "node_modules/" in normalized_candidate
                or normalized_candidate in unique_candidates
            ):
                continue
            unique_candidates.append(normalized_candidate)
        return unique_candidates

    def _derive_opencode_target_file_hints(
        self,
        task: BenchmarkTask,
        mode_env: dict[str, str],
    ) -> tuple[list[str], str]:
        """Resolve explicit or auto-derived target-file hints for OpenCode smokes."""
        explicit_hints_env = mode_env.get("OPENCODE_TARGET_FILE_HINTS", "")
        if not _is_blank_env_value(explicit_hints_env):
            explicit_hints: list[str] = []
            for item in _split_candidate_aliases(explicit_hints_env):
                normalized = self._normalize_repo_hint_path(item)
                if normalized and normalized not in explicit_hints:
                    explicit_hints.append(normalized)
            return explicit_hints[:_OPENCODE_TARGET_HINT_LIMIT], "env_override"

        auto_hints_enabled = (
            str(mode_env.get("OPENCODE_AUTO_TARGET_HINTS", "1")).strip().lower()
            not in {"", "0", "false", "no", "off"}
        )
        if not auto_hints_enabled:
            return [], "disabled"

        selected_tests = self._normalize_prompt_items(
            task.metadata.get("selected_test_files_to_run", [])
        )
        fail_to_pass = self._normalize_prompt_items(task.metadata.get("fail_to_pass", []))
        issue_text = task.problem.strip()
        lower_issue = issue_text.lower()
        combined_test_text = "\n".join(selected_tests + fail_to_pass).lower()

        candidates: list[str] = []
        if "database" in lower_issue or "database" in combined_test_text:
            candidates.extend(["src/database/redis/main.js", "src/database/mongo/main.js"])
        if "cansendvalidation" in lower_issue or (
            "email" in lower_issue and ("validation" in lower_issue or "confirm" in lower_issue)
        ):
            candidates.append("src/user/email.js")

        for item in selected_tests + fail_to_pass:
            for path_candidate in self._extract_repo_path_candidates(item):
                if path_candidate.startswith(("test/", "tests/")):
                    candidates.extend(self._expand_test_path_to_source_hints(path_candidate))
                else:
                    candidates.append(path_candidate)

        for path_candidate in self._extract_repo_path_candidates(issue_text):
            candidates.append(path_candidate)

        resolved_hints: list[str] = []
        for candidate in candidates:
            normalized = self._normalize_repo_hint_path(candidate)
            if (
                not normalized
                or normalized.startswith(("test/", "tests/"))
                or "node_modules/" in normalized
                or normalized in resolved_hints
            ):
                continue
            resolved_hints.append(normalized)
            if len(resolved_hints) >= _OPENCODE_TARGET_HINT_LIMIT:
                break

        return resolved_hints, "auto_derived" if resolved_hints else "none"

    def _resolve_opencode_strategy_sequence(self, mode_env: dict[str, str]) -> list[str]:
        """Resolve the ordered OpenCode strategy retry sequence."""
        strategies = _split_candidate_aliases(mode_env.get("OPENCODE_STRATEGY_SEQUENCE"))
        return strategies or ["bash_edit_first", "guided_edit_first", "edit_first"]

    def _resolve_opencode_strategy_context(
        self,
        strategy_name: str,
        *,
        target_file_hints: list[str],
        target_file_hints_source: str,
    ) -> dict[str, Any]:
        """Map an OpenCode strategy name to the prompt profile and hint set."""
        normalized_strategy = str(strategy_name).strip() or "edit_first"
        prompt_profile = (
            normalized_strategy
            if normalized_strategy
            not in {"bash_edit_first", "guided_edit_first", "edit_first"}
            else "edit_first"
        )
        if normalized_strategy in {"bash_edit_first", "guided_edit_first"}:
            resolved_hints = list(target_file_hints)
            resolved_hint_source = (
                target_file_hints_source if resolved_hints else "strategy_without_hints"
            )
        else:
            resolved_hints = []
            resolved_hint_source = (
                "strategy_disabled" if target_file_hints else target_file_hints_source
            )
        return {
            "strategy_name": normalized_strategy,
            "prompt_profile": prompt_profile,
            "target_file_hints": resolved_hints,
            "target_file_hints_source": resolved_hint_source,
        }

    def _resolve_opencode_primary_target_file(
        self,
        mode_env: dict[str, str],
        *,
        target_file_hints: list[str],
    ) -> str:
        """Resolve the primary target file for a stricter OpenCode edit contract."""
        explicit_primary = self._normalize_repo_hint_path(
            mode_env.get("OPENCODE_PRIMARY_TARGET_FILE", "")
        )
        if explicit_primary:
            return explicit_primary
        return target_file_hints[0] if target_file_hints else ""

    @staticmethod
    def _extract_opencode_error_message(record: dict[str, Any]) -> str:
        """Extract the most useful message from an OpenCode error record."""
        error = record.get("error")
        if isinstance(error, dict):
            data = error.get("data")
            if isinstance(data, dict):
                for key in ("message", "error", "details"):
                    value = data.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
            for key in ("message", "name", "type"):
                value = error.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        message = record.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
        return "unknown error"

    def _collect_workspace_files(
        self,
        artifacts_dir: Path,
        *,
        mode: str,
        names: tuple[str, ...],
    ) -> dict[str, str]:
        """Load selected text artifacts for ResultStore persistence."""
        workspace_files: dict[str, str] = {}
        for name in names:
            path = artifacts_dir / name
            if not path.exists() or path.is_dir():
                continue
            workspace_files[f"/swebench_agent/{mode}/{name}"] = path.read_text(
                encoding="utf-8",
                errors="replace",
            )
        return workspace_files

    def _stage_file_into_container(
        self,
        container_id: str,
        *,
        local_path: Path,
        remote_path: str,
    ) -> None:
        """Create the remote parent directory and copy a local file into it."""
        remote_parent = str(Path(remote_path).parent)
        self._docker_exec(
            container_id,
            ["bash", "-lc", f"mkdir -p {shlex.quote(remote_parent)}"],
            timeout=30,
        )
        self._docker_cp_to(container_id, str(local_path), remote_path)

    def _solve_openclaw(
        self,
        task: BenchmarkTask,
        base_image: str,
        image: str,
        container_id: str,
        start_time: float,
        runtime_metadata: dict[str, Any],
    ) -> AgentResponse:
        """Run OpenClaw inside the SWE-bench container and return the resulting patch."""
        if not _OPENCLAW_RUNNER.exists() or not _OPENCLAW_CONFIG_TEMPLATE.exists():
            raise FileNotFoundError(
                "Missing OpenClaw SWE-bench assets. Expected "
                f"{_OPENCLAW_RUNNER} and {_OPENCLAW_CONFIG_TEMPLATE}."
            )

        repo_root = self._detect_repo_root(container_id)
        mode_env = self._build_mode_env()
        for key, value in {
            "OPENCLAW_GATEWAY_TOKEN": "OPENCLAW",
            "OPENCLAW_AGENT_ID": "main",
            "OPENCLAW_TOOLS_PROFILE": "coding",
            "OPENCLAW_REQUIRE_PATCH": "1",
            "OPENCLAW_MAX_TOOL_CALLS_WITHOUT_EDIT": "20",
            "OPENCLAW_MAX_NO_EDIT_SECONDS": "300",
            "OPENCLAW_PROMPT_PROFILE": "edit_first",
            "OPENCLAW_PROBLEM_STATEMENT_MAX_CHARS": "12000",
            "ALPHADIANA_INSTANCE_ID": task.task_id,
            "OPENCLAW_SESSION_KEY": task.task_id,
            "OPENCLAW_CHAT_USER": task.task_id,
        }.items():
            if _is_blank_env_value(mode_env.get(key, "")):
                mode_env[key] = value
        candidate_aliases = self._resolve_candidate_aliases(
            mode_env,
            candidates_env_key="OPENCLAW_SMOKE_MODEL_CANDIDATES",
            primary_env_keys=("OPENCLAW_SMOKE_MODEL_NAME", "OPENAI_MODEL_NAME"),
        )
        if not candidate_aliases:
            raise RuntimeError(
                "OpenClaw requires at least one model alias. Set OPENCLAW_SMOKE_MODEL_NAME "
                "or OPENCLAW_SMOKE_MODEL_CANDIDATES."
            )
        mode_env["OPENCLAW_SMOKE_MODEL_CANDIDATES"] = ",".join(candidate_aliases)
        mode_env["OPENCLAW_SMOKE_MODEL_NAME"] = candidate_aliases[0]
        mode_env["OPENAI_MODEL_NAME"] = candidate_aliases[0]
        local_artifacts, sample_index, execution_id = self._prepare_local_artifacts_dir(task)
        prompt, prompt_budget = self._prepare_openclaw_prompt(task, repo_root, mode_env)
        prompt_path = local_artifacts / "prompt.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        prompt_budget_path = local_artifacts / "prompt_budget.json"
        prompt_budget_path.write_text(
            json.dumps(prompt_budget, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        if prompt_budget.get("needs_larger_context_model"):
            raise RuntimeError(
                "OpenClaw prompt does not fit the configured context window even after "
                "compaction. Export OPENCLAW_SMOKE_MODEL_NAME to a larger-context model "
                "or set OPENCLAW_CONTEXT_WINDOW explicitly."
            )
        mode_env["OPENCLAW_COMPLETION_MAX_TOKENS"] = str(
            prompt_budget["completion_cap"]
        )
        openclaw_attempts_dir = local_artifacts / "openclaw_attempts"
        openclaw_attempts_dir.mkdir(parents=True, exist_ok=True)
        attempt_records: list[dict[str, Any]] = []
        selected_attempt_dir: Path | None = None
        selected_attempt_record: dict[str, Any] | None = None
        selected_returncode = 1

        for index, alias in enumerate(candidate_aliases, start=1):
            attempt_name = f"{index:02d}_{_safe_artifact_fragment(alias)}"
            attempt_local_artifacts = openclaw_attempts_dir / attempt_name
            attempt_local_artifacts.mkdir(parents=True, exist_ok=True)
            attempt_execution_id = f"{execution_id}-openclaw-{attempt_name}"
            remote_root = self._build_remote_workdir(task, attempt_execution_id)
            remote_artifacts_dir = f"{remote_root}/artifacts"
            remote_prompt_path = f"{remote_root}/prompt.txt"
            remote_script_path = f"{remote_root}/run_openclaw.sh"
            remote_config_template = f"{remote_root}/openclaw.json.template"

            self._stage_file_into_container(
                container_id,
                local_path=prompt_path,
                remote_path=remote_prompt_path,
            )
            self._stage_file_into_container(
                container_id,
                local_path=_OPENCLAW_RUNNER,
                remote_path=remote_script_path,
            )
            self._stage_file_into_container(
                container_id,
                local_path=_OPENCLAW_CONFIG_TEMPLATE,
                remote_path=remote_config_template,
            )
            self._docker_exec(
                container_id,
                ["bash", "-lc", f"mkdir -p {shlex.quote(remote_artifacts_dir)}"],
                timeout=30,
            )

            exec_env = {
                **mode_env,
                "OPENAI_MODEL_NAME": alias,
                "OPENCLAW_SMOKE_MODEL_NAME": alias,
                "ALPHADIANA_ARTIFACTS_DIR": remote_artifacts_dir,
                "ALPHADIANA_PROMPT_FILE": remote_prompt_path,
                "ALPHADIANA_CONFIG_TEMPLATE": remote_config_template,
            }
            exec_result = self._docker_exec(
                container_id,
                [
                    "bash",
                    "-lc",
                    f"chmod +x {shlex.quote(remote_script_path)} && {shlex.quote(remote_script_path)}",
                ],
                env=exec_env,
                timeout=self._timeout,
                check=False,
            )

            self._docker_cp_from(
                container_id,
                f"{remote_artifacts_dir}/.",
                str(attempt_local_artifacts),
            )
            (attempt_local_artifacts / "openclaw_runner_stdout.log").write_text(
                exec_result.stdout,
                encoding="utf-8",
                errors="replace",
            )
            (attempt_local_artifacts / "openclaw_runner_stderr.log").write_text(
                exec_result.stderr,
                encoding="utf-8",
                errors="replace",
            )

            attempt_patch_path = attempt_local_artifacts / "patch.diff"
            attempt_patch = self._read_text_if_exists(attempt_patch_path).strip()
            attempt_tool_verdict = (
                self._read_json_if_exists(attempt_local_artifacts / "openclaw_tool_verdict.json")
                or {}
            )
            attempt_convergence = (
                self._read_json_if_exists(
                    attempt_local_artifacts / "openclaw_edit_convergence.json"
                )
                or {}
            )
            attempt_reason = str(
                attempt_convergence.get("reason")
                or attempt_tool_verdict.get("reason")
                or self._read_text_if_exists(attempt_local_artifacts / "gateway.log").strip()
                or exec_result.stderr.strip()
                or exec_result.stdout.strip()
                or "unknown OpenClaw outcome"
            ).strip()
            attempt_record = {
                "attempt_index": index,
                "resolved_model_alias": alias,
                "prompt_profile": mode_env.get("OPENCLAW_PROMPT_PROFILE", "edit_first"),
                "classification": str(
                    attempt_convergence.get("classification")
                    or attempt_tool_verdict.get("classification")
                    or ("patch_created" if attempt_patch else "provider_failure")
                ).strip(),
                "patch_size_bytes": attempt_patch_path.stat().st_size
                if attempt_patch_path.exists()
                else 0,
                "tool_call_count": int(attempt_convergence.get("tool_call_count") or 0),
                "tool_result_count": int(attempt_convergence.get("tool_result_count") or 0),
                "tracked_repo_change_count": int(
                    attempt_convergence.get("tracked_repo_change_count") or 0
                ),
                "reason": attempt_reason,
                "returncode": exec_result.returncode,
                "artifacts_dir": str(attempt_local_artifacts),
            }
            attempt_records.append(attempt_record)
            selected_attempt_dir = attempt_local_artifacts
            selected_attempt_record = attempt_record
            selected_returncode = exec_result.returncode
            if attempt_patch and exec_result.returncode == 0:
                break

        if selected_attempt_dir is None or selected_attempt_record is None:
            raise RuntimeError("OpenClaw attempts did not produce any artifacts")

        openclaw_attempt_matrix_path = local_artifacts / "openclaw_attempt_matrix.json"
        openclaw_selected_attempt_path = local_artifacts / "openclaw_selected_attempt.json"
        openclaw_attempt_matrix_path.write_text(
            json.dumps(
                {
                    "attempts": attempt_records,
                    "selected_attempt_index": selected_attempt_record["attempt_index"],
                    "tried_aliases": candidate_aliases,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        openclaw_selected_attempt_path.write_text(
            json.dumps(selected_attempt_record, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        self._copy_attempt_files(
            selected_attempt_dir,
            local_artifacts,
            exclude_names={
                "openclaw_attempt_matrix.json",
                "openclaw_selected_attempt.json",
            },
        )

        gateway_log = self._read_text_if_exists(local_artifacts / "gateway.log")
        openclaw_output = self._read_text_if_exists(local_artifacts / "openclaw_output.jsonl")
        openclaw_tool_verdict_path = local_artifacts / "openclaw_tool_verdict.json"
        openclaw_tool_verdict = self._read_json_if_exists(openclaw_tool_verdict_path) or {}
        openclaw_edit_convergence_path = local_artifacts / "openclaw_edit_convergence.json"
        openclaw_edit_convergence = (
            self._read_json_if_exists(openclaw_edit_convergence_path) or {}
        )
        openclaw_tool_classification = str(
            openclaw_tool_verdict.get("classification", "")
        ).strip()
        openclaw_tool_reason = str(openclaw_tool_verdict.get("reason", "")).strip()
        openclaw_convergence_classification = str(
            openclaw_edit_convergence.get("classification", "")
        ).strip()
        openclaw_convergence_reason = str(
            openclaw_edit_convergence.get("reason", "")
        ).strip()
        attempt_matrix_hint = (
            f" Tried aliases: {', '.join(candidate_aliases)}. "
            f"See {openclaw_attempt_matrix_path}"
        )

        def _runner_suffix() -> str:
            if selected_returncode == 0:
                return ""
            detail = gateway_log.strip() or openclaw_tool_reason or openclaw_convergence_reason
            if detail:
                return (
                    f" (runner exited {selected_returncode}: "
                    f"{detail.splitlines()[-1]})"
                )
            return f" (runner exited {selected_returncode})"

        if not openclaw_output.strip():
            if openclaw_tool_classification == "provider_failure":
                detail = openclaw_tool_reason or "unknown provider failure"
                raise RuntimeError(
                    "OpenClaw provider/model contract failed before a usable tool "
                    f"session started: {detail}. See {openclaw_tool_verdict_path}."
                    f"{attempt_matrix_hint}"
                )
            if not (local_artifacts / "openclaw_output.jsonl").exists():
                raise RuntimeError(
                    f"OpenClaw run did not produce openclaw_output.jsonl"
                    f"{_runner_suffix()}.{attempt_matrix_hint}"
                )
            raise RuntimeError(
                f"OpenClaw run produced an empty openclaw_output.jsonl"
                f"{_runner_suffix()}.{attempt_matrix_hint}"
            )

        events: list[dict[str, Any]] = []
        for index, line in enumerate(openclaw_output.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    "OpenClaw run produced invalid JSONL in openclaw_output.jsonl "
                    f"(line {index}): {exc}"
                ) from exc
            if not isinstance(payload, dict):
                raise RuntimeError(
                    "OpenClaw run produced a non-object JSONL record in "
                    f"openclaw_output.jsonl (line {index})"
                )
            events.append(payload)

        if not events:
            raise RuntimeError(
                f"OpenClaw run produced no SSE events in openclaw_output.jsonl"
                f"{_runner_suffix()}.{attempt_matrix_hint}"
            )

        patch = self._read_text_if_exists(local_artifacts / "patch.diff").strip()
        if not patch:
            if openclaw_convergence_classification == "toolful_no_edit":
                detail = (
                    openclaw_convergence_reason
                    or "OpenClaw used tools but never produced repository edits"
                )
                raise RuntimeError(
                    "OpenClaw active session used tools but produced no repository edits: "
                    f"{detail}. See {openclaw_edit_convergence_path}.{attempt_matrix_hint}"
                )
            if openclaw_tool_classification == "text_only":
                detail = openclaw_tool_reason or "OpenClaw only streamed assistant text"
                raise RuntimeError(
                    "OpenClaw provider/model contract produced a text-only OpenClaw "
                    f"session: {detail}. See {openclaw_tool_verdict_path}."
                    f"{attempt_matrix_hint}"
                )
            raise RuntimeError(
                f"OpenClaw run did not produce patch.diff{_runner_suffix()}."
                f"{attempt_matrix_hint}"
            )

        if selected_returncode != 0:
            detail = (
                gateway_log.strip()
                or openclaw_tool_reason
                or openclaw_convergence_reason
                or "unknown error"
            )
            raise RuntimeError(
                f"OpenClaw runner exited with status {selected_returncode}: "
                f"{detail.splitlines()[-1]}"
            )

        raw_output = self._read_text_if_exists(local_artifacts / "openclaw_response.txt")
        if not raw_output:
            raw_output = gateway_log or exec_result.stdout
        response_json = {"events": events}
        resolved_repo_root = (
            self._read_text_if_exists(local_artifacts / "repo_root.txt").strip() or repo_root
        )

        workspace_file_contents = self._collect_workspace_files(
            local_artifacts,
            mode="openclaw",
            names=(
                "patch.diff",
                "gateway.log",
                "openclaw_sse_raw.jsonl",
                "openclaw_output.jsonl",
                "openclaw_response.txt",
                "openclaw_runner_stdout.log",
                "openclaw_runner_stderr.log",
                "openclaw_request.json",
                "openclaw_tool_verdict.json",
                "openclaw_edit_convergence.json",
                "openclaw_attempt_matrix.json",
                "openclaw_selected_attempt.json",
                "openclaw_trajectory_summary.json",
                "openclaw_session.jsonl",
                "openclaw_candidate_models.txt",
                "openclaw_prompt_contract.txt",
                "openclaw_prompt_profile.txt",
                "prompt.txt",
                "prompt_budget.json",
                "repo_root.txt",
                "trajectory.jsonl",
                "git_status_before.txt",
                "git_status_after.txt",
            ),
        )
        wall_time = time.monotonic() - start_time
        artifact_manifest = {"files": {}}
        for remote_name in workspace_file_contents:
            artifact_manifest["files"].setdefault("workspace_files", []).append(remote_name)

        assistant_text = raw_output or patch
        return AgentResponse(
            answer=patch,
            trajectory=[
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": assistant_text},
            ],
            raw_output=assistant_text,
            wall_time_sec=wall_time,
            metadata={
                "container_id": container_id,
                "image": image,
                "base_image": base_image,
                "dockerhub_tag": str(task.metadata.get("dockerhub_tag", "")),
                "agent_type": self._agent_type,
                "patch_format": _detect_patch_format(patch),
                "container_started": True,
                "repo_root": resolved_repo_root,
                "artifacts_dir": str(local_artifacts),
                "sample_index": sample_index,
                "execution_id": execution_id,
                "openclaw_completion_cap": prompt_budget["completion_cap"],
                "prompt_budget_path": str(prompt_budget_path),
                "openclaw_request_path": str(local_artifacts / "openclaw_request.json"),
                "openclaw_tool_verdict_path": str(openclaw_tool_verdict_path),
                "openclaw_edit_convergence_path": str(openclaw_edit_convergence_path),
                "openclaw_attempt_matrix_path": str(openclaw_attempt_matrix_path),
                "openclaw_selected_attempt_path": str(openclaw_selected_attempt_path),
                "openclaw_candidate_models_path": str(
                    local_artifacts / "openclaw_candidate_models.txt"
                ),
                "openclaw_trajectory_summary_path": str(
                    local_artifacts / "openclaw_trajectory_summary.json"
                ),
                "openclaw_session_path": str(local_artifacts / "openclaw_session.jsonl"),
                "openclaw_prompt_contract_path": str(
                    local_artifacts / "openclaw_prompt_contract.txt"
                ),
                "openclaw_prompt_profile_path": str(
                    local_artifacts / "openclaw_prompt_profile.txt"
                ),
                "openclaw_candidate_aliases": candidate_aliases,
                "openclaw_selected_model_alias": str(
                    selected_attempt_record.get("resolved_model_alias", "")
                ),
                "bootstrap_needed": bool(runtime_metadata.get("runtime_image_built", False)),
                **runtime_metadata,
            },
            request_messages=[{"role": "user", "content": prompt}],
            response_json=response_json,
            artifact_manifest=artifact_manifest,
            gateway_log_excerpt=gateway_log,
            workspace_file_contents=workspace_file_contents,
        )

    def _solve_opencode(
        self,
        task: BenchmarkTask,
        base_image: str,
        image: str,
        container_id: str,
        start_time: float,
        runtime_metadata: dict[str, Any],
    ) -> AgentResponse:
        """Run OpenCode inside the SWE-bench container and return the resulting patch."""
        if not _OPENCODE_RUNNER.exists() or not _OPENCODE_CONFIG_TEMPLATE.exists():
            raise FileNotFoundError(
                "Missing OpenCode SWE-bench assets. Expected "
                f"{_OPENCODE_RUNNER} and {_OPENCODE_CONFIG_TEMPLATE}."
            )

        repo_root = self._detect_repo_root(container_id)
        mode_env = self._build_mode_env()
        if _is_blank_env_value(mode_env.get("OPENCODE_SMOKE_MODEL_NAME", "")):
            mode_env["OPENCODE_SMOKE_MODEL_NAME"] = str(
                mode_env.get("OPENAI_MODEL_NAME", "")
            ).strip()
        for key, value in {
            "OPENCODE_REQUIRE_PATCH": "1",
            "OPENCODE_PROMPT_PROFILE": "edit_first",
            "OPENCODE_PROBLEM_STATEMENT_MAX_CHARS": "12000",
            "OPENCODE_AUTO_TARGET_HINTS": "1",
        }.items():
            if _is_blank_env_value(mode_env.get(key, "")):
                mode_env[key] = value
        opencode_require_patch = (
            str(mode_env.get("OPENCODE_REQUIRE_PATCH", "1")).strip().lower()
            not in {"0", "false", "no", "off"}
        )
        candidate_aliases = self._resolve_candidate_aliases(
            mode_env,
            candidates_env_key="OPENCODE_SMOKE_MODEL_CANDIDATES",
            primary_env_keys=("OPENCODE_SMOKE_MODEL_NAME", "OPENAI_MODEL_NAME"),
        )
        if not candidate_aliases:
            raise RuntimeError(
                "OpenCode requires at least one model alias. Set OPENCODE_SMOKE_MODEL_NAME "
                "or OPENCODE_SMOKE_MODEL_CANDIDATES."
            )
        strategy_sequence = self._resolve_opencode_strategy_sequence(mode_env)
        mode_env["OPENCODE_SMOKE_MODEL_CANDIDATES"] = ",".join(candidate_aliases)
        mode_env["OPENCODE_STRATEGY_SEQUENCE"] = ",".join(strategy_sequence)
        mode_env["OPENCODE_SMOKE_MODEL_NAME"] = candidate_aliases[0]
        mode_env["OPENAI_MODEL_NAME"] = candidate_aliases[0]
        resolved_target_file_hints, target_file_hints_source = (
            self._derive_opencode_target_file_hints(task, mode_env)
        )
        resolved_primary_target_file = self._resolve_opencode_primary_target_file(
            mode_env,
            target_file_hints=resolved_target_file_hints,
        )
        if resolved_target_file_hints:
            mode_env["OPENCODE_TARGET_FILE_HINTS"] = ",".join(resolved_target_file_hints)
        elif _is_blank_env_value(mode_env.get("OPENCODE_TARGET_FILE_HINTS", "")):
            mode_env["OPENCODE_TARGET_FILE_HINTS"] = ""
        if resolved_primary_target_file:
            mode_env["OPENCODE_PRIMARY_TARGET_FILE"] = resolved_primary_target_file
        elif _is_blank_env_value(mode_env.get("OPENCODE_PRIMARY_TARGET_FILE", "")):
            mode_env["OPENCODE_PRIMARY_TARGET_FILE"] = ""
        local_artifacts, sample_index, execution_id = self._prepare_local_artifacts_dir(task)
        prompt_path = local_artifacts / "prompt.txt"
        opencode_attempts_dir = local_artifacts / "opencode_attempts"
        opencode_attempts_dir.mkdir(parents=True, exist_ok=True)
        attempt_records: list[dict[str, Any]] = []
        selected_attempt_dir: Path | None = None
        selected_attempt_record: dict[str, Any] | None = None
        selected_returncode = 1
        selected_prompt = ""
        selected_prompt_metadata: dict[str, Any] = {
            "prompt_profile": mode_env.get("OPENCODE_PROMPT_PROFILE", "edit_first"),
            "problem_statement_max_chars": self._parse_positive_int(
                mode_env.get("OPENCODE_PROBLEM_STATEMENT_MAX_CHARS")
            ),
            "compactions_applied": [],
            "strategy_name": strategy_sequence[0],
            "target_file_hints": resolved_target_file_hints,
            "primary_target_file": resolved_primary_target_file,
            "target_file_hints_source": target_file_hints_source,
        }

        attempt_index = 0
        patch_found = False
        for alias in candidate_aliases:
            for strategy_name in strategy_sequence:
                attempt_index += 1
                strategy_context = self._resolve_opencode_strategy_context(
                    strategy_name,
                    target_file_hints=resolved_target_file_hints,
                    target_file_hints_source=target_file_hints_source,
                )
                attempt_prompt, attempt_prompt_metadata = self._prepare_opencode_prompt(
                    task,
                    repo_root,
                    mode_env,
                    prompt_profile_override=strategy_context["prompt_profile"],
                    strategy_name=strategy_context["strategy_name"],
                    target_file_hints=strategy_context["target_file_hints"],
                    primary_target_file=resolved_primary_target_file,
                    target_file_hints_source=strategy_context["target_file_hints_source"],
                )
                attempt_name = (
                    f"{attempt_index:02d}_{_safe_artifact_fragment(alias)}"
                    f"__{_safe_artifact_fragment(strategy_context['strategy_name'])}"
                )
                attempt_local_artifacts = opencode_attempts_dir / attempt_name
                attempt_local_artifacts.mkdir(parents=True, exist_ok=True)
                attempt_prompt_path = attempt_local_artifacts / "prompt.txt"
                attempt_prompt_path.write_text(attempt_prompt, encoding="utf-8")
                attempt_execution_id = f"{execution_id}-opencode-{attempt_name}"
                remote_root = self._build_remote_workdir(task, attempt_execution_id)
                remote_artifacts_dir = f"{remote_root}/artifacts"
                remote_prompt_path = f"{remote_root}/prompt.txt"
                remote_script_path = f"{remote_root}/run_opencode.sh"
                remote_config_template = f"{remote_root}/opencode.json.template"

                self._stage_file_into_container(
                    container_id,
                    local_path=attempt_prompt_path,
                    remote_path=remote_prompt_path,
                )
                self._stage_file_into_container(
                    container_id,
                    local_path=_OPENCODE_RUNNER,
                    remote_path=remote_script_path,
                )
                self._stage_file_into_container(
                    container_id,
                    local_path=_OPENCODE_CONFIG_TEMPLATE,
                    remote_path=remote_config_template,
                )
                self._docker_exec(
                    container_id,
                    ["bash", "-lc", f"mkdir -p {shlex.quote(remote_artifacts_dir)}"],
                    timeout=30,
                )

                exec_env = {
                    **mode_env,
                    "OPENAI_MODEL_NAME": alias,
                    "OPENCODE_SMOKE_MODEL_NAME": alias,
                    "OPENCODE_PROMPT_PROFILE": strategy_context["prompt_profile"],
                    "OPENCODE_STRATEGY_NAME": strategy_context["strategy_name"],
                    "OPENCODE_TARGET_FILE_HINTS": ",".join(
                        strategy_context["target_file_hints"]
                    ),
                    "OPENCODE_PRIMARY_TARGET_FILE": resolved_primary_target_file,
                    "OPENCODE_TARGET_FILE_HINTS_SOURCE": strategy_context[
                        "target_file_hints_source"
                    ],
                    "ALPHADIANA_ARTIFACTS_DIR": remote_artifacts_dir,
                    "ALPHADIANA_PROMPT_FILE": remote_prompt_path,
                    "ALPHADIANA_CONFIG_TEMPLATE": remote_config_template,
                }
                exec_result = self._docker_exec(
                    container_id,
                    [
                        "bash",
                        "-lc",
                        f"chmod +x {shlex.quote(remote_script_path)} && {shlex.quote(remote_script_path)}",
                    ],
                    env=exec_env,
                    timeout=self._timeout,
                    check=False,
                )

                self._docker_cp_from(
                    container_id,
                    f"{remote_artifacts_dir}/.",
                    str(attempt_local_artifacts),
                )
                (attempt_local_artifacts / "opencode_runner_stdout.log").write_text(
                    exec_result.stdout,
                    encoding="utf-8",
                    errors="replace",
                )
                (attempt_local_artifacts / "opencode_runner_stderr.log").write_text(
                    exec_result.stderr,
                    encoding="utf-8",
                    errors="replace",
                )

                attempt_patch_path = attempt_local_artifacts / "patch.diff"
                attempt_patch = self._read_text_if_exists(attempt_patch_path).strip()
                attempt_provider_preflight = self._read_text_if_exists(
                    attempt_local_artifacts / "opencode_provider_preflight.txt"
                )
                attempt_startup = self._read_text_if_exists(
                    attempt_local_artifacts / "opencode_startup_diagnostics.txt"
                )
                attempt_activity_summary = (
                    self._read_json_if_exists(
                        attempt_local_artifacts / "opencode_activity_summary.json"
                    )
                    or {}
                )
                attempt_stall_reason = self._read_text_if_exists(
                    attempt_local_artifacts / "opencode_stall_reason.txt"
                ).strip()
                attempt_no_edit_reason = self._read_text_if_exists(
                    attempt_local_artifacts / "opencode_no_edit_reason.txt"
                ).strip()
                preflight_status = _extract_prefixed_value(
                    attempt_provider_preflight, "status: "
                )
                startup_status = _extract_prefixed_value(attempt_startup, "status: ")
                classification = str(
                    attempt_activity_summary.get("classification", "")
                ).strip()
                if not classification and attempt_patch:
                    classification = "patch_created"
                if not classification and preflight_status == "failed":
                    classification = "provider_preflight_failed"
                if not classification and attempt_stall_reason:
                    classification = "stalled_no_progress"
                if not classification and startup_status:
                    classification = startup_status
                attempt_reason = (
                    attempt_no_edit_reason
                    or _extract_prefixed_value(attempt_provider_preflight, "error: ")
                    or attempt_stall_reason
                    or self._read_text_if_exists(
                        attempt_local_artifacts / "opencode_stderr.log"
                    ).strip()
                    or exec_result.stderr.strip()
                    or exec_result.stdout.strip()
                    or "unknown OpenCode outcome"
                ).strip()
                attempt_record = {
                    "attempt_index": attempt_index,
                    "resolved_model_alias": alias,
                    "strategy_name": strategy_context["strategy_name"],
                    "prompt_profile": attempt_prompt_metadata["prompt_profile"],
                    "target_file_hints": strategy_context["target_file_hints"],
                    "primary_target_file": attempt_prompt_metadata["primary_target_file"],
                    "target_file_hints_source": strategy_context[
                        "target_file_hints_source"
                    ],
                    "preflight_status": preflight_status or "unknown",
                    "startup_status": startup_status or "unknown",
                    "classification": classification or "provider_failure",
                    "patch_size_bytes": attempt_patch_path.stat().st_size
                    if attempt_patch_path.exists()
                    else 0,
                    "tool_use_count": int(
                        attempt_activity_summary.get("tool_use_count") or 0
                    ),
                    "tracked_repo_change_count": int(
                        attempt_activity_summary.get("tracked_repo_change_count") or 0
                    ),
                    "tracked_repo_changed_paths": list(
                        attempt_activity_summary.get("tracked_repo_changed_paths") or []
                    ),
                    "primary_target_file_changed": bool(
                        attempt_activity_summary.get("primary_target_file_changed")
                    ),
                    "reason": attempt_reason,
                    "returncode": exec_result.returncode,
                    "artifacts_dir": str(attempt_local_artifacts),
                }
                attempt_records.append(attempt_record)
                selected_attempt_dir = attempt_local_artifacts
                selected_attempt_record = attempt_record
                selected_returncode = exec_result.returncode
                selected_prompt = attempt_prompt
                selected_prompt_metadata = attempt_prompt_metadata
                if attempt_patch and exec_result.returncode == 0:
                    patch_found = True
                    break
            if patch_found:
                break

        if selected_attempt_dir is None or selected_attempt_record is None:
            raise RuntimeError("OpenCode attempts did not produce any artifacts")
        prompt_path.write_text(selected_prompt, encoding="utf-8")

        opencode_attempt_matrix_path = local_artifacts / "opencode_attempt_matrix.json"
        opencode_selected_attempt_path = local_artifacts / "opencode_selected_attempt.json"
        opencode_attempt_matrix_path.write_text(
            json.dumps(
                {
                    "attempts": attempt_records,
                    "selected_attempt_index": selected_attempt_record["attempt_index"],
                    "tried_aliases": candidate_aliases,
                    "tried_strategy_names": strategy_sequence,
                    "resolved_target_file_hints": resolved_target_file_hints,
                    "target_file_hints_source": target_file_hints_source,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        opencode_selected_attempt_path.write_text(
            json.dumps(selected_attempt_record, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        self._copy_attempt_files(
            selected_attempt_dir,
            local_artifacts,
            exclude_names={
                "opencode_attempt_matrix.json",
                "opencode_selected_attempt.json",
            },
        )

        opencode_output = self._read_text_if_exists(local_artifacts / "opencode_output.jsonl")
        opencode_stderr = self._read_text_if_exists(local_artifacts / "opencode_stderr.log")
        opencode_provider_preflight = self._read_text_if_exists(
            local_artifacts / "opencode_provider_preflight.txt"
        )
        opencode_startup_diagnostics = self._read_text_if_exists(
            local_artifacts / "opencode_startup_diagnostics.txt"
        )
        opencode_activity_summary_path = local_artifacts / "opencode_activity_summary.json"
        opencode_activity_summary = (
            self._read_json_if_exists(opencode_activity_summary_path) or {}
        )
        opencode_activity_classification = str(
            opencode_activity_summary.get("classification", "")
        ).strip()
        opencode_stall_reason = self._read_text_if_exists(
            local_artifacts / "opencode_stall_reason.txt"
        )
        opencode_progress_snapshot = self._read_text_if_exists(
            local_artifacts / "opencode_progress_snapshot.txt"
        )
        opencode_no_edit_reason_path = local_artifacts / "opencode_no_edit_reason.txt"
        opencode_no_edit_reason = self._read_text_if_exists(opencode_no_edit_reason_path).strip()
        selected_hint_summary = ", ".join(
            str(item).strip()
            for item in selected_attempt_record.get("target_file_hints", [])
            if str(item).strip()
        )
        attempt_matrix_hint = (
            " Tried alias/strategy combinations: "
            + ", ".join(
                f"{record.get('resolved_model_alias', '')}[{record.get('strategy_name', 'edit_first')}]"
                for record in attempt_records
            )
            + ". "
            + "Selected attempt: "
            + f"alias={selected_attempt_record.get('resolved_model_alias', '')}, "
            + f"strategy={selected_attempt_record.get('strategy_name', 'edit_first')}, "
            + f"hints={selected_hint_summary or '<none>'}. "
            + f"See {opencode_attempt_matrix_path}"
        )

        def _runner_suffix() -> str:
            if selected_returncode == 0:
                return ""
            detail = opencode_stderr.strip() or opencode_no_edit_reason or opencode_stall_reason.strip()
            if detail:
                return (
                    f" (runner exited {selected_returncode}: "
                    f"{detail.splitlines()[-1]})"
                )
            return f" (runner exited {selected_returncode})"

        if "status: failed" in opencode_provider_preflight:
            model_alias = ""
            error_detail = ""
            for line in opencode_provider_preflight.splitlines():
                if line.startswith("model: "):
                    model_alias = line.split(": ", 1)[1].strip()
                if line.startswith("error: "):
                    error_detail = line.split(": ", 1)[1].strip()
            detail = error_detail or "unknown provider-preflight failure"
            if model_alias:
                detail = f"{detail} (model={model_alias})"
            raise RuntimeError(f"OpenCode provider preflight failed: {detail}.{attempt_matrix_hint}")

        if "status: no_activity_within_timeout" in opencode_startup_diagnostics:
            raise RuntimeError(
                "OpenCode startup produced no session/output activity. "
                f"See {local_artifacts / 'opencode_startup_diagnostics.txt'}.{attempt_matrix_hint}"
            )
        if "status: process_exited_before_activity" in opencode_startup_diagnostics:
            raise RuntimeError(
                "OpenCode startup exited before any session/output activity. "
                f"See {local_artifacts / 'opencode_startup_diagnostics.txt'}.{attempt_matrix_hint}"
            )

        if opencode_stall_reason.strip():
            detail = opencode_stall_reason.strip()
            if detail.startswith("OpenCode stalled after"):
                detail = "OpenCode run stalled after" + detail[len("OpenCode stalled after") :]
            elif not detail.startswith("OpenCode run stalled after"):
                detail = f"OpenCode run stalled after: {detail}"
            snapshot_summary = ""
            for line in opencode_progress_snapshot.splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("=====") and stripped != "OpenCode progress snapshot":
                    snapshot_summary = stripped
                    break
            if snapshot_summary:
                detail = f"{detail}. Last progress: {snapshot_summary}"
            raise RuntimeError(f"{detail}.{attempt_matrix_hint}")

        if not opencode_output.strip():
            if not (local_artifacts / "opencode_output.jsonl").exists():
                raise RuntimeError(
                    f"OpenCode run did not produce opencode_output.jsonl"
                    f"{_runner_suffix()}.{attempt_matrix_hint}"
                )
            raise RuntimeError(
                f"OpenCode run produced an empty opencode_output.jsonl"
                f"{_runner_suffix()}.{attempt_matrix_hint}"
            )

        records: list[dict[str, Any]] = []
        for index, line in enumerate(opencode_output.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    "OpenCode run produced invalid JSONL in opencode_output.jsonl "
                    f"(line {index}): {exc}"
                ) from exc
            if not isinstance(payload, dict):
                raise RuntimeError(
                    "OpenCode run produced a non-object JSONL record in "
                    f"opencode_output.jsonl (line {index})"
                )
            records.append(payload)

        if not records:
            raise RuntimeError(
                f"OpenCode run produced an empty opencode_output.jsonl{_runner_suffix()}"
            )

        for record in records:
            if record.get("type") == "error":
                message = self._extract_opencode_error_message(record)
                raise RuntimeError(f"OpenCode run reported error record: {message}.{attempt_matrix_hint}")

        tolerate_no_edit_without_patch = (
            not opencode_require_patch
            and opencode_activity_classification == "active_session_no_patch"
        )

        if opencode_activity_classification == "active_session_no_patch" and opencode_require_patch:
            detail = opencode_no_edit_reason or str(
                opencode_activity_summary.get("reason", "")
            ).strip()
            if not detail:
                detail = "active session produced output but no tracked repository edits"
            raise RuntimeError(
                "OpenCode active session timed out without repository edits: "
                f"{detail}. See {opencode_activity_summary_path}.{attempt_matrix_hint}"
            )

        patch = self._read_text_if_exists(local_artifacts / "patch.diff").strip()
        if not patch and opencode_require_patch:
            raise RuntimeError(
                f"OpenCode run did not produce patch.diff{_runner_suffix()}."
                f"{attempt_matrix_hint}"
            )

        if selected_returncode != 0 and not tolerate_no_edit_without_patch:
            detail = (
                opencode_stderr.strip()
                or opencode_no_edit_reason
                or opencode_stall_reason.strip()
                or "unknown error"
            )
            raise RuntimeError(
                f"OpenCode runner exited with status {selected_returncode}: "
                f"{detail.splitlines()[-1]}"
            )

        resolved_repo_root = (
            self._read_text_if_exists(local_artifacts / "repo_root.txt").strip() or repo_root
        )
        workspace_file_contents = self._collect_workspace_files(
            local_artifacts,
            mode="opencode",
            names=(
                "patch.diff",
                "opencode_output.jsonl",
                "opencode_stderr.log",
                "opencode_session.jsonl",
                "opencode_provider_preflight.txt",
                "opencode_startup_diagnostics.txt",
                "opencode_activity_summary.json",
                "opencode_no_edit_reason.txt",
                "opencode_prompt_contract.txt",
                "opencode_prompt_profile.txt",
                "opencode_candidate_models.txt",
                "opencode_target_file_hints.txt",
                "opencode_edit_bootstrap.txt",
                "opencode_edit_contract.txt",
                "opencode_attempt_matrix.json",
                "opencode_selected_attempt.json",
                "opencode_stall_reason.txt",
                "opencode_progress_snapshot.txt",
                "opencode_runner_stdout.log",
                "opencode_runner_stderr.log",
                "git_status_before.txt",
                "git_status_after.txt",
                "repo_root.txt",
            ),
        )
        artifact_manifest = {"files": {}}
        for remote_name in workspace_file_contents:
            artifact_manifest["files"].setdefault("workspace_files", []).append(remote_name)

        wall_time = time.monotonic() - start_time
        assistant_text = opencode_output.strip() or patch
        return AgentResponse(
            answer=patch,
            trajectory=[
                {"role": "user", "content": selected_prompt},
                {"role": "assistant", "content": assistant_text},
            ],
            raw_output=assistant_text,
            wall_time_sec=wall_time,
            metadata={
                "container_id": container_id,
                "image": image,
                "base_image": base_image,
                "dockerhub_tag": str(task.metadata.get("dockerhub_tag", "")),
                "agent_type": self._agent_type,
                "patch_format": _detect_patch_format(patch),
                "container_started": True,
                "repo_root": resolved_repo_root,
                "artifacts_dir": str(local_artifacts),
                "sample_index": sample_index,
                "execution_id": execution_id,
                "opencode_prompt_profile": selected_prompt_metadata["prompt_profile"],
                "opencode_prompt_compactions_applied": selected_prompt_metadata[
                    "compactions_applied"
                ],
                "opencode_output_path": str(local_artifacts / "opencode_output.jsonl"),
                "opencode_stderr_path": str(local_artifacts / "opencode_stderr.log"),
                "opencode_session_path": str(local_artifacts / "opencode_session.jsonl"),
                "opencode_provider_preflight_path": str(
                    local_artifacts / "opencode_provider_preflight.txt"
                ),
                "opencode_startup_diagnostics_path": str(
                    local_artifacts / "opencode_startup_diagnostics.txt"
                ),
                "opencode_activity_summary_path": str(opencode_activity_summary_path),
                "opencode_no_edit_reason_path": str(opencode_no_edit_reason_path),
                "opencode_prompt_contract_path": str(
                    local_artifacts / "opencode_prompt_contract.txt"
                ),
                "opencode_prompt_profile_path": str(
                    local_artifacts / "opencode_prompt_profile.txt"
                ),
                "opencode_candidate_models_path": str(
                    local_artifacts / "opencode_candidate_models.txt"
                ),
                "opencode_target_file_hints_path": str(
                    local_artifacts / "opencode_target_file_hints.txt"
                ),
                "opencode_edit_bootstrap_path": str(
                    local_artifacts / "opencode_edit_bootstrap.txt"
                ),
                "opencode_edit_contract_path": str(
                    local_artifacts / "opencode_edit_contract.txt"
                ),
                "opencode_attempt_matrix_path": str(opencode_attempt_matrix_path),
                "opencode_selected_attempt_path": str(opencode_selected_attempt_path),
                "opencode_stall_reason_path": str(local_artifacts / "opencode_stall_reason.txt"),
                "opencode_progress_snapshot_path": str(
                    local_artifacts / "opencode_progress_snapshot.txt"
                ),
                "git_status_before_path": str(local_artifacts / "git_status_before.txt"),
                "git_status_after_path": str(local_artifacts / "git_status_after.txt"),
                "opencode_candidate_aliases": candidate_aliases,
                "opencode_require_patch": opencode_require_patch,
                "opencode_strategy_sequence": strategy_sequence,
                "opencode_selected_model_alias": str(
                    selected_attempt_record.get("resolved_model_alias", "")
                ),
                "opencode_selected_classification": str(
                    selected_attempt_record.get("classification", "")
                ),
                "opencode_selected_reason": str(
                    selected_attempt_record.get("reason", "")
                ),
                "opencode_selected_strategy_name": str(
                    selected_attempt_record.get("strategy_name", "")
                ),
                "opencode_selected_target_file_hints": list(
                    selected_attempt_record.get("target_file_hints", [])
                ),
                "opencode_selected_primary_target_file": str(
                    selected_attempt_record.get("primary_target_file", "")
                ),
                "opencode_selected_target_file_hints_source": str(
                    selected_attempt_record.get("target_file_hints_source", "")
                ),
                "opencode_selected_tracked_repo_changed_paths": list(
                    selected_attempt_record.get("tracked_repo_changed_paths", [])
                ),
                "opencode_selected_primary_target_file_changed": bool(
                    selected_attempt_record.get("primary_target_file_changed")
                ),
                "bootstrap_needed": bool(runtime_metadata.get("runtime_image_built", False)),
                **runtime_metadata,
            },
            request_messages=[{"role": "user", "content": selected_prompt}],
            response_json={"records": records},
            artifact_manifest=artifact_manifest,
            gateway_log_excerpt=opencode_stderr,
            workspace_file_contents=workspace_file_contents,
        )

    def _solve_zeroclaw(
        self,
        task: BenchmarkTask,
        base_image: str,
        image: str,
        container_id: str,
        start_time: float,
        runtime_metadata: dict[str, Any],
    ) -> AgentResponse:
        """Run ZeroClaw inside the SWE-bench container and return the resulting patch."""
        if not _ZEROCLAW_RUNNER.exists() or not _ZEROCLAW_CONFIG_TEMPLATE.exists():
            raise FileNotFoundError(
                "Missing ZeroClaw SWE-bench assets. Expected "
                f"{_ZEROCLAW_RUNNER} and {_ZEROCLAW_CONFIG_TEMPLATE}."
            )

        repo_root = self._detect_repo_root(container_id)
        mode_env = self._build_mode_env()
        if _is_blank_env_value(mode_env.get("ZEROCLAW_SMOKE_MODEL_NAME", "")):
            mode_env["ZEROCLAW_SMOKE_MODEL_NAME"] = str(
                mode_env.get("OPENAI_MODEL_NAME", "")
            ).strip()
        for key, value in {
            "ZEROCLAW_REQUIRE_PATCH": "1",
            "ZEROCLAW_PROMPT_PROFILE": "edit_first",
            "ZEROCLAW_PROBLEM_STATEMENT_MAX_CHARS": "12000",
            "ZEROCLAW_TIMEOUT_SEC": str(max(self._timeout - 60, 60)),
            "ZEROCLAW_WORKSPACE_ONLY": "false",
            "ZEROCLAW_MAX_TOOL_ITERATIONS": "100",
            "ZEROCLAW_MAX_ACTIONS_PER_HOUR": "200",
            "ZEROCLAW_RUNTIME_TRACE_MODE": "none",
        }.items():
            if _is_blank_env_value(mode_env.get(key, "")):
                mode_env[key] = value
        zeroclaw_require_patch = (
            str(mode_env.get("ZEROCLAW_REQUIRE_PATCH", "1")).strip().lower()
            not in {"0", "false", "no", "off"}
        )
        candidate_aliases = self._resolve_candidate_aliases(
            mode_env,
            candidates_env_key="ZEROCLAW_SMOKE_MODEL_CANDIDATES",
            primary_env_keys=("ZEROCLAW_SMOKE_MODEL_NAME", "OPENAI_MODEL_NAME"),
        )
        if not candidate_aliases:
            raise RuntimeError(
                "ZeroClaw requires at least one model alias. Set ZEROCLAW_SMOKE_MODEL_NAME "
                "or ZEROCLAW_SMOKE_MODEL_CANDIDATES."
            )
        mode_env["ZEROCLAW_SMOKE_MODEL_CANDIDATES"] = ",".join(candidate_aliases)
        mode_env["ZEROCLAW_SMOKE_MODEL_NAME"] = candidate_aliases[0]
        mode_env["OPENAI_MODEL_NAME"] = candidate_aliases[0]
        local_artifacts, sample_index, execution_id = self._prepare_local_artifacts_dir(task)
        prompt_path = local_artifacts / "prompt.txt"
        zeroclaw_attempts_dir = local_artifacts / "zeroclaw_attempts"
        zeroclaw_attempts_dir.mkdir(parents=True, exist_ok=True)
        attempt_records: list[dict[str, Any]] = []
        selected_attempt_dir: Path | None = None
        selected_attempt_record: dict[str, Any] | None = None
        selected_returncode = 1
        selected_prompt = ""
        selected_prompt_metadata: dict[str, Any] = {
            "prompt_profile": mode_env.get("ZEROCLAW_PROMPT_PROFILE", "edit_first"),
            "problem_statement_max_chars": self._parse_positive_int(
                mode_env.get("ZEROCLAW_PROBLEM_STATEMENT_MAX_CHARS")
            ),
            "compactions_applied": [],
        }

        for index, alias in enumerate(candidate_aliases, start=1):
            attempt_name = f"{index:02d}_{_safe_artifact_fragment(alias)}"
            attempt_local_artifacts = zeroclaw_attempts_dir / attempt_name
            attempt_local_artifacts.mkdir(parents=True, exist_ok=True)
            attempt_prompt, attempt_prompt_metadata = self._prepare_zeroclaw_prompt(
                task,
                repo_root,
                {
                    **mode_env,
                    "OPENAI_MODEL_NAME": alias,
                    "ZEROCLAW_SMOKE_MODEL_NAME": alias,
                },
            )
            attempt_prompt_path = attempt_local_artifacts / "prompt.txt"
            attempt_prompt_path.write_text(attempt_prompt, encoding="utf-8")
            attempt_execution_id = f"{execution_id}-zeroclaw-{attempt_name}"
            remote_root = self._build_remote_workdir(task, attempt_execution_id)
            remote_artifacts_dir = f"{remote_root}/artifacts"
            remote_prompt_path = f"{remote_root}/prompt.txt"
            remote_script_path = f"{remote_root}/run_zeroclaw.sh"
            remote_config_template = f"{remote_root}/zeroclaw.toml.template"

            self._stage_file_into_container(
                container_id,
                local_path=attempt_prompt_path,
                remote_path=remote_prompt_path,
            )
            self._stage_file_into_container(
                container_id,
                local_path=_ZEROCLAW_RUNNER,
                remote_path=remote_script_path,
            )
            self._stage_file_into_container(
                container_id,
                local_path=_ZEROCLAW_CONFIG_TEMPLATE,
                remote_path=remote_config_template,
            )
            self._docker_exec(
                container_id,
                ["bash", "-lc", f"mkdir -p {shlex.quote(remote_artifacts_dir)}"],
                timeout=30,
            )

            exec_env = {
                **mode_env,
                "OPENAI_MODEL_NAME": alias,
                "ZEROCLAW_SMOKE_MODEL_NAME": alias,
                "ALPHADIANA_ARTIFACTS_DIR": remote_artifacts_dir,
                "ALPHADIANA_PROMPT_FILE": remote_prompt_path,
                "ALPHADIANA_CONFIG_TEMPLATE": remote_config_template,
            }
            exec_result = self._docker_exec(
                container_id,
                [
                    "bash",
                    "-lc",
                    f"chmod +x {shlex.quote(remote_script_path)} && {shlex.quote(remote_script_path)}",
                ],
                env=exec_env,
                timeout=self._timeout,
                check=False,
            )

            self._docker_cp_from(
                container_id,
                f"{remote_artifacts_dir}/.",
                str(attempt_local_artifacts),
            )
            (attempt_local_artifacts / "zeroclaw_runner_stdout.log").write_text(
                exec_result.stdout,
                encoding="utf-8",
                errors="replace",
            )
            (attempt_local_artifacts / "zeroclaw_runner_stderr.log").write_text(
                exec_result.stderr,
                encoding="utf-8",
                errors="replace",
            )

            attempt_patch_path = attempt_local_artifacts / "patch.diff"
            attempt_patch = self._read_text_if_exists(attempt_patch_path).strip()
            attempt_selected = (
                self._read_json_if_exists(attempt_local_artifacts / "zeroclaw_selected_attempt.json")
                or {}
            )
            attempt_no_edit_reason = self._read_text_if_exists(
                attempt_local_artifacts / "zeroclaw_no_edit_reason.txt"
            ).strip()
            attempt_reason = str(
                attempt_selected.get("reason")
                or attempt_no_edit_reason
                or self._read_text_if_exists(
                    attempt_local_artifacts / "zeroclaw_stderr.log"
                ).strip()
                or exec_result.stderr.strip()
                or exec_result.stdout.strip()
                or "unknown ZeroClaw outcome"
            ).strip()
            attempt_record = {
                "attempt_index": index,
                "resolved_model_alias": alias,
                "prompt_profile": attempt_prompt_metadata["prompt_profile"],
                "classification": str(
                    attempt_selected.get("classification")
                    or ("patch_created" if attempt_patch else "cli_error")
                ).strip(),
                "patch_size_bytes": attempt_patch_path.stat().st_size
                if attempt_patch_path.exists()
                else 0,
                "tracked_repo_change_count": int(
                    attempt_selected.get("tracked_repo_change_count") or 0
                ),
                "untracked_repo_change_count": int(
                    attempt_selected.get("untracked_repo_change_count") or 0
                ),
                "reason": attempt_reason,
                "returncode": exec_result.returncode,
                "artifacts_dir": str(attempt_local_artifacts),
            }
            attempt_records.append(attempt_record)
            selected_attempt_dir = attempt_local_artifacts
            selected_attempt_record = attempt_record
            selected_returncode = exec_result.returncode
            selected_prompt = attempt_prompt
            selected_prompt_metadata = attempt_prompt_metadata
            if attempt_patch and exec_result.returncode == 0:
                break

        if selected_attempt_dir is None or selected_attempt_record is None:
            raise RuntimeError("ZeroClaw attempts did not produce any artifacts")
        prompt_path.write_text(selected_prompt, encoding="utf-8")

        zeroclaw_attempt_matrix_path = local_artifacts / "zeroclaw_attempt_matrix.json"
        zeroclaw_selected_attempt_path = local_artifacts / "zeroclaw_selected_attempt.json"
        zeroclaw_attempt_matrix_path.write_text(
            json.dumps(
                {
                    "attempts": attempt_records,
                    "selected_attempt_index": selected_attempt_record["attempt_index"],
                    "tried_aliases": candidate_aliases,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        zeroclaw_selected_attempt_path.write_text(
            json.dumps(selected_attempt_record, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        self._copy_attempt_files(
            selected_attempt_dir,
            local_artifacts,
            exclude_names={
                "zeroclaw_attempt_matrix.json",
                "zeroclaw_selected_attempt.json",
            },
        )

        zeroclaw_output = self._read_text_if_exists(local_artifacts / "zeroclaw_output.txt")
        zeroclaw_stderr = self._read_text_if_exists(local_artifacts / "zeroclaw_stderr.log")
        zeroclaw_runtime_trace = self._read_text_if_exists(
            local_artifacts / "runtime_trace.jsonl"
        )
        zeroclaw_runner_stdout = self._read_text_if_exists(
            local_artifacts / "zeroclaw_runner_stdout.log"
        )
        zeroclaw_runner_stderr = self._read_text_if_exists(
            local_artifacts / "zeroclaw_runner_stderr.log"
        )
        zeroclaw_no_edit_reason_path = local_artifacts / "zeroclaw_no_edit_reason.txt"
        zeroclaw_no_edit_reason = self._read_text_if_exists(zeroclaw_no_edit_reason_path).strip()
        selected_classification = str(
            selected_attempt_record.get("classification", "")
        ).strip()
        selected_reason = str(selected_attempt_record.get("reason", "")).strip()
        patch = self._read_text_if_exists(local_artifacts / "patch.diff").strip()
        resolved_repo_root = (
            self._read_text_if_exists(local_artifacts / "repo_root.txt").strip() or repo_root
        )
        workspace_file_contents = self._collect_workspace_files(
            local_artifacts,
            mode="zeroclaw",
            names=(
                "patch.diff",
                "zeroclaw_output.txt",
                "zeroclaw_stderr.log",
                "runtime_trace.jsonl",
                "zeroclaw_no_edit_reason.txt",
                "zeroclaw_prompt_contract.txt",
                "zeroclaw_prompt_profile.txt",
                "zeroclaw_candidate_models.txt",
                "zeroclaw_attempt_matrix.json",
                "zeroclaw_selected_attempt.json",
                "zeroclaw_runner_stdout.log",
                "zeroclaw_runner_stderr.log",
                "prompt.txt",
                "git_status_before.txt",
                "git_status_after.txt",
                "repo_root.txt",
            ),
        )
        artifact_manifest = {"files": {}}
        for remote_name in workspace_file_contents:
            artifact_manifest["files"].setdefault("workspace_files", []).append(remote_name)

        wall_time = time.monotonic() - start_time
        assistant_text = (
            zeroclaw_output.strip()
            or patch
            or zeroclaw_no_edit_reason
            or zeroclaw_stderr.strip()
            or zeroclaw_runner_stderr.strip()
            or zeroclaw_runner_stdout.strip()
            or selected_reason
            or "ZeroClaw completed without producing a patch."
        )
        zeroclaw_failure_detail = (
            zeroclaw_stderr.strip()
            or zeroclaw_no_edit_reason
            or zeroclaw_runner_stderr.strip()
            or zeroclaw_runner_stdout.strip()
            or selected_reason
        )
        zeroclaw_preserved_failure = bool(
            selected_returncode != 0 or not patch or selected_classification != "patch_created"
        )
        return AgentResponse(
            answer=patch,
            trajectory=[
                {"role": "user", "content": selected_prompt},
                {"role": "assistant", "content": assistant_text},
            ],
            raw_output=assistant_text,
            wall_time_sec=wall_time,
            metadata={
                "container_id": container_id,
                "image": image,
                "base_image": base_image,
                "dockerhub_tag": str(task.metadata.get("dockerhub_tag", "")),
                "agent_type": self._agent_type,
                "patch_format": _detect_patch_format(patch),
                "container_started": True,
                "repo_root": resolved_repo_root,
                "artifacts_dir": str(local_artifacts),
                "sample_index": sample_index,
                "execution_id": execution_id,
                "zeroclaw_prompt_profile": selected_prompt_metadata["prompt_profile"],
                "zeroclaw_prompt_compactions_applied": selected_prompt_metadata[
                    "compactions_applied"
                ],
                "zeroclaw_output_path": str(local_artifacts / "zeroclaw_output.txt"),
                "zeroclaw_stderr_path": str(local_artifacts / "zeroclaw_stderr.log"),
                "zeroclaw_runtime_trace_path": str(local_artifacts / "runtime_trace.jsonl"),
                "zeroclaw_no_edit_reason_path": str(zeroclaw_no_edit_reason_path),
                "zeroclaw_prompt_contract_path": str(
                    local_artifacts / "zeroclaw_prompt_contract.txt"
                ),
                "zeroclaw_prompt_profile_path": str(
                    local_artifacts / "zeroclaw_prompt_profile.txt"
                ),
                "zeroclaw_candidate_models_path": str(
                    local_artifacts / "zeroclaw_candidate_models.txt"
                ),
                "zeroclaw_attempt_matrix_path": str(zeroclaw_attempt_matrix_path),
                "zeroclaw_selected_attempt_path": str(zeroclaw_selected_attempt_path),
                "git_status_before_path": str(local_artifacts / "git_status_before.txt"),
                "git_status_after_path": str(local_artifacts / "git_status_after.txt"),
                "zeroclaw_candidate_aliases": candidate_aliases,
                "zeroclaw_require_patch": zeroclaw_require_patch,
                "zeroclaw_selected_model_alias": str(
                    selected_attempt_record.get("resolved_model_alias", "")
                ),
                "zeroclaw_selected_returncode": selected_returncode,
                "zeroclaw_selected_classification": selected_classification,
                "zeroclaw_selected_reason": selected_reason,
                "zeroclaw_preserved_failure": zeroclaw_preserved_failure,
                "bootstrap_needed": bool(runtime_metadata.get("runtime_image_built", False)),
                **runtime_metadata,
            },
            request_messages=[{"role": "user", "content": selected_prompt}],
            response_json={
                "output_text": zeroclaw_output.strip(),
                "stderr_text": zeroclaw_stderr.strip(),
                "runtime_trace_present": bool(zeroclaw_runtime_trace.strip()),
                "runner_stdout_text": zeroclaw_runner_stdout.strip(),
                "runner_stderr_text": zeroclaw_runner_stderr.strip(),
                "selected_returncode": selected_returncode,
                "selected_classification": selected_classification,
            },
            artifact_manifest=artifact_manifest,
            gateway_log_excerpt=zeroclaw_stderr,
            workspace_file_contents=workspace_file_contents,
            finish_reason=(
                "completed"
                if not zeroclaw_preserved_failure
                else "preserved_failure"
            ),
        )

    def _solve_direct_llm(
        self,
        task: BenchmarkTask,
        image: str,
        container_id: str,
        start_time: float,
    ) -> AgentResponse:
        """Generate a patch string from task.problem via an OpenAI-compatible API."""
        if self._client is None:
            try:
                from openai import OpenAI

                self._client = OpenAI(base_url=self._api_base, api_key=self._api_key)
            except ImportError as exc:
                raise RuntimeError(
                    "The 'openai' package is required for swebench_docker direct_llm mode. "
                    "Install with: pip install openai"
                ) from exc

        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": task.problem},
        ]
        request_kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
        }
        if self._max_completion_tokens is not None:
            request_kwargs["max_completion_tokens"] = self._max_completion_tokens
        else:
            raw_max = self._resolve_max_tokens()
            request_kwargs["max_tokens"] = self._cap_max_tokens(raw_max, messages)
        if self._top_p is not None:
            request_kwargs["top_p"] = self._top_p

        last_exc: Exception | None = None
        raw_output = ""
        raw_reasoning = ""
        finish_reason = ""
        token_usage: dict[str, Any] = {}
        response_json: dict[str, Any] = {}

        for attempt in range(self._max_retries + 1):
            try:
                if self._stream:
                    raw_output, finish_reason, token_usage, raw_reasoning = (
                        self._call_streaming(request_kwargs)
                    )
                    if raw_reasoning:
                        response_json = {
                            "choices": [{
                                "message": {
                                    "role": "assistant",
                                    "content": raw_output,
                                    "reasoning_content": raw_reasoning,
                                }
                            }]
                        }
                else:
                    raw_output, finish_reason, token_usage, raw_reasoning, response_json = (
                        self._call_non_streaming(request_kwargs)
                    )
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                if not self._is_retryable(exc) or attempt == self._max_retries:
                    break
                delay = min(2.0 * (2 ** attempt), 60.0)
                jitter = random.uniform(0, delay * 0.3)
                logger.warning(
                    "swebench_docker direct_llm attempt %d/%d failed: %s. Retrying in %.1fs",
                    attempt + 1,
                    self._max_retries,
                    exc,
                    delay + jitter,
                )
                time.sleep(delay + jitter)

        if last_exc is not None:
            raise last_exc
        if not raw_output.strip():
            raise RuntimeError("swebench_docker direct_llm returned empty output")

        patch = _extract_patch(raw_output)
        patch_format = _detect_patch_format(patch)
        wall_time = time.monotonic() - start_time

        reasoning_trajectory: list[dict[str, str]] = []
        if raw_reasoning:
            reasoning_trajectory.append(
                {"role": "assistant", "reasoning_content": raw_reasoning}
            )

        assistant_msg: dict[str, str] = {"role": "assistant", "content": raw_output}
        if raw_reasoning:
            assistant_msg["thinking"] = raw_reasoning

        return AgentResponse(
            answer=patch,
            trajectory=[
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": task.problem},
                assistant_msg,
            ],
            raw_output=raw_output,
            token_usage=token_usage,
            wall_time_sec=wall_time,
            metadata={
                "container_id": container_id,
                "image": image,
                "dockerhub_tag": str(task.metadata.get("dockerhub_tag", "")),
                "agent_type": self._agent_type,
                "patch_format": patch_format,
                "container_started": True,
            },
            reasoning_trajectory=reasoning_trajectory,
            request_messages=messages,
            response_json=response_json,
            system_prompt=self._system_prompt,
            finish_reason=finish_reason,
        )

    def _resolve_max_tokens(self) -> int:
        """Resolve max_tokens by querying the model endpoint or using a fallback."""
        if self._resolved_max_tokens is not None:
            return self._resolved_max_tokens
        if self._max_tokens is not None:
            self._resolved_max_tokens = int(self._max_tokens)
            return self._resolved_max_tokens

        try:
            import httpx

            max_len = self._resolve_model_context_window(self._model)
            if isinstance(max_len, int) and max_len > 0:
                self._resolved_max_tokens = max_len
                return max_len
        except Exception:
            pass

        self._resolved_max_tokens = 65536
        return self._resolved_max_tokens

    @staticmethod
    def _estimate_prompt_tokens(messages: list[dict[str, str]]) -> int:
        """Rough token estimate for prompt sizing."""
        total_chars = sum(len(message.get("content", "")) for message in messages)
        return total_chars // 3

    def _cap_max_tokens(self, max_tokens: int, messages: list[dict[str, str]]) -> int:
        """Cap output tokens so prompt + completion stays within context."""
        max_model_len = getattr(self, "_max_model_len", None) or 0
        if max_model_len <= 0:
            return max_tokens
        estimated_prompt = self._estimate_prompt_tokens(messages)
        headroom = max_model_len - estimated_prompt
        if headroom < max_tokens:
            capped = max(headroom - 64, 1)
            logger.info(
                "Capping max_tokens from %d to %d (est. prompt=%d, model_len=%d)",
                max_tokens,
                capped,
                estimated_prompt,
                max_model_len,
            )
            return capped
        return max_tokens

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        """Return True when an API error is worth retrying."""
        try:
            from openai import (
                APIConnectionError,
                APIError,
                APIStatusError,
                APITimeoutError,
                RateLimitError,
            )

            if isinstance(exc, (RateLimitError, APITimeoutError, APIConnectionError)):
                return True
            if isinstance(exc, APIStatusError) and exc.status_code >= 500:
                return True
            if isinstance(exc, APIError):
                return True
        except ImportError:
            pass

        try:
            import httpx

            if isinstance(
                exc,
                (
                    httpx.RemoteProtocolError,
                    httpx.ReadError,
                    httpx.ReadTimeout,
                    httpx.ConnectError,
                    httpx.ConnectTimeout,
                ),
            ):
                return True
        except ImportError:
            pass

        message = str(exc).lower()
        return any(
            keyword in message
            for keyword in (
                "timeout",
                "rate",
                "429",
                "502",
                "503",
                "network connection lost",
                "incomplete chunked read",
                "peer closed connection",
                "remoteprotocolerror",
                "response payload is not completed",
            )
        )

    @staticmethod
    def _extract_reasoning_from_model_extra(obj: object) -> str:
        """Extract provider-specific reasoning strings from model_extra."""
        extra = getattr(obj, "model_extra", None)
        if not extra or not isinstance(extra, dict):
            return ""
        for key in ("reasoning_content", "reasoning"):
            value = extra.get(key)
            if value and isinstance(value, str):
                return value
        return ""

    def _call_streaming(self, request_kwargs: dict[str, Any]) -> tuple[str, str, dict[str, Any], str]:
        """Call the API in streaming mode."""
        kwargs = {**request_kwargs, "stream": True}
        if self._stream_options_supported:
            kwargs["stream_options"] = {"include_usage": True}

        try:
            stream = self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            if self._stream_options_supported and (
                "stream_options" in str(exc).lower()
                or getattr(exc, "status_code", 0) == 400
            ):
                logger.info("stream_options not supported by API, retrying without it")
                self._stream_options_supported = False
                kwargs.pop("stream_options", None)
                stream = self._client.chat.completions.create(**kwargs)
            else:
                raise

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        finish_reason = ""
        token_usage: dict[str, Any] = {}

        for chunk in stream:
            if chunk.choices:
                delta = chunk.choices[0].delta
                if delta:
                    if delta.content:
                        content_parts.append(delta.content)
                    reasoning = self._extract_reasoning_from_model_extra(delta)
                    if reasoning:
                        reasoning_parts.append(reasoning)
                if chunk.choices[0].finish_reason:
                    finish_reason = chunk.choices[0].finish_reason
            if hasattr(chunk, "usage") and chunk.usage:
                token_usage = {
                    "prompt_tokens": chunk.usage.prompt_tokens or 0,
                    "completion_tokens": chunk.usage.completion_tokens or 0,
                    "total_tokens": chunk.usage.total_tokens or 0,
                }

        raw_content = "".join(content_parts)
        raw_reasoning = "".join(reasoning_parts)
        if not raw_reasoning and "<think>" in raw_content:
            raw_reasoning, raw_content = _split_think_tags(raw_content)
        return raw_content, finish_reason, token_usage, raw_reasoning

    def _call_non_streaming(
        self,
        request_kwargs: dict[str, Any],
    ) -> tuple[str, str, dict[str, Any], str, dict[str, Any]]:
        """Call the API in non-streaming mode."""
        response = self._client.chat.completions.create(**request_kwargs)
        choice = response.choices[0]
        raw_output = choice.message.content or ""
        finish_reason = choice.finish_reason or ""
        token_usage: dict[str, Any] = {}
        if response.usage:
            token_usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }
        raw_reasoning = self._extract_reasoning_from_model_extra(choice.message)
        if not raw_reasoning and "<think>" in raw_output:
            raw_reasoning, raw_output = _split_think_tags(raw_output)
        response_json = {}
        model_dump = getattr(response, "model_dump", None)
        if callable(model_dump):
            response_json = model_dump()
        return raw_output, finish_reason, token_usage, raw_reasoning, response_json


def _split_think_tags(content: str) -> tuple[str, str]:
    """Split `<think>...</think>` content from the visible response text."""
    parts = _THINK_TAG_RE.findall(content)
    if not parts:
        return "", content
    reasoning = "\n".join(parts)
    cleaned = _THINK_TAG_RE.sub("", content).strip()
    return reasoning, cleaned


def _extract_patch(text: str) -> str:
    """Extract the most likely patch payload from model output."""
    stripped = text.strip()
    if not stripped:
        return ""

    try:
        payload = json.loads(stripped)
        if isinstance(payload, dict):
            for key in ("model_patch", "patch"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    except Exception:
        pass

    for match in _FENCED_BLOCK_RE.findall(stripped):
        candidate = match.strip()
        if candidate.startswith("diff --git") or ("\n--- " in candidate and "\n+++ " in candidate):
            return candidate

    diff_index = stripped.find("diff --git")
    if diff_index >= 0:
        return stripped[diff_index:].strip()

    triple_dash_index = stripped.find("--- ")
    if triple_dash_index >= 0 and "+++ " in stripped[triple_dash_index:]:
        return stripped[triple_dash_index:].strip()

    return stripped


def _detect_patch_format(patch: str) -> str:
    """Classify the returned patch payload for downstream observability."""
    if patch.startswith("diff --git"):
        return "git_diff"
    if patch.startswith("--- ") and "\n+++ " in patch:
        return "unified_diff"
    return "text"
