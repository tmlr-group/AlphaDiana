"""Shared helpers for terminal-bench-2 agents."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from alphadiana.benchmark.base import BenchmarkTask

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are an expert software engineer working in a Linux shell environment.\n"
    "You have access to a bash shell. Output shell commands on their own lines,\n"
    "prefixed with `$ `, and I will execute them and return the output.\n"
    "When the task is complete, output DONE on its own line.\n"
    "Do not use \\boxed{} format. This is a shell task."
)


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
        self._logs_base_dir = Path(config.get("logs_base_dir", "/tmp/tb2_logs"))

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

    def _start_container(self, docker_image: str, logs_dir: Path, task: BenchmarkTask) -> str:
        task_dir = task.metadata.get("task_dir", "")
        tests_host_dir = str(Path(task_dir) / "tests") if task_dir else ""

        cmd = ["docker", "run", "-d", "--rm", "-v", f"{logs_dir}:/logs"]
        if tests_host_dir and Path(tests_host_dir).is_dir():
            cmd += ["-v", f"{tests_host_dir}:/tests:ro"]
        cmd += [docker_image, "sleep", "infinity"]

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

    def _exec_command(self, container_id: str, cmd: str, timeout_sec: int | None = None) -> str:
        timeout = timeout_sec if timeout_sec is not None else self._timeout_sec
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

    def _read_reward(self, logs_dir: Path, task_id: str) -> str:
        reward_path = logs_dir / "verifier" / "reward.txt"
        if not reward_path.exists():
            logger.warning(
                "Task %s — reward.txt not found at %s "
                "(tests/test.sh may have failed to write it)",
                task_id,
                reward_path,
            )
            return "0"
        try:
            return reward_path.read_text(encoding="utf-8").strip()
        except Exception as exc:
            logger.warning("Task %s — failed to read reward.txt: %s", task_id, exc)
            return "0"

    def _stop_container(self, container_id: str, task_id: str) -> None:
        try:
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
