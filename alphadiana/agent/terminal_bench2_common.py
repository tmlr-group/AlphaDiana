"""Shared helpers for terminal-bench-2 agents."""

from __future__ import annotations

import logging
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.container_runtime.podman_cli import PodmanCLI, PodmanError
from alphadiana.container_runtime.proxy_env import podman_proxy_env

logger = logging.getLogger(__name__)
_SAFE_CONTAINER_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_PODMAN_START_TIMEOUT_SEC = int(os.environ.get("PODMAN_TB2_START_TIMEOUT_SEC", "180"))
_PODMAN_START_ATTEMPTS = int(os.environ.get("PODMAN_TB2_START_ATTEMPTS", "3"))


def _proxy_bypass_hosts_from_urls(*urls: str) -> list[str]:
    hosts: list[str] = []
    for raw_url in urls:
        value = str(raw_url or "").strip()
        if not value:
            continue
        parsed = urlsplit(value)
        host = parsed.hostname
        if host and host not in hosts:
            hosts.append(host)
    return hosts

SYSTEM_PROMPT = (
    "You are an expert software engineer working from a local control workspace "
    "against a running terminal-bench-2 Docker task.\n"
    "Use only the helper scripts in the current directory:\n"
    "- `./tb2-exec 'cmd'` runs `cmd` inside the target container.\n"
    "- `./tb2-copy-from <remote_path> <local_path>` copies files out of the container.\n"
    "- `./tb2-copy-to <local_path> <remote_path>` copies files into the container.\n"
    "- `./tb2-test` runs the benchmark verifier.\n"
    "Do not call docker directly. Output shell commands on their own lines, "
    "prefixed with `$ `, and I will execute them locally in this control workspace.\n"
    "When the task is complete, output DONE on its own line.\n"
    "Do not use \\boxed{} format. This is a shell task."
)

NATIVE_AGENT_PROMPT = """You are solving a terminal-bench-2 task against a running Docker container.

Use only the helper scripts in the current directory:
- `./tb2-exec 'cmd'` runs `cmd` inside the target container via `docker exec`.
- `./tb2-copy-from <remote_path> <local_path>` copies a file from the container.
- `./tb2-copy-to <local_path> <remote_path>` copies a file into the container.
- `./tb2-test` runs the benchmark verifier, but do not call it during normal solving.

Do not call docker directly. Do not stop or replace the target container.
Inspect `TASK.md` for the benchmark instruction.
Inspect `TASK_HINTS.md` as well if it exists.
After you have copied the final answer into the container, stop. The harness will run the verifier for you.
"""

AGENT_GUIDANCE = """# Terminal Bench 2 Workspace

This workspace controls a running terminal-bench-2 task container.

Rules:
- Use only `./tb2-exec`, `./tb2-copy-from`, and `./tb2-copy-to` while solving.
- `./tb2-test` is available only for manual debugging; do not call it during normal solving.
- Do not call `docker` directly.
- Do not stop, replace, or recreate the task container.
- Read `TASK.md` for the task details.
- Read `TASK_HINTS.md` too if it exists.
- After you copy the final output back into the container, stop and let the harness run the verifier.
"""


@dataclass
class TerminalBench2RuntimeContext:
    task: BenchmarkTask
    docker_image: str
    container_id: str
    logs_dir: Path
    workdir: Path
    helper_paths: dict[str, Path]
    _tempdir: tempfile.TemporaryDirectory[str] = field(repr=False)
    container_engine: str = "docker"

    def cleanup(self) -> None:
        self._tempdir.cleanup()


@dataclass
class LocalCommandResult:
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0


@dataclass
class VerifierResult:
    test_output: str
    reward: str | None
    status: str
    reward_path: str
    verifier_output_path: str


def parse_commands(text: str) -> list[str]:
    """Extract shell commands from LLM output."""
    commands: list[str] = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("$ "):
            cmd = stripped[2:].rstrip()
            if cmd:
                commands.append(cmd)
    return commands


def is_done(text: str) -> bool:
    """Return True if the output contains a standalone DONE line."""
    return any(line.strip() == "DONE" for line in text.splitlines())


class TerminalBench2ContainerMixin:
    """Container lifecycle helpers shared by terminal-bench-2 agents."""

    def _setup_container_config(self, config: dict) -> None:
        self._timeout_sec = int(config.get("timeout_sec", 300))
        self._test_timeout_sec = int(config.get("test_timeout_sec", 120))
        self._logs_base_dir = Path(config.get("logs_base_dir", "/tmp/tb2_logs")).expanduser().resolve()
        self._container_engine = str(config.get("container_engine", "docker") or "docker").strip().lower()
        if self._container_engine not in {"docker", "podman"}:
            raise ValueError(
                "terminal_bench2 agent.config.container_engine must be one of docker, podman"
            )
        injected_podman = config.get("podman_runtime")
        self._podman = injected_podman if injected_podman is not None else PodmanCLI()
        self._podman_proxy_host_alias = str(
            config.get("podman_proxy_host_alias", "host.containers.internal")
            or "host.containers.internal"
        ).strip()
        self._podman_network = str(
            config.get("podman_network", "slirp4netns:allow_host_loopback=true") or ""
        ).strip()
        forward_proxy_env = config.get("forward_proxy_env", config.get("podman_forward_proxy_env", True))
        if isinstance(forward_proxy_env, str):
            self._forward_proxy_env = forward_proxy_env.strip().lower() not in {
                "0",
                "false",
                "no",
                "off",
            }
        else:
            self._forward_proxy_env = bool(forward_proxy_env)

    def _setup_controller_config(
        self,
        config: dict,
        *,
        default_mode: str = "host",
        default_image: str = "",
    ) -> None:
        self._controller_mode = str(config.get("controller_mode", default_mode) or default_mode).strip()
        self._controller_image = str(config.get("controller_image", default_image) or default_image).strip()
        self._unsafe_network_host = bool(config.get("unsafe_network_host", False))
        explicit_network = str(config.get("controller_network", "") or "").strip()
        self._controller_network = explicit_network or ("host" if self._unsafe_network_host else "")
        self._mount_docker_socket = bool(
            config.get(
                "mount_docker_socket",
                config.get("controller_mount_docker_socket", False),
            )
        )
        if self._controller_network == "host":
            logger.warning(
                "terminal_bench2: unsafe_network_host=True; controller container has full host network access"
            )
        if self._mount_docker_socket:
            logger.warning(
                "terminal_bench2: mount_docker_socket=True; controller container can access the host Docker socket"
            )

    def _logs_dir_for_task(self, task: BenchmarkTask) -> Path:
        sample_index = task.metadata.get("sample_index", 0)
        execution_id = str(task.metadata.get("execution_id", "") or "")
        log_suffix = f"{task.task_id}_s{sample_index}"
        if execution_id:
            log_suffix += f"_{execution_id[:8]}"
        logs_dir = self._logs_base_dir / log_suffix
        logs_dir.mkdir(parents=True, exist_ok=True)
        return logs_dir

    def _cleanup_logs_dir(self, logs_dir: Path) -> None:
        shutil.rmtree(logs_dir, ignore_errors=True)

    def _resolve_docker_image(self, task: BenchmarkTask) -> str:
        docker_image = str(task.metadata.get("docker_image", "") or "").strip()
        if not docker_image:
            raise ValueError(
                f"Task {task.task_id} missing 'docker_image' in metadata. "
                "Ensure TerminalBench2Benchmark populated task.metadata correctly."
            )
        return docker_image

    def _start_container(self, docker_image: str, logs_dir: Path, task: BenchmarkTask) -> str:
        task_dir = task.metadata.get("task_dir", "")
        tests_host_dir = str(Path(task_dir) / "tests") if task_dir else ""

        volumes = [f"{logs_dir}:/logs"]
        if tests_host_dir and Path(tests_host_dir).is_dir():
            volumes.append(f"{tests_host_dir}:/tests:ro")

        if self._container_engine == "podman":
            logger.info("Task %s — starting Podman container: %s", task.task_id, docker_image)
            container_env: dict[str, str] = {}
            if self._forward_proxy_env:
                no_proxy_hosts = _proxy_bypass_hosts_from_urls(
                    str(getattr(self, "_api_base", "") or ""),
                    os.environ.get("OPENAI_BASE_URL", ""),
                    os.environ.get("OPENAI_API_BASE", ""),
                    os.environ.get("CUSTOM_API_BASE", ""),
                )
                container_env = podman_proxy_env(
                    os.environ,
                    host_alias=self._podman_proxy_host_alias,
                    no_proxy_hosts=no_proxy_hosts,
                )
            container_name = self._podman_container_name(task)
            container_id = ""
            last_error: PodmanError | None = None
            attempts = max(1, _PODMAN_START_ATTEMPTS)
            timeout = max(60, _PODMAN_START_TIMEOUT_SEC)
            for attempt in range(1, attempts + 1):
                try:
                    result = self._podman.run(
                        docker_image,
                        detach=True,
                        remove=True,
                        network=self._podman_network or None,
                        volumes=volumes,
                        env=container_env,
                        entrypoint="/bin/sh",
                        extra_args=["--http-proxy=false", "--name", container_name],
                        command=["-lc", "sleep infinity"],
                        timeout=timeout,
                    )
                    container_id = result.stdout.strip()
                    break
                except PodmanError as exc:
                    last_error = exc
                    container_id = self._find_podman_container_id(container_name)
                    if container_id:
                        logger.warning(
                            "Task %s — recovered Podman container %s after start error: %s",
                            task.task_id,
                            container_id[:12],
                            exc,
                        )
                        break
                    if attempt >= attempts:
                        break
                    logger.warning(
                        "Task %s — Podman container start failed on attempt %d/%d: %s",
                        task.task_id,
                        attempt,
                        attempts,
                        exc,
                    )
                    time.sleep(min(attempt, 5))
            if not container_id:
                if last_error is not None:
                    raise last_error
                raise RuntimeError(f"Podman container start produced no container ID for task {task.task_id}")
            self._podman.exec(
                container_id,
                ["mkdir", "-p", "/logs/verifier"],
                user="0:0",
                timeout=10,
                check=False,
            )
            return container_id

        cmd = [
            "docker",
            "run",
            "-d",
            "--rm",
            "--entrypoint",
            "/bin/sh",
            "-v",
            f"{logs_dir}:/logs",
        ]
        if tests_host_dir and Path(tests_host_dir).is_dir():
            cmd += ["-v", f"{tests_host_dir}:/tests:ro"]
        cmd += [docker_image, "-lc", "sleep infinity"]

        logger.info("Task %s — starting container: %s", task.task_id, docker_image)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(
                f"docker run failed for task {task.task_id} "
                f"(image: {docker_image}): {result.stderr.strip()}\n"
                f"Pre-pull the image with: docker pull {docker_image}"
            )
        container_id = result.stdout.strip()
        if not container_id:
            raise RuntimeError(f"docker run produced no container ID for task {task.task_id}")
        subprocess.run(
            ["docker", "exec", container_id, "mkdir", "-p", "/logs/verifier"],
            capture_output=True,
            timeout=10,
        )
        return container_id

    def _podman_container_name(self, task: BenchmarkTask) -> str:
        sample = str(task.metadata.get("sample_index", "0") or "0")
        execution_id = str(task.metadata.get("execution_id", "") or "")[:12]
        raw_name = f"alphadiana-{task.task_id}-s{sample}-{execution_id}"
        name = _SAFE_CONTAINER_NAME_RE.sub("-", raw_name).strip("-").lower()
        return name[:120] or "alphadiana-tb2-task"

    def _find_podman_container_id(self, container_name: str) -> str:
        try:
            result = self._podman.inspect(container_name, timeout=30, check=False)
        except PodmanError:
            return ""
        if not result.ok:
            return ""
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return ""
        if isinstance(payload, dict):
            records = [payload]
        elif isinstance(payload, list):
            records = [item for item in payload if isinstance(item, dict)]
        else:
            records = []
        for item in records:
            container_id = str(item.get("Id") or "").strip()
            state = item.get("State") if isinstance(item.get("State"), dict) else {}
            running = state.get("Running")
            status = str(state.get("Status") or "").strip().lower()
            if container_id and (running is True or status == "running"):
                return container_id
        return ""

    def _prepare_runtime(
        self,
        task: BenchmarkTask,
        *,
        temp_prefix: str = "tb2-runtime-",
        prompt_text: str = "",
        agent_guidance: str = AGENT_GUIDANCE,
    ) -> TerminalBench2RuntimeContext:
        docker_image = self._resolve_docker_image(task)
        logs_dir = self._logs_dir_for_task(task)
        container_id = self._start_container(docker_image, logs_dir, task)
        tempdir = tempfile.TemporaryDirectory(prefix=temp_prefix)
        workdir = Path(tempdir.name)
        self._bootstrap_workspace_files(workdir, container_id, task)
        helper_paths = self._write_helper_scripts(
            workdir,
            container_id,
            task,
            prompt_text=prompt_text,
            agent_guidance=agent_guidance,
            task_note=self._task_runtime_note(task, workdir),
        )
        return TerminalBench2RuntimeContext(
            task=task,
            docker_image=docker_image,
            container_id=container_id,
            logs_dir=logs_dir,
            workdir=workdir,
            helper_paths=helper_paths,
            container_engine=self._container_engine,
            _tempdir=tempdir,
        )

    def _cleanup_runtime(self, runtime: TerminalBench2RuntimeContext) -> None:
        try:
            self._stop_container(runtime.container_id, runtime.task.task_id)
        finally:
            try:
                runtime.cleanup()
            except Exception as exc:
                logger.warning(
                    "Task %s — failed to clean temp runtime %s: %s",
                    runtime.task.task_id,
                    runtime.workdir,
                    exc,
                )

    def _write_helper_scripts(
        self,
        workdir: Path,
        container_id: str,
        task: BenchmarkTask,
        *,
        prompt_text: str = "",
        agent_guidance: str = AGENT_GUIDANCE,
        task_note: str = "",
    ) -> dict[str, Path]:
        quoted_container = shlex.quote(container_id)
        container_engine = getattr(self, "_container_engine", "docker")
        runtime_pythonpath = shlex.quote(str(Path(__file__).resolve().parents[2]))
        runtime_python = shlex.quote(sys.executable or "python3")
        runtime_env = f"export PYTHONPATH={runtime_pythonpath}:\"${{PYTHONPATH:-}}\""
        if container_engine == "podman":
            exec_command = (
                f"{runtime_env}\n"
                f"exec {runtime_python} -m alphadiana.container_runtime.task_cli "
                f"exec {quoted_container} bash -lc \"$*\""
            )
            copy_from_command = (
                f"{runtime_env}\n"
                f"exec {runtime_python} -m alphadiana.container_runtime.task_cli "
                f"cp {quoted_container}:\"$1\" \"$2\""
            )
            copy_to_command = (
                f"{runtime_env}\n"
                f"exec {runtime_python} -m alphadiana.container_runtime.task_cli "
                f"cp \"$1\" {quoted_container}:\"$2\""
            )
            test_command = (
                f"{runtime_env}\n"
                f"exec {runtime_python} -m alphadiana.container_runtime.task_cli "
                f"exec {quoted_container} bash /tests/test.sh"
            )
        else:
            exec_command = f"docker exec {quoted_container} bash -lc \"$*\""
            copy_from_command = f"docker cp {quoted_container}:\"$1\" \"$2\""
            copy_to_command = f"docker cp \"$1\" {quoted_container}:\"$2\""
            test_command = f"docker exec {quoted_container} bash /tests/test.sh"
        helper_paths = {
            "task": workdir / "TASK.md",
            "prompt": workdir / "PROMPT.txt",
            "agents": workdir / "AGENTS.md",
            "task_hints": workdir / "TASK_HINTS.md",
            "exec": workdir / "tb2-exec",
            "copy_from": workdir / "tb2-copy-from",
            "copy_to": workdir / "tb2-copy-to",
            "test": workdir / "tb2-test",
        }
        task_text = task.problem
        if task_note:
            task_text = f"{task.problem.rstrip()}\n\n--- Task Hints ---\n{task_note.rstrip()}\n"
        helper_paths["task"].write_text(task_text, encoding="utf-8")
        if task_note:
            helper_paths["task_hints"].write_text(task_note.rstrip() + "\n", encoding="utf-8")
        helper_paths["exec"].write_text(
            "\n".join([
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                exec_command,
            ]),
            encoding="utf-8",
        )
        helper_paths["copy_from"].write_text(
            "\n".join([
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                copy_from_command,
            ]),
            encoding="utf-8",
        )
        helper_paths["copy_to"].write_text(
            "\n".join([
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                copy_to_command,
            ]),
            encoding="utf-8",
        )
        helper_paths["test"].write_text(
            "\n".join([
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                test_command,
            ]),
            encoding="utf-8",
        )
        for key in ("exec", "copy_from", "copy_to", "test"):
            os.chmod(helper_paths[key], 0o755)
        if prompt_text:
            helper_paths["prompt"].write_text(prompt_text, encoding="utf-8")
        if agent_guidance:
            helper_paths["agents"].write_text(agent_guidance, encoding="utf-8")
        return helper_paths

    def _disable_test_helper(self, helper_paths: dict[str, Path]) -> None:
        test_helper = helper_paths.get("test")
        if test_helper is None:
            return
        test_helper.write_text(
            "\n".join([
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                "echo 'tb2-test is disabled for native agents. Stop after copying outputs; the harness will run verification.'",
                "exit 0",
            ]),
            encoding="utf-8",
        )
        os.chmod(test_helper, 0o755)

    def _task_runtime_note(self, task: BenchmarkTask, workdir: Path | None = None) -> str:
        task_dir_name = Path(str(task.metadata.get("task_dir", "") or "")).name
        if task.task_id != "tb2_db-wal-recovery" and task_dir_name != "db-wal-recovery":
            return ""

        lines = [
            "Task-specific guidance:",
            "- Initial workspace copies of `/app/main.db` and `/app/main.db-wal` should be available under `./bootstrap/app/`.",
            "- Untouched backups should be available under `./bootstrap/original/`. If your working WAL copy disappears after local inspection, restore it from there instead of re-touching the live container.",
            "- Prefer working from the local copies first. Opening `/app/main.db` in default read-write mode can mutate or remove the live WAL sidecar before you recover it.",
            "- If you must inspect the live database before recovery, prefer `sqlite3 \"file:/app/main.db?mode=ro\" ...`.",
            "- Produce `recovered.json` in the workspace and copy it back with `./tb2-copy-to recovered.json /app/recovered.json`.",
        ]
        if workdir is not None:
            snapshot_lines: list[str] = []
            for prefix in ("app", "original"):
                snapshot_dir = workdir / "bootstrap" / prefix
                for name in ("main.db", "main.db-wal"):
                    path = snapshot_dir / name
                    if path.exists():
                        snapshot_lines.append(f"- Workspace snapshot ready: `./bootstrap/{prefix}/{name}`")
            if snapshot_lines:
                lines.extend(["", "Initial workspace state:"])
                lines.extend(snapshot_lines)
        return "\n".join(lines)

    def _bootstrap_workspace_files(
        self,
        workdir: Path,
        container_id: str,
        task: BenchmarkTask,
    ) -> None:
        task_dir_name = Path(str(task.metadata.get("task_dir", "") or "")).name
        if task.task_id != "tb2_db-wal-recovery" and task_dir_name != "db-wal-recovery":
            return

        snapshot_dir = workdir / "bootstrap" / "app"
        original_dir = workdir / "bootstrap" / "original"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        original_dir.mkdir(parents=True, exist_ok=True)
        for remote_path, local_name in (
            ("/app/main.db", "main.db"),
            ("/app/main.db-wal", "main.db-wal"),
        ):
            snapshot_path = snapshot_dir / local_name
            if getattr(self, "_container_engine", "docker") == "podman":
                result = self._podman.cp(
                    f"{container_id}:{remote_path}",
                    str(snapshot_path),
                    timeout=20,
                    check=False,
                )
            else:
                result = subprocess.run(
                    ["docker", "cp", f"{container_id}:{remote_path}", str(snapshot_path)],
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
            if result.returncode != 0:
                logger.warning(
                    "Task %s — failed to snapshot %s into workspace bootstrap: %s",
                    task.task_id,
                    remote_path,
                    result.stderr.strip(),
                )
                continue
            shutil.copy2(snapshot_path, original_dir / local_name)

    def _run_local_process(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_sec: int,
        progress_paths: list[Path] | None = None,
        idle_timeout_sec: int | None = None,
    ) -> LocalCommandResult:
        process: subprocess.Popen[str] | None = None
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                env=env,
                cwd=cwd,
                start_new_session=True,
            )
            if not progress_paths or idle_timeout_sec is None or idle_timeout_sec <= 0:
                stdout, stderr = process.communicate(timeout=timeout_sec)
                return LocalCommandResult(
                    stdout=stdout,
                    stderr=stderr,
                    returncode=int(process.returncode or 0),
                )
            start_time = time.monotonic()
            last_progress = start_time
            last_progress_mtime = self._latest_progress_mtime(progress_paths)
            while True:
                if process.poll() is not None:
                    stdout, stderr = process.communicate()
                    return LocalCommandResult(
                        stdout=stdout,
                        stderr=stderr,
                        returncode=int(process.returncode or 0),
                    )
                now = time.monotonic()
                if now - start_time > timeout_sec:
                    raise subprocess.TimeoutExpired(cmd, timeout_sec)
                current_progress_mtime = self._latest_progress_mtime(progress_paths)
                if (
                    current_progress_mtime is not None
                    and (
                        last_progress_mtime is None
                        or current_progress_mtime > last_progress_mtime + 1e-6
                    )
                ):
                    last_progress = now
                    last_progress_mtime = current_progress_mtime
                if now - last_progress > idle_timeout_sec:
                    raise subprocess.TimeoutExpired(
                        cmd,
                        timeout=idle_timeout_sec,
                    )
                time.sleep(1)
        except subprocess.TimeoutExpired:
            stdout = ""
            timeout_label = timeout_sec
            if idle_timeout_sec is not None and idle_timeout_sec > 0:
                timeout_label = idle_timeout_sec
            stderr = f"Timeout after {timeout_label}s"
            if process is not None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    stdout, timed_out_stderr = process.communicate(timeout=5)
                    stderr = timed_out_stderr or stderr
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    stdout, timed_out_stderr = process.communicate()
                    stderr = timed_out_stderr or stderr
                except ProcessLookupError:
                    stdout, timed_out_stderr = process.communicate()
                    stderr = timed_out_stderr or stderr
            return LocalCommandResult(stdout=stdout, stderr=stderr, returncode=-1)
        except OSError as exc:
            return LocalCommandResult(stdout="", stderr=str(exc), returncode=127)

    @staticmethod
    def _latest_progress_mtime(paths: list[Path]) -> float | None:
        latest: float | None = None
        for path in paths:
            if not path.exists():
                continue
            candidates: list[Path] = []
            if path.is_file():
                candidates.append(path)
            else:
                candidates.extend(p for p in path.rglob("*") if p.is_file())
            for candidate in candidates:
                try:
                    candidate_mtime = candidate.stat().st_mtime
                except OSError:
                    continue
                if latest is None or candidate_mtime > latest:
                    latest = candidate_mtime
        return latest

    def _run_controller_process(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_sec: int,
        progress_paths: list[Path] | None = None,
        idle_timeout_sec: int | None = None,
    ) -> LocalCommandResult:
        controller_mode = getattr(self, "_controller_mode", "host")
        if controller_mode not in {"docker", "podman"}:
            return self._run_local_process(
                cmd,
                cwd=cwd,
                env=env,
                timeout_sec=timeout_sec,
                progress_paths=progress_paths,
                idle_timeout_sec=idle_timeout_sec,
            )

        image = str(getattr(self, "_controller_image", "") or "").strip()
        if not image:
            return LocalCommandResult(
                stdout="",
                stderr=f"controller_mode={controller_mode} requires controller_image",
                returncode=2,
            )

        controller_env = dict(env)
        controller_username = str(os.environ.get("USER", "controller") or "controller").strip() or "controller"
        controller_home = cwd / ".controller-home"
        controller_cache = cwd / ".controller-cache"
        controller_tmp = cwd / ".controller-tmp"
        container_home = Path("/home") / controller_username
        controller_home.mkdir(parents=True, exist_ok=True)
        controller_cache.mkdir(parents=True, exist_ok=True)
        controller_tmp.mkdir(parents=True, exist_ok=True)
        controller_env.setdefault("HOME", str(container_home))
        controller_env.setdefault("USER", controller_username)
        controller_env.setdefault("LOGNAME", controller_username)
        controller_env.setdefault("XDG_CACHE_HOME", str(controller_cache))
        controller_env.setdefault("TMPDIR", str(controller_tmp))
        controller_env.setdefault("TMP", controller_env["TMPDIR"])
        controller_env.setdefault("TEMP", controller_env["TMPDIR"])

        network = str(getattr(self, "_controller_network", "") or "").strip()
        if controller_mode == "podman":
            extra_args = ["--init"]
            volumes = [f"{cwd}:{cwd}", f"{controller_home}:{container_home}"]
            try:
                result = self._podman.run(
                    image,
                    remove=True,
                    user=f"{os.getuid()}:{os.getgid()}",
                    network=network or None,
                    volumes=volumes,
                    workdir=str(cwd),
                    env=controller_env,
                    entrypoint="/bin/bash",
                    command=["-lc", shlex.join(cmd)],
                    extra_args=extra_args,
                    timeout=timeout_sec,
                )
                return LocalCommandResult(
                    stdout=result.stdout,
                    stderr=result.stderr,
                    returncode=result.returncode,
                )
            except PodmanError as exc:
                return LocalCommandResult(
                    stdout=exc.result.stdout,
                    stderr=exc.result.stderr or str(exc),
                    returncode=exc.result.returncode,
                )

        docker_cmd = ["docker", "run", "--rm", "--init"]
        docker_cmd.extend(["--user", f"{os.getuid()}:{os.getgid()}"])
        if network:
            docker_cmd.extend(["--network", network])
        if getattr(self, "_mount_docker_socket", False):
            try:
                socket_gid = os.stat("/var/run/docker.sock").st_gid
            except OSError:
                socket_gid = None
            if socket_gid is not None:
                docker_cmd.extend(["--group-add", str(socket_gid)])
            docker_cmd.extend(["-v", "/var/run/docker.sock:/var/run/docker.sock"])
        docker_cmd.extend(["-v", f"{cwd}:{cwd}", "-v", f"{controller_home}:{container_home}", "-w", str(cwd)])
        for key, value in sorted(controller_env.items()):
            docker_cmd.extend(["-e", f"{key}={value}"])
        docker_cmd.extend(["--entrypoint", "/bin/bash", image, "-lc", shlex.join(cmd)])

        host_env = os.environ.copy()
        return self._run_local_process(
            docker_cmd,
            cwd=cwd,
            env=host_env,
            timeout_sec=timeout_sec,
            progress_paths=progress_paths,
            idle_timeout_sec=idle_timeout_sec,
        )

    def _exec_local_command(
        self,
        workdir: Path,
        cmd: str,
        timeout_sec: int | None = None,
    ) -> str:
        timeout = timeout_sec if timeout_sec is not None else self._timeout_sec
        try:
            result = subprocess.run(
                ["bash", "-lc", cmd],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=workdir,
            )
            output = result.stdout
            if result.stderr:
                output += result.stderr
            return output.strip()
        except subprocess.TimeoutExpired:
            logger.warning("Local helper command timed out after %ds: %s", timeout, cmd[:100])
            return f"[TIMEOUT after {timeout}s]"
        except Exception as exc:
            logger.warning("Local helper command failed: %s", exc)
            return f"[ERROR: {exc}]"

    def _exec_command(self, container_id: str, cmd: str, timeout_sec: int | None = None) -> str:
        timeout = timeout_sec if timeout_sec is not None else self._timeout_sec
        if getattr(self, "_container_engine", "docker") == "podman":
            try:
                result = self._podman.exec(
                    container_id,
                    ["bash", "-c", cmd],
                    user="0:0",
                    timeout=timeout,
                    check=False,
                )
                output = result.stdout
                if result.stderr:
                    output += result.stderr
                return output.strip()
            except PodmanError as exc:
                return f"[ERROR: {exc}]"
        exec_cmd = ["docker", "exec", container_id, "bash", "-c", cmd]
        try:
            result = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=timeout)
            output = result.stdout
            if result.stderr:
                output += result.stderr
            return output.strip()
        except subprocess.TimeoutExpired:
            logger.warning("Command timed out after %ds: %s", timeout, cmd[:100])
            return f"[TIMEOUT after {timeout}s]"
        except Exception as exc:
            logger.warning("Command exec error: %s", exc)
            return f"[ERROR: {exc}]"

    def _run_tests(self, container_id: str, timeout_sec: int | None = None) -> str:
        timeout = timeout_sec if timeout_sec is not None else self._test_timeout_sec
        self._ensure_verifier_tooling(container_id)
        if getattr(self, "_container_engine", "docker") == "podman":
            try:
                result = self._podman.exec(
                    container_id,
                    ["bash", "/tests/test.sh"],
                    user="0:0",
                    timeout=timeout,
                    check=False,
                )
                output = result.stdout
                if result.stderr:
                    output += result.stderr
                return output.strip()
            except PodmanError as exc:
                if exc.result.returncode == 124:
                    logger.warning("tests/test.sh timed out after %ds", timeout)
                    return f"[TIMEOUT after {timeout}s]"
                logger.warning("tests/test.sh exec error: %s", exc)
                return f"[ERROR: {exc}]"
        exec_cmd = ["docker", "exec", container_id, "bash", "/tests/test.sh"]
        try:
            result = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=timeout)
            output = result.stdout
            if result.stderr:
                output += result.stderr
            return output.strip()
        except subprocess.TimeoutExpired:
            logger.warning("tests/test.sh timed out after %ds", timeout)
            return f"[TIMEOUT after {timeout}s]"
        except Exception as exc:
            logger.warning("tests/test.sh exec error: %s", exc)
            return f"[ERROR: {exc}]"

    def _ensure_verifier_tooling(self, container_id: str) -> None:
        bootstrap = r"""
mkdir -p /root/.local/bin /usr/local/bin
cat >/root/.local/bin/env <<'EOF'
export PATH="/usr/local/bin:/root/.local/bin:$PATH"
EOF
cat >/usr/local/bin/uvx <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

export PATH="/usr/local/bin:/root/.local/bin:$PATH"
packages=()
cmd=()
while (($#)); do
  case "$1" in
    -p)
      shift
      [ "$#" -gt 0 ] && shift || true
      ;;
    -w)
      shift
      [ "$#" -gt 0 ] || break
      packages+=("$1")
      shift
      ;;
    *)
      cmd=("$@")
      break
      ;;
  esac
done

if [ ${#cmd[@]} -eq 0 ]; then
  echo "uvx shim: missing command" >&2
  exit 2
fi

python3 -m ensurepip --upgrade >/dev/null 2>&1 || true
if [ ${#packages[@]} -gt 0 ]; then
  attempt=1
  while true; do
    if python3 -m pip install \
      --disable-pip-version-check \
      --user \
      --break-system-packages \
      --retries 5 \
      --timeout 120 \
      "${packages[@]}"; then
      break
    fi
    if [ "$attempt" -ge 3 ]; then
      exit 1
    fi
    attempt=$((attempt + 1))
    sleep 5
  done
fi

if [ "${cmd[0]}" = "pytest" ]; then
  exec python3 -m pytest "${cmd[@]:1}"
fi
exec "${cmd[@]}"
EOF
chmod +x /usr/local/bin/uvx /root/.local/bin/env
""".strip()
        try:
            if getattr(self, "_container_engine", "docker") == "podman":
                self._podman.exec(
                    container_id,
                    ["bash", "-lc", bootstrap],
                    user="0:0",
                    timeout=30,
                    check=False,
                )
                return
            subprocess.run(
                ["docker", "exec", container_id, "bash", "-lc", bootstrap],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except Exception as exc:
            logger.warning("Failed to pre-bootstrap verifier tooling in %s: %s", container_id[:12], exc)

    def _run_verifier_and_read_reward(
        self,
        runtime: TerminalBench2RuntimeContext,
        *,
        timeout_sec: int | None = None,
    ) -> VerifierResult:
        reward_path = self._verifier_reward_path(runtime.logs_dir)
        verifier_output_path = reward_path.parent
        existing_reward = self._read_reward_if_exists(runtime.logs_dir, runtime.task.task_id)
        if existing_reward is not None:
            return VerifierResult(
                test_output="[reused existing verifier reward.txt]",
                reward=existing_reward,
                status="ok",
                reward_path=str(reward_path),
                verifier_output_path=str(verifier_output_path),
            )

        running_verifier, probe_status = self._probe_running_verifier(runtime.container_id)
        if running_verifier:
            wait_timeout = timeout_sec if timeout_sec is not None else self._test_timeout_sec
            reused_output, reused_reward, reused_status = self._wait_for_existing_verifier(
                runtime,
                timeout_sec=wait_timeout,
            )
            if reused_reward is not None:
                return VerifierResult(
                    test_output=reused_output,
                    reward=reused_reward,
                    status="ok",
                    reward_path=str(reward_path),
                    verifier_output_path=str(verifier_output_path),
                )
            if reused_status == "skipped_duplicate":
                return VerifierResult(
                    test_output=reused_output,
                    reward=None,
                    status="skipped_duplicate",
                    reward_path=str(reward_path),
                    verifier_output_path=str(verifier_output_path),
                )

        test_output = self._run_tests(runtime.container_id, timeout_sec)
        reward = self._read_reward_if_exists(runtime.logs_dir, runtime.task.task_id)
        if reward is not None:
            status = "ok"
        elif "[TIMEOUT" in test_output:
            reward = "0"
            status = "timeout"
        elif probe_status == "probe_error":
            reward = "0"
            status = "probe_error"
        else:
            reward = "0"
            status = "missing_reward"
        return VerifierResult(
            test_output=test_output,
            reward=reward,
            status=status,
            reward_path=str(reward_path),
            verifier_output_path=str(verifier_output_path),
        )

    @staticmethod
    def _collect_text_artifacts(files: dict[str, Path]) -> dict[str, str]:
        artifacts: dict[str, str] = {}
        for name, path in files.items():
            if not path.exists() or path.is_dir():
                continue
            artifacts[name] = path.read_text(encoding="utf-8", errors="replace")
        return artifacts

    def _build_metadata(
        self,
        runtime: TerminalBench2RuntimeContext,
        *,
        reward: str | None,
        rounds_used: int = 0,
        runner: str,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        metadata = {
            "docker_image": runtime.docker_image,
            "container_engine": runtime.container_engine,
            "container_id": runtime.container_id,
            "category": runtime.task.metadata.get("category", ""),
            "difficulty": runtime.task.metadata.get("difficulty", ""),
            "reward": reward,
            "rounds_used": rounds_used,
            "runner": runner,
            "reward_path": str(self._verifier_reward_path(runtime.logs_dir)),
            "verifier_output_path": str(runtime.logs_dir / "verifier"),
            "test_timeout_sec": self._test_timeout_sec,
        }
        if runtime.container_engine == "podman":
            metadata["sandbox_backend"] = "podman"
        if extra:
            metadata.update(extra)
        return metadata

    @staticmethod
    def _verifier_reward_path(logs_dir: Path) -> Path:
        return logs_dir / "verifier" / "reward.txt"

    def _read_reward_if_exists(self, logs_dir: Path, task_id: str) -> str | None:
        reward_path = self._verifier_reward_path(logs_dir)
        if not reward_path.exists():
            return None
        try:
            return reward_path.read_text(encoding="utf-8").strip()
        except Exception as exc:
            logger.warning("Task %s — failed to read reward.txt: %s", task_id, exc)
            return None

    def _probe_running_verifier(self, container_id: str) -> tuple[bool, str]:
        probe = r"""
me="$$"
for pid in /proc/[0-9]*; do
  [ "${pid##*/}" = "$me" ] && continue
  [ -r "$pid/cmdline" ] || continue
  cmd="$(tr '\0' ' ' < "$pid/cmdline" 2>/dev/null || true)"
  case "$cmd" in
    *"/tests/test.sh"*) exit 0 ;;
  esac
done
exit 1
""".strip()
        try:
            if getattr(self, "_container_engine", "docker") == "podman":
                result = self._podman.exec(
                    container_id,
                    ["bash", "-lc", probe],
                    user="0:0",
                    timeout=10,
                    check=False,
                )
            else:
                result = subprocess.run(
                    ["docker", "exec", container_id, "bash", "-lc", probe],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
        except subprocess.TimeoutExpired:
            logger.warning("Verifier probe timed out for container %s", container_id[:12])
            return False, "probe_error"
        except Exception as exc:
            logger.warning("Verifier probe failed for container %s: %s", container_id[:12], exc)
            return False, "probe_error"
        if result.returncode == 0:
            return True, "ok"
        if result.returncode == 1:
            return False, "ok"
        logger.warning(
            "Verifier probe exited with %d for container %s: %s",
            result.returncode,
            container_id[:12],
            result.stderr.strip(),
        )
        return False, "probe_error"

    def _has_running_verifier(self, container_id: str) -> bool:
        running, _ = self._probe_running_verifier(container_id)
        return running

    def _wait_for_existing_verifier(
        self,
        runtime: TerminalBench2RuntimeContext,
        *,
        timeout_sec: int,
    ) -> tuple[str, str | None, str]:
        deadline = time.time() + max(timeout_sec, 1)
        final_status = "ok"
        while time.time() < deadline:
            reward = self._read_reward_if_exists(runtime.logs_dir, runtime.task.task_id)
            if reward is not None:
                return "[reused reward.txt from existing verifier]", reward, "ok"
            running, probe_status = self._probe_running_verifier(runtime.container_id)
            final_status = probe_status
            if not running:
                break
            time.sleep(1)

        reward = self._read_reward_if_exists(runtime.logs_dir, runtime.task.task_id)
        if reward is not None:
            return "[reused reward.txt from existing verifier after wait]", reward, "ok"
        running, probe_status = self._probe_running_verifier(runtime.container_id)
        final_status = probe_status
        if running:
            logger.warning(
                "Task %s — existing verifier still running after %ds; "
                "skipping duplicate verifier run",
                runtime.task.task_id,
                timeout_sec,
            )
            return (
                f"[skipped duplicate verifier run after waiting {timeout_sec}s "
                "for an existing verifier]",
                None,
                "skipped_duplicate",
            )
        return "", None, final_status

    def _stop_container(self, container_id: str, task_id: str) -> None:
        try:
            if getattr(self, "_container_engine", "docker") == "podman":
                result = self._podman.stop(container_id, stop_timeout=10, timeout=120, check=False)
                if not result.ok:
                    self._podman.rm(container_id, force=True, timeout=120, check=False)
                return
            subprocess.run(
                ["docker", "stop", container_id],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except Exception as exc:
            logger.warning(
                "Task %s — failed to stop container %s: %s",
                task_id,
                container_id[:12],
                exc,
            )
