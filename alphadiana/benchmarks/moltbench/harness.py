"""MoltBench Docker agent: runs agents inside GPU Docker containers.

Manages the full container lifecycle:
  1. Write task config to a temp directory
  2. docker run with GPU access and task mount
  3. Wait for container exit
  4. docker cp to extract all artifacts
  5. docker rm to clean up
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from alphadiana.harness.base import Agent, AgentResponse
from alphadiana.harness.registry import register_agent
from alphadiana.benchmarks.base import BenchmarkTask
from alphadiana.benchmarks.moltbench.utils import (
    parse_results_json,
    parse_trajectory,
)

logger = logging.getLogger(__name__)
_CONTAINER_ENGINES = {"docker", "podman"}
_DEFAULT_PODMAN_GPU_ARGS = ["--device", "nvidia.com/gpu=all"]


def _run(cmd: list[str], timeout: int | None = None, **kwargs) -> subprocess.CompletedProcess:
    """Run a command and return the result."""
    logger.debug("Running: %s", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kwargs)


def _prepare_task_payload(task: BenchmarkTask, task_dir: str) -> dict[str, Any]:
    """Materialize task.json and optional offline source files for the container."""
    task_json = dict(task.metadata.get("task_json", {}))
    if task.problem and "prompt" not in task_json:
        task_json["prompt"] = task.problem

    source_code = task.metadata.get("source_code")
    if source_code and _is_kernelbench_setup_cmd(task_json.get("setup_cmd")):
        source_path = Path(task_dir) / "problem.py"
        source_path.write_text(str(source_code), encoding="utf-8")

        level = str(task_json.get("level", 0))
        problem_id = str(task_json.get("problem_id", 0))
        backend = str(task_json.get("backend", "triton"))
        task_json["source"] = "file"
        task_json["file_path"] = "/workspace/task/problem.py"
        task_json["setup_cmd"] = [
            "python3",
            "kernelbench/bridge.py",
            "setup",
            "--level",
            level,
            "--problem",
            problem_id,
            "--backend",
            backend,
            "--source",
            "file",
            "--file-path",
            "/workspace/task/problem.py",
        ]

    return task_json


def _parse_results_or_error(artifacts_dir: str | Path) -> dict[str, Any]:
    try:
        return parse_results_json(artifacts_dir)
    except Exception as exc:
        logger.warning("Failed to parse MoltBench results from %s: %s", artifacts_dir, exc)
        return {"correctness": "MISSING", "speedup": 0.0, "error": str(exc)}


def _is_kernelbench_setup_cmd(cmd: Any) -> bool:
    """Return True when setup_cmd is the standard bridge.py setup invocation."""
    return (
        isinstance(cmd, list)
        and len(cmd) >= 3
        and cmd[:3] == ["python3", "kernelbench/bridge.py", "setup"]
    )


@register_agent("moltbench_docker")
class MoltBenchDockerAgent(Agent):
    """Agent that runs optimization inside a GPU Docker container.

    The agent (Claude Code, OpenClaw, etc.) runs inside the container
    and is unaware of the Docker layer. AlphaDiana only manages the
    container lifecycle and collects artifacts.

    Config keys:
        agent_type: str — "claude-code" or "openclaw" (determines Docker image)
        image_prefix: str — Docker image prefix (default: "tmlrgroup/alphadiana")
        image: str — full image name override (optional, overrides agent_type + image_prefix)
        request_timeout: int — max seconds to wait for container (default: 3600)
        gpus: str — GPU allocation (default: "all")
        network_mode: str | None — Docker network mode, e.g. "host" (optional)
        env: dict — extra environment variables to pass to container
        output_dir: str — local directory for artifacts (default: "./moltbench_artifacts")
        container_engine: str — "docker" (default) or "podman"
        container_engine_binary: str | None — explicit Docker/Podman executable (optional)
        podman_gpu_args: list[str] — Podman GPU flags (default: CDI nvidia all)
        extra_volumes: list[str] — additional -v mount specs
        shm_size: str | None — optional --shm-size value
        ipc_mode: str | None — container IPC namespace mode (optional)
        container_runtime: str | None — OCI runtime override, e.g. "runc" (optional)
    """

    name = "moltbench_docker"

    def __init__(self) -> None:
        self._agent_type: str = "claude-code"
        self._image: str = ""
        self._timeout: int = 3600
        self._gpus: str = "all"
        self._network_mode: str | None = None
        self._mount_claude_auth: bool = True
        self._env: dict[str, str] = {}
        self._output_dir: str = "./moltbench_artifacts"
        self._container_engine: str = "docker"
        self._container_engine_binary: str = "docker"
        self._podman_gpu_args: list[str] = list(_DEFAULT_PODMAN_GPU_ARGS)
        self._extra_volumes: list[str] = []
        self._shm_size: str | None = None
        self._ipc_mode: str | None = None
        self._container_runtime: str | None = None

    def setup(self, config: dict) -> None:
        self._agent_type = config.get("agent_type", "claude-code")
        image_prefix = config.get("image_prefix", "tmlrgroup/alphadiana")
        self._image = config.get("image", f"{image_prefix}:{self._agent_type}")
        self._timeout = int(config.get("request_timeout", 3600))
        self._gpus = config.get("gpus", "all")
        self._network_mode = config.get("network_mode")
        self._mount_claude_auth = bool(config.get("mount_claude_auth", True))
        self._output_dir = config.get("output_dir", "./moltbench_artifacts")
        self._container_engine = str(config.get("container_engine", "docker") or "docker").strip().lower()
        if self._container_engine not in _CONTAINER_ENGINES:
            raise ValueError(
                "moltbench_docker agent.config.container_engine must be one of "
                f"{sorted(_CONTAINER_ENGINES)}"
            )
        self._container_engine_binary = str(
            config.get("container_engine_binary", self._container_engine)
            or self._container_engine
        ).strip()
        self._podman_gpu_args = self._coerce_args_list(
            config.get("podman_gpu_args", _DEFAULT_PODMAN_GPU_ARGS),
            key="podman_gpu_args",
        )
        self._extra_volumes = self._coerce_args_list(config.get("extra_volumes", []), key="extra_volumes")
        self._shm_size = config.get("shm_size")
        self._ipc_mode = str(config.get("ipc_mode", "") or "").strip() or None
        self._container_runtime = str(config.get("container_runtime", "") or "").strip() or None
        if self._shm_size and self._ipc_mode == "host":
            raise ValueError(
                "moltbench_docker agent.config.shm_size cannot be combined with ipc_mode=host"
            )
        if self._container_engine == "podman" and self._shm_size and not self._ipc_mode:
            # Podman may inherit ipcns=host from containers.conf, which conflicts
            # with --shm-size. Make the requested shared-memory size deterministic.
            self._ipc_mode = "private"

        # Collect environment variables to pass into container
        self._env = dict(config.get("env", {}))

        # Auto-detect common API keys and proxy settings from host environment.
        for key in (
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_BASE_URL",
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "OPENAI_MODEL_NAME",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
            "no_proxy",
        ):
            if (key not in self._env or not self._env[key]) and os.environ.get(key):
                self._env[key] = os.environ[key]

        logger.info(
            "MoltBenchDockerAgent configured: image=%s, agent_type=%s, engine=%s, "
            "engine_binary=%s, timeout=%ds",
            self._image, self._agent_type, self._container_engine,
            self._container_engine_binary, self._timeout,
        )

    def solve(self, task: BenchmarkTask, sandbox: Optional[Any] = None) -> AgentResponse:
        start_time = time.monotonic()

        # 1. Prepare task directory
        task_dir = tempfile.mkdtemp(prefix=f"moltbench_{task.task_id}_")
        task_json = _prepare_task_payload(task, task_dir)
        task_json_path = Path(task_dir) / "task.json"
        task_json_path.write_text(json.dumps(task_json, indent=2), encoding="utf-8")

        # Local artifacts directory
        sample_index = self._sample_index(task)
        execution_id = str(task.metadata.get("execution_id") or uuid4().hex)
        artifacts_local = (
            Path(self._output_dir)
            / task.task_id
            / f"sample_{sample_index}"
            / execution_id
        )
        artifacts_local.mkdir(parents=True, exist_ok=False)

        container_id = ""
        try:
            # 2. Start container
            container_id = self._container_run(task_dir)
            logger.info("Task %s: container %s started", task.task_id, container_id[:12])

            # 3. Wait for container to finish
            exit_code = self._container_wait(container_id)
            wall_time = time.monotonic() - start_time
            logger.info(
                "Task %s: container exited with code %d after %.1fs",
                task.task_id, exit_code, wall_time,
            )

            # 4. Extract artifacts
            self._container_cp(container_id, "/workspace/artifacts/.", str(artifacts_local))

            # Also grab container logs
            logs_result = _run([self._container_engine_binary, "logs", container_id], timeout=30)
            (artifacts_local / "container_stdout.log").write_text(
                logs_result.stdout, encoding="utf-8",
            )
            (artifacts_local / "container_stderr.log").write_text(
                logs_result.stderr, encoding="utf-8",
            )
            exit_benign = (
                exit_code != 0
                and self._agent_type == "claude-code"
                and "Reached max turns" in (logs_result.stdout or "")
            )

            # 5. Parse results
            results = _parse_results_or_error(artifacts_local)
            trajectory = parse_trajectory(artifacts_local)

            return AgentResponse(
                answer=results,
                trajectory=trajectory,
                raw_output=logs_result.stdout[-10000:] if logs_result.stdout else "",
                wall_time_sec=wall_time,
                metadata={
                    "artifacts_dir": str(artifacts_local),
                    "container_id": container_id,
                    "exit_code": exit_code,
                    "exit_benign": exit_benign,
                    "sample_index": sample_index,
                    "execution_id": execution_id,
                    "agent_type": self._agent_type,
                    "image": self._image,
                    "container_engine": self._container_engine,
                    "container_engine_binary": self._container_engine_binary,
                },
            )

        except subprocess.TimeoutExpired:
            wall_time = time.monotonic() - start_time
            logger.warning("Task %s: container timed out after %.1fs", task.task_id, wall_time)

            # Try to extract partial artifacts
            if container_id:
                _run([self._container_engine_binary, "stop", "-t", "10", container_id], timeout=30)
                self._container_cp(container_id, "/workspace/artifacts/.", str(artifacts_local))

            results = _parse_results_or_error(artifacts_local)
            return AgentResponse(
                answer=results,
                wall_time_sec=wall_time,
                metadata={
                    "artifacts_dir": str(artifacts_local),
                    "container_id": container_id,
                    "timeout": True,
                    "sample_index": sample_index,
                    "execution_id": execution_id,
                    "agent_type": self._agent_type,
                    "container_engine": self._container_engine,
                    "container_engine_binary": self._container_engine_binary,
                },
            )

        finally:
            # 6. Cleanup
            if container_id:
                _run([self._container_engine_binary, "rm", "-f", container_id], timeout=30)
            shutil.rmtree(task_dir, ignore_errors=True)

    def _container_run(self, task_dir: str) -> str:
        """Start a task container and return its ID."""
        cmd = self._build_run_command(task_dir)
        result = _run(cmd, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(
                f"{self._container_engine} run failed (exit {result.returncode}): {result.stderr}"
            )
        return result.stdout.strip()

    def _build_run_command(self, task_dir: str) -> list[str]:
        """Build the container run command without executing it."""
        cmd = [
            self._container_engine_binary, "run", "-d",
        ]
        if self._container_runtime:
            cmd.extend(["--runtime", self._container_runtime])
        if self._container_engine == "podman":
            cmd.extend(self._podman_gpu_args)
        else:
            cmd.extend(["--gpus", self._gpus])
        cmd.extend([
            "-v", f"{task_dir}:/workspace/task:ro",
        ])
        if self._shm_size:
            cmd.extend(["--shm-size", str(self._shm_size)])
        if self._ipc_mode:
            cmd.extend(["--ipc", self._ipc_mode])
        for volume in self._extra_volumes:
            cmd.extend(["-v", volume])
        if self._agent_type == "claude-code" and self._mount_claude_auth:
            claude_dir = Path.home() / ".claude"
            claude_json = Path.home() / ".claude.json"
            if claude_dir.exists():
                cmd.extend(["-v", f"{claude_dir}:/root/.claude:ro"])
            if claude_json.exists():
                cmd.extend(["-v", f"{claude_json}:/root/.claude.json:ro"])
        if self._network_mode:
            cmd.extend(["--network", self._network_mode])

        # Environment variables
        env = {**self._env, "MOLTBENCH_AGENT": self._agent_type}
        for key, val in env.items():
            cmd.extend(["-e", f"{key}={val}"])

        cmd.append(self._image)
        return cmd

    def _container_wait(self, container_id: str) -> int:
        """Wait for a container to exit. Returns exit code."""
        result = _run(
            [self._container_engine_binary, "wait", container_id],
            timeout=self._timeout + 60,  # extra buffer
        )
        if result.returncode != 0:
            raise RuntimeError(f"{self._container_engine} wait failed: {result.stderr}")
        return int(result.stdout.strip())

    def _container_cp(self, container_id: str, src: str, dst: str) -> None:
        """Copy files from container to local filesystem."""
        result = _run(
            [self._container_engine_binary, "cp", f"{container_id}:{src}", dst],
            timeout=120,
        )
        if result.returncode != 0:
            logger.warning("%s cp failed: %s", self._container_engine, result.stderr)

    @staticmethod
    def _coerce_args_list(value: Any, *, key: str) -> list[str]:
        if value in (None, ""):
            return []
        if isinstance(value, str):
            return shlex.split(value)
        if isinstance(value, (list, tuple)):
            return [str(item) for item in value]
        raise ValueError(f"moltbench_docker agent.config.{key} must be a list or string")

    @staticmethod
    def _sample_index(task: BenchmarkTask) -> int:
        """Return the runtime sample index for this execution."""
        raw_sample_index = task.metadata.get("sample_index", 0)
        try:
            return max(0, int(raw_sample_index))
        except (TypeError, ValueError):
            return 0
