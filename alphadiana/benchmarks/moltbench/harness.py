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
from alphadiana.engine.container_runtime.gpu import nvidia_cdi_args
from alphadiana.engine.container_runtime.podman_cli import PodmanCLI, PodmanError

logger = logging.getLogger(__name__)


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
        container_engine: str — "docker" (default) or "podman". Podman injects GPUs
            via NVIDIA CDI (--device nvidia.com/gpu=...) instead of --gpus.
        network_mode: str | None — Docker network mode, e.g. "host" (optional)
        env: dict — extra environment variables to pass to container
        output_dir: str — local directory for artifacts (default: "./moltbench_artifacts")
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
        self._podman: PodmanCLI | None = None
        self._volume_mounts: list[str] = []
        self._memory: str | None = None
        self._gpu_mode: str = "cdi"
        self._nvidia_runtime: str = "/usr/bin/nvidia-container-runtime"

    def setup(self, config: dict) -> None:
        self._agent_type = config.get("agent_type", "claude-code")
        image_prefix = config.get("image_prefix", "tmlrgroup/alphadiana")
        self._image = config.get("image", f"{image_prefix}:{self._agent_type}")
        self._timeout = int(config.get("request_timeout", 3600))
        self._gpus = config.get("gpus", "all")
        self._network_mode = config.get("network_mode")
        self._mount_claude_auth = bool(config.get("mount_claude_auth", True))
        self._output_dir = config.get("output_dir", "./moltbench_artifacts")
        # Extra read-only/data mounts (e.g. model dir, conda env, CUDA toolkit) as
        # "host:container[:opts]" specs, and an optional memory cap (e.g. "64g").
        self._volume_mounts = [str(v) for v in (config.get("volume_mounts") or [])]
        self._memory = config.get("memory")
        # Podman GPU injection: "cdi" (--device nvidia.com/gpu=N, needs podman>=4.1)
        # or "nvidia-runtime" (--runtime nvidia-container-runtime + NVIDIA_VISIBLE_DEVICES,
        # works on podman 3.4.x). Docker path always uses --gpus.
        self._gpu_mode = str(config.get("gpu_mode", "cdi") or "cdi").strip().lower()
        self._nvidia_runtime = str(
            config.get("nvidia_runtime_path", "/usr/bin/nvidia-container-runtime")
        )

        self._container_engine = str(
            config.get("container_engine", "docker") or "docker"
        ).strip().lower()
        if self._container_engine not in {"docker", "podman"}:
            raise ValueError(
                "moltbench_docker agent.config.container_engine must be one of "
                "docker, podman"
            )
        self._podman = PodmanCLI() if self._container_engine == "podman" else None

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
            "MoltBenchDockerAgent configured: image=%s, agent_type=%s, timeout=%ds, engine=%s",
            self._image, self._agent_type, self._timeout, self._container_engine,
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
            container_id = self._docker_run(task_dir)
            logger.info("Task %s: container %s started", task.task_id, container_id[:12])

            # 3. Wait for container to finish
            exit_code = self._docker_wait(container_id)
            wall_time = time.monotonic() - start_time
            logger.info(
                "Task %s: container exited with code %d after %.1fs",
                task.task_id, exit_code, wall_time,
            )

            # 4. Extract artifacts
            self._docker_cp(container_id, "/workspace/artifacts/.", str(artifacts_local))

            # Also grab container logs
            logs_stdout, logs_stderr = self._container_logs(container_id)
            (artifacts_local / "container_stdout.log").write_text(
                logs_stdout, encoding="utf-8",
            )
            (artifacts_local / "container_stderr.log").write_text(
                logs_stderr, encoding="utf-8",
            )
            exit_benign = (
                exit_code != 0
                and self._agent_type == "claude-code"
                and "Reached max turns" in (logs_stdout or "")
            )

            # 5. Parse results
            results = parse_results_json(artifacts_local)
            trajectory = parse_trajectory(artifacts_local)

            return AgentResponse(
                answer=results,
                trajectory=trajectory,
                raw_output=logs_stdout[-10000:] if logs_stdout else "",
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
                },
            )

        except subprocess.TimeoutExpired:
            wall_time = time.monotonic() - start_time
            logger.warning("Task %s: container timed out after %.1fs", task.task_id, wall_time)

            # Try to extract partial artifacts
            if container_id:
                self._container_stop(container_id)
                self._docker_cp(container_id, "/workspace/artifacts/.", str(artifacts_local))

            results = parse_results_json(artifacts_local)
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
                },
            )

        finally:
            # 6. Cleanup
            if container_id:
                self._container_rm(container_id)
            shutil.rmtree(task_dir, ignore_errors=True)

    def _docker_run(self, task_dir: str) -> str:
        """Start a container and return its ID."""
        if self._container_engine == "podman":
            return self._podman_run(task_dir)
        # NOTE: intentionally NO --rm. Artifacts and logs are extracted via `docker cp` /
        # `docker logs` AFTER the container exits (see solve()), which requires the stopped
        # container to persist; --rm would delete it on exit and lose the artifact suitcase.
        # Cleanup is guaranteed by the finally: _container_rm() block instead.
        cmd = [
            "docker", "run", "-d",
            "--gpus", self._gpus,
            "-v", f"{task_dir}:/workspace/task:ro",
        ]
        if self._agent_type == "claude-code" and self._mount_claude_auth:
            claude_dir = Path.home() / ".claude"
            claude_json = Path.home() / ".claude.json"
            if claude_dir.exists():
                cmd.extend(["-v", f"{claude_dir}:/root/.claude:ro"])
            if claude_json.exists():
                cmd.extend(["-v", f"{claude_json}:/root/.claude.json:ro"])
        for spec in self._volume_mounts:
            cmd.extend(["-v", spec])
        if self._memory:
            cmd.extend(["--memory", str(self._memory)])
        if self._network_mode:
            cmd.extend(["--network", self._network_mode])

        # Environment variables
        env = {**self._env, "MOLTBENCH_AGENT": self._agent_type}
        for key, val in env.items():
            cmd.extend(["-e", f"{key}={val}"])

        cmd.append(self._image)

        result = _run(cmd, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(
                f"docker run failed (exit {result.returncode}): {result.stderr}"
            )
        return result.stdout.strip()

    def _docker_wait(self, container_id: str) -> int:
        """Wait for a container to exit. Returns exit code."""
        if self._container_engine == "podman":
            return self._podman_wait(container_id)
        result = _run(
            ["docker", "wait", container_id],
            timeout=self._timeout + 60,  # extra buffer
        )
        if result.returncode != 0:
            raise RuntimeError(f"docker wait failed: {result.stderr}")
        return int(result.stdout.strip())

    def _docker_cp(self, container_id: str, src: str, dst: str) -> None:
        """Copy files from container to local filesystem."""
        if self._container_engine == "podman":
            assert self._podman is not None
            self._podman.cp(f"{container_id}:{src}", dst, timeout=120, check=False)
            return
        result = _run(
            ["docker", "cp", f"{container_id}:{src}", dst],
            timeout=120,
        )
        if result.returncode != 0:
            logger.warning("docker cp failed: %s", result.stderr)

    # ── Podman lifecycle ──────────────────────────────────────────────────────

    def _cdi_devices(self) -> str | list[str]:
        """Map docker --gpus syntax to NVIDIA CDI device id(s).

        "all" -> "all"; "7" -> "7"; "device=7" -> "7"; "device=0,1" -> ["0", "1"].
        nvidia_cdi_args() adds the "nvidia.com/gpu=" prefix.
        """
        raw = str(self._gpus or "all").strip()
        if raw in ("", "all"):
            return "all"
        if raw.startswith("device="):
            raw = raw[len("device="):]
        ids = [d.strip() for d in raw.split(",") if d.strip()]
        return ids if len(ids) > 1 else ids[0]

    def _podman_run(self, task_dir: str) -> str:
        """Start a Podman container (GPU via CDI) and return its ID."""
        assert self._podman is not None
        volumes = [f"{task_dir}:/workspace/task:ro"]
        if self._agent_type == "claude-code" and self._mount_claude_auth:
            claude_dir = Path.home() / ".claude"
            claude_json = Path.home() / ".claude.json"
            if claude_dir.exists():
                volumes.append(f"{claude_dir}:/root/.claude:ro")
            if claude_json.exists():
                volumes.append(f"{claude_json}:/root/.claude.json:ro")
        volumes.extend(self._volume_mounts)

        env = {**self._env, "MOLTBENCH_AGENT": self._agent_type}
        if self._gpu_mode == "nvidia-runtime":
            # podman 3.4.x: GPU via the nvidia OCI runtime + NVIDIA_VISIBLE_DEVICES.
            dev = self._cdi_devices()
            env["NVIDIA_VISIBLE_DEVICES"] = ",".join(dev) if isinstance(dev, list) else str(dev)
            env.setdefault("NVIDIA_DRIVER_CAPABILITIES", "all")
            extra_args = ["--runtime", self._nvidia_runtime, "--security-opt", "label=disable"]
        else:
            extra_args = nvidia_cdi_args(self._cdi_devices())  # podman >=4.1 CDI
        if self._memory:
            extra_args = [*extra_args, "--memory", str(self._memory)]
        try:
            result = self._podman.run(
                self._image,
                detach=True,
                env=env,
                volumes=volumes,
                network=self._network_mode or None,
                extra_args=extra_args,
                timeout=60,
            )
        except PodmanError as exc:
            raise RuntimeError(
                f"podman run failed (exit {exc.result.returncode}): {exc.result.stderr}"
            ) from exc
        return result.stdout.strip()

    def _podman_wait(self, container_id: str) -> int:
        """Wait for a Podman container to exit. Returns exit code."""
        assert self._podman is not None
        try:
            result = self._podman.wait(container_id, timeout=self._timeout + 60)
        except PodmanError as exc:
            if exc.result.returncode == 124:
                # Re-raise as TimeoutExpired so solve()'s timeout handler runs.
                raise subprocess.TimeoutExpired(
                    cmd="podman wait", timeout=self._timeout + 60
                ) from exc
            raise RuntimeError(f"podman wait failed: {exc.result.stderr}") from exc
        return int(result.stdout.strip())

    # ── Engine-agnostic helpers ───────────────────────────────────────────────

    def _container_logs(self, container_id: str) -> tuple[str, str]:
        """Return (stdout, stderr) of container logs for either engine."""
        if self._container_engine == "podman":
            assert self._podman is not None
            result = self._podman.logs(container_id, timeout=30)
            return result.stdout, result.stderr
        result = _run(["docker", "logs", container_id], timeout=30)
        return result.stdout, result.stderr

    def _container_stop(self, container_id: str) -> None:
        """Stop a container (best effort) for either engine."""
        if self._container_engine == "podman":
            assert self._podman is not None
            self._podman.stop(container_id, stop_timeout=10, timeout=30)
            return
        _run(["docker", "stop", "-t", "10", container_id], timeout=30)

    def _container_rm(self, container_id: str) -> None:
        """Force-remove a container (best effort) for either engine."""
        if self._container_engine == "podman":
            assert self._podman is not None
            self._podman.rm(container_id, force=True, timeout=30)
            return
        _run(["docker", "rm", "-f", container_id], timeout=30)

    @staticmethod
    def _sample_index(task: BenchmarkTask) -> int:
        """Return the runtime sample index for this execution."""
        raw_sample_index = task.metadata.get("sample_index", 0)
        try:
            return max(0, int(raw_sample_index))
        except (TypeError, ValueError):
            return 0
