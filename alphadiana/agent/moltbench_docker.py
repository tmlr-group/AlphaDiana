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

from alphadiana.agent.base import Agent, AgentResponse
from alphadiana.agent.registry import register_agent
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.benchmark.moltbench_utils import (
    parse_results_json,
    parse_trajectory,
)

logger = logging.getLogger(__name__)


def _run(cmd: list[str], timeout: int | None = None, **kwargs) -> subprocess.CompletedProcess:
    """Run a command and return the result."""
    logger.debug("Running: %s", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kwargs)


@register_agent("moltbench_docker")
class MoltBenchDockerAgent(Agent):
    """Agent that runs optimization inside a GPU Docker container.

    The agent (Claude Code, OpenClaw, etc.) runs inside the container
    and is unaware of the Docker layer. AlphaDiana only manages the
    container lifecycle and collects artifacts.

    Config keys:
        agent_type: str — "claude-code" or "openclaw" (determines Docker image)
        image_prefix: str — Docker image prefix (default: "moltbench")
        image: str — full image name override (optional, overrides agent_type + image_prefix)
        request_timeout: int — max seconds to wait for container (default: 3600)
        gpus: str — GPU allocation (default: "all")
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

    def setup(self, config: dict) -> None:
        self._agent_type = config.get("agent_type", "claude-code")
        image_prefix = config.get("image_prefix", "moltbench")
        self._image = config.get("image", f"{image_prefix}/{self._agent_type}")
        self._timeout = int(config.get("request_timeout", 3600))
        self._gpus = config.get("gpus", "all")
        self._network_mode = config.get("network_mode")
        self._mount_claude_auth = bool(config.get("mount_claude_auth", True))
        self._output_dir = config.get("output_dir", "./moltbench_artifacts")

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
            "MoltBenchDockerAgent configured: image=%s, agent_type=%s, timeout=%ds",
            self._image, self._agent_type, self._timeout,
        )

    def solve(self, task: BenchmarkTask, sandbox: Optional[Any] = None) -> AgentResponse:
        start_time = time.monotonic()

        # 1. Prepare task directory
        task_dir = tempfile.mkdtemp(prefix=f"moltbench_{task.task_id}_")
        task_json = dict(task.metadata.get("task_json", {}))
        if task.problem and "prompt" not in task_json:
            task_json["prompt"] = task.problem
        task_json_path = Path(task_dir) / "task.json"
        task_json_path.write_text(json.dumps(task_json, indent=2), encoding="utf-8")

        # Local artifacts directory
        artifacts_local = Path(self._output_dir) / task.task_id
        artifacts_local.mkdir(parents=True, exist_ok=True)

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
            logs_result = _run(["docker", "logs", container_id], timeout=30)
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
            results = parse_results_json(artifacts_local)
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
                    "agent_type": self._agent_type,
                    "image": self._image,
                },
            )

        except subprocess.TimeoutExpired:
            wall_time = time.monotonic() - start_time
            logger.warning("Task %s: container timed out after %.1fs", task.task_id, wall_time)

            # Try to extract partial artifacts
            if container_id:
                _run(["docker", "stop", "-t", "10", container_id], timeout=30)
                self._docker_cp(container_id, "/workspace/artifacts/.", str(artifacts_local))

            results = parse_results_json(artifacts_local)
            return AgentResponse(
                answer=results,
                wall_time_sec=wall_time,
                metadata={
                    "artifacts_dir": str(artifacts_local),
                    "container_id": container_id,
                    "timeout": True,
                    "agent_type": self._agent_type,
                },
            )

        finally:
            # 6. Cleanup
            if container_id:
                _run(["docker", "rm", "-f", container_id], timeout=30)
            shutil.rmtree(task_dir, ignore_errors=True)

    def _docker_run(self, task_dir: str) -> str:
        """Start a Docker container and return its ID."""
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
        result = _run(
            ["docker", "wait", container_id],
            timeout=self._timeout + 60,  # extra buffer
        )
        if result.returncode != 0:
            raise RuntimeError(f"docker wait failed: {result.stderr}")
        return int(result.stdout.strip())

    def _docker_cp(self, container_id: str, src: str, dst: str) -> None:
        """Copy files from container to local filesystem."""
        result = _run(
            ["docker", "cp", f"{container_id}:{src}", dst],
            timeout=120,
        )
        if result.returncode != 0:
            logger.warning("docker cp failed: %s", result.stderr)
