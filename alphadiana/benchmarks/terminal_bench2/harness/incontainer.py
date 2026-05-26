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

from alphadiana.benchmarks.terminal_bench2.harness.common import (
    LocalCommandResult,
    TerminalBench2ContainerMixin,
    TerminalBench2RuntimeContext,
)
from alphadiana.benchmarks.base import BenchmarkTask
from alphadiana.engine.container_runtime.podman_cli import PodmanError, normalize_podman_image_ref
from alphadiana.utils.rock_runtime import PREBUILT_SANDBOX_IMAGE

logger = logging.getLogger(__name__)

IN_CONTAINER_AGENT_PROMPT = """You are solving a terminal-bench-2 task from inside the task container.

Work directly on the live filesystem visible in the current shell.
Do not call `docker` directly. Do not stop, replace, or recreate the container.
The full task instruction is included below in this prompt.
If companion files such as `TASK.md` or `TASK_HINTS.md` are present, you may read them, but do not assume they exist under `/app`.
When your changes are complete, stop. The harness will run `/tests/test.sh` for you.
"""

_RUNTIME_IMAGE_REPO = os.environ.get("TB2_RUNTIME_IMAGE_REPO", "alphadiana-tb2-runtime")
_DEFAULT_RUNTIME_IMAGES = {
    "openclaw": os.environ.get("TB2_OPENCLAW_RUNTIME_IMAGE", PREBUILT_SANDBOX_IMAGE),
    "opencode": os.environ.get("TB2_OPENCODE_RUNTIME_IMAGE", "tmlrgroup/alphadiana:opencode"),
    "zeroclaw": os.environ.get("TB2_ZEROCLAW_RUNTIME_IMAGE", "zeroclaw-reasoning:0.6.9"),
}
_SAFE_FRAGMENT_RE = re.compile(r"[^A-Za-z0-9._-]+")
_PODMAN_IMAGE_INSPECT_TIMEOUT_SEC = int(os.environ.get("PODMAN_TB2_IMAGE_INSPECT_TIMEOUT_SEC", "180"))
_PODMAN_IMAGE_INSPECT_ATTEMPTS = int(os.environ.get("PODMAN_TB2_IMAGE_INSPECT_ATTEMPTS", "3"))
_PODMAN_BUILD_ATTEMPTS = int(os.environ.get("PODMAN_TB2_BUILD_ATTEMPTS", "2"))
_PODMAN_EXEC_ATTEMPTS = int(os.environ.get("PODMAN_TB2_EXEC_ATTEMPTS", "3"))


def _safe_fragment(value: str) -> str:
    fragment = _SAFE_FRAGMENT_RE.sub("-", str(value).strip()).strip("-")
    return fragment or "value"


def _is_transient_podman_exec_error(result: LocalCommandResult) -> bool:
    text = f"{result.stderr}\n{result.stdout}".lower()
    return (
        "no such file or directory" in text
        and "overlay-containers" in text
        and "/userdata/" in text
        and "/exit/" in text
    )


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
        runtime_image, runtime_metadata = self._prepare_runtime_image(
            task,
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
            container_engine=getattr(self, "_container_engine", "docker"),
            _tempdir=tempdir,
        )
        return runtime, runtime_metadata

    def _prepare_runtime_image(
        self,
        task: BenchmarkTask,
        *,
        agent_type: str,
        runtime_source_image: str,
    ) -> tuple[str, dict[str, Any]]:
        base_image = self._resolve_docker_image(task)
        dockerfile = self._build_runtime_overlay_dockerfile(agent_type)
        fingerprint = hashlib.sha256(
            "\n".join([agent_type, base_image, runtime_source_image, dockerfile]).encode("utf-8")
        ).hexdigest()[:16]
        runtime_image = f"{_RUNTIME_IMAGE_REPO}:{agent_type}-{fingerprint}"
        runtime_image_built = False
        base_image_built_locally = False

        if not self._docker_image_exists(runtime_image):
            if not self._docker_image_exists(base_image):
                if self._is_local_task_image(base_image):
                    self._docker_build_local_task_image(task, base_image)
                    base_image_built_locally = True
                else:
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
            "base_image_built_locally": base_image_built_locally,
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
    def _is_local_task_image(image: str) -> bool:
        return str(image or "").strip().endswith(":local")

    def _docker_build_local_task_image(self, task: BenchmarkTask, image: str) -> None:
        task_dir = Path(str(task.metadata.get("task_dir", "") or "").strip())
        if not task_dir:
            raise RuntimeError(
                f"Task {task.task_id} declares local image {image} but is missing task_dir metadata"
            )

        env_dir = task_dir / "environment"
        dockerfile_path = env_dir / "Dockerfile"
        if not dockerfile_path.exists():
            raise RuntimeError(
                f"Task {task.task_id} declares local image {image} but {dockerfile_path} is missing"
            )

        build_timeout_sec = int(float(task.metadata.get("build_timeout_sec", 600.0) or 600.0))
        if getattr(self, "_container_engine", "docker") == "podman":
            self._podman.build(
                str(env_dir),
                tag=image,
                file=str(dockerfile_path),
                timeout=build_timeout_sec,
            )
            return
        result = subprocess.run(
            ["docker", "build", "--tag", image, "-f", str(dockerfile_path), str(env_dir)],
            capture_output=True,
            text=True,
            timeout=build_timeout_sec,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(f"docker build failed for local task image {image}: {detail}")

    def _docker_image_exists(self, image: str) -> bool:
        if getattr(self, "_container_engine", "docker") == "podman":
            last_error: PodmanError | None = None
            attempts = max(1, _PODMAN_IMAGE_INSPECT_ATTEMPTS)
            timeout = max(30, _PODMAN_IMAGE_INSPECT_TIMEOUT_SEC)
            for attempt in range(1, attempts + 1):
                try:
                    return self._podman.image_exists(image, timeout=timeout)
                except PodmanError as exc:
                    last_error = exc
                    if attempt >= attempts:
                        break
                    logger.warning(
                        "Podman image inspect for %s failed on attempt %d/%d: %s",
                        image,
                        attempt,
                        attempts,
                        exc,
                    )
                    time.sleep(min(attempt, 5))
            if last_error is not None:
                raise last_error
            return False
        result = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0

    def _docker_pull(self, image: str) -> None:
        if getattr(self, "_container_engine", "docker") == "podman":
            self._podman.pull(image, timeout=1800)
            return
        result = subprocess.run(
            ["docker", "pull", image],
            capture_output=True,
            text=True,
            timeout=1800,
        )
        if result.returncode != 0:
            raise RuntimeError(f"docker pull failed for {image}: {result.stderr.strip()}")

    def _docker_build_image(
        self,
        image: str,
        dockerfile: str,
        *,
        build_args: dict[str, str],
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="alphadiana-tb2-build-") as context_dir:
            if getattr(self, "_container_engine", "docker") == "podman":
                extra_args: list[str] = []
                for key, value in sorted(build_args.items()):
                    if key.upper().endswith("IMAGE"):
                        value = normalize_podman_image_ref(value)
                    extra_args.extend(["--build-arg", f"{key}={value}"])
                last_error: PodmanError | None = None
                attempts = max(1, _PODMAN_BUILD_ATTEMPTS)
                for attempt in range(1, attempts + 1):
                    try:
                        self._podman.build(
                            context_dir,
                            tag=image,
                            file="-",
                            input_text=dockerfile,
                            extra_args=extra_args,
                            timeout=1800,
                        )
                        return
                    except PodmanError as exc:
                        last_error = exc
                        if attempt >= attempts:
                            break
                        logger.warning(
                            "Podman build for %s failed on attempt %d/%d: %s",
                            image,
                            attempt,
                            attempts,
                            exc,
                        )
                        time.sleep(min(attempt, 5))
                if last_error is not None:
                    raise last_error
                return
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
        if getattr(self, "_container_engine", "docker") == "podman":
            attempts = max(1, _PODMAN_EXEC_ATTEMPTS)
            exec_result = LocalCommandResult(stdout="", stderr="", returncode=1)
            for attempt in range(1, attempts + 1):
                try:
                    result = self._podman.exec(
                        container_id,
                        ["bash", "-lc", "\n".join(shell_lines)],
                        user="0:0",
                        timeout=timeout_sec,
                        check=False,
                    )
                    exec_result = LocalCommandResult(
                        stdout=result.stdout,
                        stderr=result.stderr,
                        returncode=result.returncode,
                    )
                except PodmanError as exc:
                    exec_result = LocalCommandResult(
                        stdout=exc.result.stdout,
                        stderr=exc.result.stderr or str(exc),
                        returncode=exc.result.returncode,
                    )
                if not _is_transient_podman_exec_error(exec_result) or attempt >= attempts:
                    break
                logger.warning(
                    "Podman exec hit transient control-plane failure for container %s "
                    "on attempt %d/%d",
                    container_id,
                    attempt,
                    attempts,
                )
                time.sleep(min(attempt, 5))
            if check and exec_result.returncode != 0:
                detail = exec_result.stderr.strip() or exec_result.stdout.strip() or "unknown error"
                raise RuntimeError(f"Podman exec failed: {detail}")
            return exec_result
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
        if getattr(self, "_container_engine", "docker") == "podman":
            result = self._podman.cp(
                str(local_path),
                f"{container_id}:{remote_path}",
                timeout=120,
                check=False,
            )
        else:
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
        local_path.parent.mkdir(parents=True, exist_ok=True)
        attempts = max(1, int(os.environ.get("PODMAN_TB2_COPY_ATTEMPTS", "3")))
        last_error = ""
        for attempt in range(1, attempts + 1):
            if getattr(self, "_container_engine", "docker") == "podman":
                result = self._podman.cp(
                    f"{container_id}:{remote_path}",
                    str(local_path),
                    timeout=120,
                    check=False,
                )
            else:
                result = subprocess.run(
                    ["docker", "cp", f"{container_id}:{remote_path}", str(local_path)],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
            if result.returncode == 0 and local_path.exists():
                return True
            last_error = result.stderr.strip() or result.stdout.strip()

            fallback = self._docker_exec_capture(
                container_id,
                f"cat {shlex.quote(remote_path)}",
                timeout_sec=120,
            )
            if fallback.returncode == 0:
                local_path.write_text(fallback.stdout, encoding="utf-8", errors="replace")
                return True
            last_error = fallback.stderr.strip() or fallback.stdout.strip() or last_error
            if attempt < attempts:
                time.sleep(1)

        if getattr(self, "_container_engine", "docker") != "podman":
            raise RuntimeError(f"docker cp from container failed: {last_error}")
        return False

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
