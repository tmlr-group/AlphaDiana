"""Shared in-container runtime helpers for terminal-bench-2 agents."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shlex
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from alphadiana.agent.terminal_bench2_common import (
    LocalCommandResult,
    TerminalBench2ContainerMixin,
    TerminalBench2RuntimeContext,
)
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.utils.rock_runtime import PREBUILT_SANDBOX_IMAGE

logger = logging.getLogger(__name__)

IN_CONTAINER_AGENT_PROMPT = """You are solving a terminal-bench-2 task from inside the task container.

Work directly on the live filesystem visible in the current shell.
Do not call `docker` directly. Do not stop, replace, or recreate the container.
Read `TASK.md` for the task details and `TASK_HINTS.md` too if it exists.
When your changes are complete, stop. The harness will run `/tests/test.sh` for you.
"""

_RUNTIME_IMAGE_REPO = os.environ.get("TB2_RUNTIME_IMAGE_REPO", "alphadiana-tb2-runtime")
_DEFAULT_RUNTIME_IMAGES = {
    "openclaw": os.environ.get("TB2_OPENCLAW_RUNTIME_IMAGE", PREBUILT_SANDBOX_IMAGE),
    "opencode": os.environ.get("TB2_OPENCODE_RUNTIME_IMAGE", "tmlrgroup/alphadiana:opencode"),
    "zeroclaw": os.environ.get("TB2_ZEROCLAW_RUNTIME_IMAGE", "zeroclaw-reasoning:0.6.9"),
}
_SAFE_FRAGMENT_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_fragment(value: str) -> str:
    fragment = _SAFE_FRAGMENT_RE.sub("-", str(value).strip()).strip("-")
    return fragment or "value"


class TerminalBench2InContainerMixin(TerminalBench2ContainerMixin):
    """Shared helpers for TB2 agents that run directly inside the task container."""

    def _resolve_runtime_source_image(
        self,
        config: dict,
        *,
        agent_type: str,
    ) -> str:
        configured = str(config.get("runtime_source_image", "") or "").strip()
        if configured:
            return configured
        return _DEFAULT_RUNTIME_IMAGES[agent_type]

    def _prepare_incontainer_runtime(
        self,
        task: BenchmarkTask,
        *,
        agent_type: str,
        runtime_source_image: str,
        temp_prefix: str,
    ) -> tuple[TerminalBench2RuntimeContext, dict[str, Any]]:
        base_image = self._resolve_docker_image(task)
        runtime_image, runtime_metadata = self._prepare_runtime_image(
            base_image,
            agent_type=agent_type,
            runtime_source_image=runtime_source_image,
        )
        logs_dir = self._logs_dir_for_task(task)
        container_id = self._start_container(runtime_image, logs_dir, task)
        tempdir = tempfile.TemporaryDirectory(prefix=temp_prefix)
        workdir = Path(tempdir.name)
        runtime = TerminalBench2RuntimeContext(
            task=task,
            docker_image=runtime_image,
            container_id=container_id,
            logs_dir=logs_dir,
            workdir=workdir,
            helper_paths={},
            _tempdir=tempdir,
        )
        return runtime, runtime_metadata

    def _prepare_runtime_image(
        self,
        base_image: str,
        *,
        agent_type: str,
        runtime_source_image: str,
    ) -> tuple[str, dict[str, Any]]:
        dockerfile = self._build_runtime_overlay_dockerfile(agent_type)
        fingerprint = hashlib.sha256(
            "\n".join([agent_type, base_image, runtime_source_image, dockerfile]).encode("utf-8")
        ).hexdigest()[:16]
        runtime_image = f"{_RUNTIME_IMAGE_REPO}:{agent_type}-{fingerprint}"
        runtime_image_built = False

        if not self._docker_image_exists(runtime_image):
            if not self._docker_image_exists(base_image):
                self._docker_pull(base_image)
            if not self._docker_image_exists(runtime_source_image):
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

    @staticmethod
    def _build_runtime_overlay_dockerfile(agent_type: str) -> str:
        if agent_type == "openclaw":
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
        if agent_type == "opencode":
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
        if agent_type == "zeroclaw":
            return """
ARG RUNTIME_IMAGE
ARG BASE_IMAGE
FROM ${RUNTIME_IMAGE} AS runtime
FROM ${BASE_IMAGE}
USER root
COPY --from=runtime /usr/local/bin/zeroclaw /usr/local/bin/zeroclaw
""".strip()
        raise RuntimeError(f"Unsupported TB2 runtime overlay agent_type: {agent_type!r}")

    @staticmethod
    def _docker_image_exists(image: str) -> bool:
        result = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0

    @staticmethod
    def _docker_pull(image: str) -> None:
        result = subprocess.run(
            ["docker", "pull", image],
            capture_output=True,
            text=True,
            timeout=1800,
        )
        if result.returncode != 0:
            raise RuntimeError(f"docker pull failed for {image}: {result.stderr.strip()}")

    @staticmethod
    def _docker_build_image(
        image: str,
        dockerfile: str,
        *,
        build_args: dict[str, str],
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="alphadiana-tb2-build-") as context_dir:
            cmd = ["docker", "build", "--tag", image]
            for key, value in sorted(build_args.items()):
                cmd.extend(["--build-arg", f"{key}={value}"])
            cmd.extend(["-f", "-", context_dir])
            result = subprocess.run(
                cmd,
                input=dockerfile,
                capture_output=True,
                text=True,
                timeout=1800,
            )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(f"docker build failed for {image}: {detail}")

    def _docker_exec_capture(
        self,
        container_id: str,
        script: str,
        *,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        timeout_sec: int,
        check: bool = False,
    ) -> LocalCommandResult:
        shell_lines: list[str] = []
        for key, value in sorted((env or {}).items()):
            shell_lines.append(f"export {key}={shlex.quote(value)}")
        if cwd:
            shell_lines.append(f"cd {shlex.quote(cwd)}")
        shell_lines.append(script)
        cmd = ["docker", "exec", container_id, "bash", "-lc", "\n".join(shell_lines)]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
        except subprocess.TimeoutExpired:
            return LocalCommandResult(stdout="", stderr=f"Timeout after {timeout_sec}s", returncode=-1)
        exec_result = LocalCommandResult(
            stdout=result.stdout,
            stderr=result.stderr,
            returncode=result.returncode,
        )
        if check and exec_result.returncode != 0:
            detail = exec_result.stderr.strip() or exec_result.stdout.strip() or "unknown error"
            raise RuntimeError(f"docker exec failed: {detail}")
        return exec_result

    def _docker_path_exists(self, container_id: str, path: str) -> bool:
        result = self._docker_exec_capture(
            container_id,
            f"test -e {shlex.quote(path)}",
            timeout_sec=10,
        )
        return result.returncode == 0

    def _stage_file_into_container(
        self,
        container_id: str,
        *,
        local_path: Path,
        remote_path: str,
    ) -> None:
        remote_parent = str(Path(remote_path).parent)
        self._docker_exec_capture(
            container_id,
            f"mkdir -p {shlex.quote(remote_parent)}",
            timeout_sec=30,
            check=True,
        )
        result = subprocess.run(
            ["docker", "cp", str(local_path), f"{container_id}:{remote_path}"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"docker cp to container failed: {result.stderr.strip()}")

    def _copy_file_from_container(
        self,
        container_id: str,
        *,
        remote_path: str,
        local_path: Path,
    ) -> bool:
        if not self._docker_path_exists(container_id, remote_path):
            return False
        local_path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["docker", "cp", f"{container_id}:{remote_path}", str(local_path)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"docker cp from container failed: {result.stderr.strip()}")
        return True

    def _read_container_text(
        self,
        container_id: str,
        remote_path: str,
        *,
        timeout_sec: int = 30,
    ) -> str:
        result = self._docker_exec_capture(
            container_id,
            f"test -f {shlex.quote(remote_path)} && cat {shlex.quote(remote_path)} || true",
            timeout_sec=timeout_sec,
        )
        return result.stdout

    def _build_remote_root(
        self,
        task: BenchmarkTask,
        *,
        agent_name: str,
    ) -> str:
        sample_index = task.metadata.get("sample_index", 0)
        execution_id = str(task.metadata.get("execution_id", "") or "") or str(int(time.time()))
        suffix = f"{task.task_id}-s{sample_index}-{_safe_fragment(execution_id[:16])}"
        return f"/tmp/alphadiana-tb2/{agent_name}/{suffix}"

    def _detect_container_workspace(self, container_id: str) -> str:
        pwd_result = self._docker_exec_capture(container_id, "pwd", timeout_sec=10)
        pwd = pwd_result.stdout.strip().splitlines()[-1] if pwd_result.stdout.strip() else ""
        has_app = self._docker_path_exists(container_id, "/app")
        if pwd in {"", "/", "/root", "/tmp"} and has_app:
            return "/app"
        if pwd:
            return pwd
        return "/app" if has_app else "/"

    def _build_incontainer_task_note(self, task: BenchmarkTask) -> str:
        task_dir_name = Path(str(task.metadata.get("task_dir", "") or "")).name
        if task.task_id != "tb2_db-wal-recovery" and task_dir_name != "db-wal-recovery":
            return ""
        return "\n".join([
            "Task-specific guidance:",
            '- If you inspect `/app/main.db` before recovery, prefer `sqlite3 "file:/app/main.db?mode=ro" ...`.',
            "- If you want a safe scratch copy, copy `/app/main.db` and `/app/main.db-wal` into `/tmp` first instead of mutating the live files during inspection.",
            "- Write the final answer to `/app/recovered.json` before stopping.",
        ])

    def _build_incontainer_prompt(self, task: BenchmarkTask) -> tuple[str, str]:
        task_note = self._build_incontainer_task_note(task)
        task_text = task.problem if not task_note else f"{task.problem.rstrip()}\n\n{task_note}\n"
        prompt_text = f"{IN_CONTAINER_AGENT_PROMPT}\n\n--- Task ---\n{task_text}\n"
        return task_text, prompt_text
