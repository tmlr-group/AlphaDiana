"""MoltBench task-container agent.

Manages the full container lifecycle:
  1. Write task config to a temp directory
  2. run a GPU task container with task mount
  3. Wait for container exit
  4. copy out all artifacts
  5. clean up the task container

Supports both Docker and Podman container engines.
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
from alphadiana.engine.container_runtime import PodmanCLI, PodmanError, nvidia_cdi_args

logger = logging.getLogger(__name__)

_CONTAINER_ENGINES = {"docker", "podman"}
_PODMAN_GPU_MODES = {"auto", "cdi", "legacy_devices", "none"}
_PODMAN_CDI_UNSUPPORTED_MARKERS = (
    "stat nvidia.com/gpu=",
    "no such file or directory",
)
_NVIDIA_DRIVER_LIB_GLOBS = (
    "/usr/lib/x86_64-linux-gnu/libcuda.so*",
    "/usr/lib/x86_64-linux-gnu/libnvidia-ml.so*",
)
_NVIDIA_DRIVER_BINARIES = (
    "/usr/bin/nvidia-smi",
)


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


def _podman_devices_from_docker_gpus(value: Any) -> str | list[str] | None:
    """Translate common Docker ``--gpus`` values into Podman CDI selectors."""
    if value in (None, "", [], ()):
        return None
    if isinstance(value, (list, tuple, set)):
        return [str(part).strip() for part in value if str(part).strip()]

    text = str(value).strip()
    if not text:
        return None
    if text.startswith("device="):
        text = text.split("=", 1)[1].strip()
    if text.startswith("nvidia.com/gpu="):
        return text
    if "," in text and text != "all":
        return [part.strip() for part in text.split(",") if part.strip()]
    return text


def _legacy_nvidia_device_args(devices: str | list[str] | None) -> list[str]:
    """Return direct device mounts for Podman versions without CDI support."""
    if devices in (None, "", [], ()):
        return []
    device_values = [devices] if isinstance(devices, str) else list(devices)
    gpu_paths: list[str] = []
    if any(str(device).strip() == "all" for device in device_values):
        gpu_paths = sorted(str(path) for path in Path("/dev").glob("nvidia[0-9]*"))
    else:
        for raw_device in device_values:
            device = str(raw_device).strip()
            if not device:
                continue
            if device.startswith("nvidia.com/gpu="):
                device = device.split("=", 1)[1]
            if device.startswith("/dev/"):
                gpu_paths.append(device)
            elif device.isdigit():
                gpu_paths.append(f"/dev/nvidia{device}")

    support_paths = [
        "/dev/nvidiactl",
        "/dev/nvidia-uvm",
        "/dev/nvidia-uvm-tools",
    ]
    paths: list[str] = []
    for path in [*gpu_paths, *support_paths]:
        if path not in paths and Path(path).exists():
            paths.append(path)

    args: list[str] = []
    for path in paths:
        args.extend(["--device", path])
    return args


def _legacy_nvidia_driver_mounts() -> tuple[list[str], list[str]]:
    """Return read-only driver library mounts for Podman legacy GPU mode."""
    paths: list[Path] = []
    for pattern in _NVIDIA_DRIVER_LIB_GLOBS:
        pattern_path = Path(pattern)
        for path in sorted(pattern_path.parent.glob(pattern_path.name)):
            if path.exists() and path.is_file() and path not in paths:
                paths.append(path)
    for raw_path in _NVIDIA_DRIVER_BINARIES:
        path = Path(raw_path)
        if path.exists() and path.is_file() and path not in paths:
            paths.append(path)

    args: list[str] = []
    lib_dirs: list[str] = []
    for path in paths:
        path_text = str(path)
        args.extend(["-v", f"{path_text}:{path_text}:ro"])
        if path.name.startswith("lib"):
            parent = str(path.parent)
            if parent not in lib_dirs:
                lib_dirs.append(parent)
    return args, lib_dirs


def _prepend_path_entries(entries: list[str], existing: str | None) -> str:
    parts: list[str] = []
    for entry in entries:
        if entry and entry not in parts:
            parts.append(entry)
    if existing:
        for entry in existing.split(":"):
            if entry and entry not in parts:
                parts.append(entry)
    return ":".join(parts)


def _podman_error_looks_like_unsupported_cdi(exc: PodmanError) -> bool:
    text = f"{exc.result.stderr}\n{exc.result.stdout}".lower()
    return all(marker in text for marker in _PODMAN_CDI_UNSUPPORTED_MARKERS)


@register_agent("moltbench_docker")
class MoltBenchDockerAgent(Agent):
    """Agent that runs optimization inside a GPU task container.

    The agent (Claude Code, OpenClaw, etc.) runs inside the container
    and is unaware of the container layer. AlphaDiana only manages the
    container lifecycle and collects artifacts.

    Config keys:
        agent_type: str — "claude-code", "openclaw", "opencode", or "zeroclaw"
        image_prefix: str — image prefix (default: "tmlrgroup/alphadiana")
        image: str — full image name override (optional, overrides agent_type + image_prefix)
        container_engine: str — "docker" or "podman" (default: "docker")
        request_timeout: int — max seconds to wait for container (default: 3600)
        gpus: str — Docker GPU allocation or Podman fallback CDI selector (default: "all")
        podman_devices: str | list — Podman CDI devices, e.g. "all" or "0" (optional)
        podman_gpu_mode: str — "auto", "cdi", "legacy_devices", or "none" (default: "auto")
        podman_mount_driver_libs: bool — mount host NVIDIA driver libs in legacy mode (default: true)
        network_mode: str | None — container network mode, e.g. "host" (optional)
        mount_claude_auth: bool — mount ~/.claude into container for Claude Code (default: true)
        env: dict — extra environment variables to pass to container
        output_dir: str — local directory for artifacts (default: "./moltbench_artifacts")
    """

    name = "moltbench_docker"
    default_container_engine = "docker"

    def __init__(self) -> None:
        self._agent_type: str = "claude-code"
        self._image: str = ""
        self._container_engine: str = self.default_container_engine
        self._podman: PodmanCLI = PodmanCLI()
        self._timeout: int = 3600
        self._gpus: str = "all"
        self._podman_devices: str | list[str] | None = "all"
        self._podman_gpu_mode: str = "auto"
        self._resolved_podman_gpu_mode: str = ""
        self._podman_disable_selinux_label: bool = True
        self._podman_mount_driver_libs: bool = True
        self._podman_extra_args: list[str] = []
        self._network_mode: str | None = None
        self._mount_claude_auth: bool = True
        self._env: dict[str, str] = {}
        self._output_dir: str = "./moltbench_artifacts"

    def setup(self, config: dict) -> None:
        self._agent_type = config.get("agent_type", "claude-code")
        image_prefix = config.get("image_prefix", "tmlrgroup/alphadiana")
        self._image = config.get("image", f"{image_prefix}:{self._agent_type}")
        self._container_engine = str(
            config.get("container_engine", self.default_container_engine)
            or self.default_container_engine
        ).strip().lower()
        if self._container_engine not in _CONTAINER_ENGINES:
            raise ValueError(
                "moltbench_docker agent.config.container_engine must be one of "
                f"{', '.join(sorted(_CONTAINER_ENGINES))}"
            )
        injected_podman = config.get("podman_runtime")
        self._podman = injected_podman if injected_podman is not None else PodmanCLI()
        self._timeout = int(config.get("request_timeout", 3600))
        self._gpus = config.get("gpus", "all")
        if "podman_devices" in config:
            self._podman_devices = _podman_devices_from_docker_gpus(config.get("podman_devices"))
        else:
            self._podman_devices = _podman_devices_from_docker_gpus(self._gpus)
        self._podman_gpu_mode = str(config.get("podman_gpu_mode", "auto") or "auto").strip()
        if self._podman_gpu_mode not in _PODMAN_GPU_MODES:
            raise ValueError(
                "moltbench_docker agent.config.podman_gpu_mode must be one of "
                f"{', '.join(sorted(_PODMAN_GPU_MODES))}"
            )
        self._resolved_podman_gpu_mode = ""
        self._podman_disable_selinux_label = bool(
            config.get("podman_disable_selinux_label", True)
        )
        self._podman_mount_driver_libs = bool(config.get("podman_mount_driver_libs", True))
        self._podman_extra_args = [str(arg) for arg in config.get("podman_extra_args", [])]
        self._network_mode = config.get("network_mode")
        self._mount_claude_auth = bool(config.get("mount_claude_auth", True))
        self._output_dir = config.get("output_dir", "./moltbench_artifacts")

        # Collect environment variables to pass into container
        self._env = dict(config.get("env", {}))

        # Auto-detect common API keys and proxy settings from host environment.
        for key in (
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_BASE_URL",
            "ANTHROPIC_AUTH_TOKEN",
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
            "MoltBenchDockerAgent configured: engine=%s, image=%s, agent_type=%s, timeout=%ds",
            self._container_engine, self._image, self._agent_type, self._timeout,
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
            logs_result = self._container_logs(container_id)
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
                    **self._container_engine_metadata(),
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
            logs_result = subprocess.CompletedProcess([], 0, stdout="", stderr="")
            if container_id:
                self._container_stop(container_id)
                self._container_cp(container_id, "/workspace/artifacts/.", str(artifacts_local))
                logs_result = self._container_logs(container_id)
                (artifacts_local / "container_stdout.log").write_text(
                    logs_result.stdout, encoding="utf-8",
                )
                (artifacts_local / "container_stderr.log").write_text(
                    logs_result.stderr, encoding="utf-8",
                )

            try:
                results = parse_results_json(artifacts_local)
            except FileNotFoundError as exc:
                results = {
                    "correctness": "MISSING",
                    "speedup": 0.0,
                    "error": str(exc),
                }
            return AgentResponse(
                answer=results,
                raw_output=logs_result.stdout[-10000:] if logs_result.stdout else "",
                wall_time_sec=wall_time,
                metadata={
                    **self._container_engine_metadata(),
                    "artifacts_dir": str(artifacts_local),
                    "container_id": container_id,
                    "timeout": True,
                    "sample_index": sample_index,
                    "execution_id": execution_id,
                    "agent_type": self._agent_type,
                    "image": self._image,
                },
                finish_reason="timeout",
            )

        finally:
            # 6. Cleanup
            if container_id:
                self._container_rm(container_id)
            shutil.rmtree(task_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Container lifecycle — engine-agnostic interface
    # ------------------------------------------------------------------

    def _container_run(self, task_dir: str) -> str:
        """Start a task container and return its ID."""
        volumes = [f"{task_dir}:/workspace/task:ro"]
        if self._agent_type == "claude-code" and self._mount_claude_auth:
            claude_dir = Path.home() / ".claude"
            claude_json = Path.home() / ".claude.json"
            if claude_dir.exists():
                volumes.append(f"{claude_dir}:/root/.claude:ro")
            if claude_json.exists():
                volumes.append(f"{claude_json}:/root/.claude.json:ro")

        # Environment variables
        env = {**self._env, "MOLTBENCH_AGENT": self._agent_type}

        if self._container_engine == "podman":
            return self._podman_run(volumes, env)

        return self._docker_run(volumes, env)

    def _docker_run(self, volumes: list[str], env: dict[str, str]) -> str:
        """Start a container using Docker."""
        cmd = [
            "docker", "run", "-d",
            "--gpus", self._gpus,
        ]
        for volume in volumes:
            cmd.extend(["-v", volume])
        if self._network_mode:
            cmd.extend(["--network", self._network_mode])
        for key, val in env.items():
            cmd.extend(["-e", f"{key}={val}"])
        cmd.append(self._image)

        result = _run(cmd, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(
                f"docker run failed (exit {result.returncode}): {result.stderr}"
            )
        return result.stdout.strip()

    def _podman_run(self, volumes: list[str], env: dict[str, str]) -> str:
        """Start a container using Podman with GPU support."""
        attempted_gpu_mode = self._initial_podman_gpu_mode()
        extra_args, podman_env = self._podman_runtime_args_and_env(attempted_gpu_mode, env)
        try:
            result = self._podman.run(
                self._image,
                detach=True,
                env=podman_env,
                volumes=volumes,
                network=self._network_mode,
                extra_args=[*extra_args, *self._podman_extra_args],
                timeout=60,
            )
            self._resolved_podman_gpu_mode = attempted_gpu_mode
        except PodmanError as exc:
            if self._podman_gpu_mode != "auto" or not _podman_error_looks_like_unsupported_cdi(exc):
                raise
            logger.warning(
                "Podman CDI GPU args are not supported by this runtime; "
                "retrying MoltBench container with direct /dev/nvidia* mounts"
            )
            extra_args, podman_env = self._podman_runtime_args_and_env(
                "legacy_devices",
                env,
            )
            result = self._podman.run(
                self._image,
                detach=True,
                env=podman_env,
                volumes=volumes,
                network=self._network_mode,
                extra_args=[*extra_args, *self._podman_extra_args],
                timeout=60,
            )
            self._resolved_podman_gpu_mode = "legacy_devices"
        container_id = result.stdout.strip()
        if not container_id:
            raise RuntimeError(f"podman run returned an empty container id for {self._image}")
        return container_id

    def _container_wait(self, container_id: str) -> int:
        """Wait for a container to exit. Returns exit code."""
        if self._container_engine == "podman":
            try:
                result = self._podman.wait(
                    container_id,
                    timeout=self._timeout + 60,
                )
            except PodmanError as exc:
                if exc.result.returncode == 124:
                    raise subprocess.TimeoutExpired(
                        list(exc.result.redacted_argv),
                        self._timeout + 60,
                        output=exc.result.stdout,
                        stderr=exc.result.stderr,
                    ) from exc
                raise
            return int(result.stdout.strip())

        result = _run(
            ["docker", "wait", container_id],
            timeout=self._timeout + 60,
        )
        if result.returncode != 0:
            raise RuntimeError(f"docker wait failed: {result.stderr}")
        return int(result.stdout.strip())

    def _container_cp(self, container_id: str, src: str, dst: str) -> None:
        """Copy files from container to local filesystem.

        Podman cp does not support the trailing-dot convention that Docker
        uses to copy directory *contents* into a destination.  Instead we
        copy the directory itself and then move its contents up one level.
        """
        if self._container_engine == "podman":
            # Strip trailing /. — podman doesn't support this Docker convention
            podman_src = src.rstrip("/")
            if podman_src.endswith("."):
                podman_src = podman_src[:-1].rstrip("/")
            Path(dst).mkdir(parents=True, exist_ok=True)
            result = self._podman.cp(
                f"{container_id}:{podman_src}",
                dst,
                timeout=120,
                check=False,
            )
            if result.returncode != 0:
                logger.warning("podman cp failed: %s", result.stderr)
                return
            # podman cp always creates a subdirectory named after the source;
            # Docker's trailing-dot copies contents directly.  Move contents up.
            src_name = Path(podman_src).name
            staging = Path(dst) / src_name
            if staging.is_dir():
                for item in staging.iterdir():
                    shutil.move(str(item), str(Path(dst) / item.name))
                shutil.rmtree(staging, ignore_errors=True)
            return

        result = _run(
            ["docker", "cp", f"{container_id}:{src}", dst],
            timeout=120,
        )
        if result.returncode != 0:
            logger.warning("docker cp failed: %s", result.stderr)

    def _container_logs(self, container_id: str) -> subprocess.CompletedProcess | Any:
        """Return captured container logs using the configured engine."""
        if self._container_engine == "podman":
            try:
                return self._podman.logs(container_id, timeout=30)
            except PodmanError as exc:
                logger.warning("podman logs failed: %s", exc.result.stderr)
                return subprocess.CompletedProcess(
                    list(exc.result.redacted_argv),
                    exc.result.returncode,
                    stdout=exc.result.stdout,
                    stderr=exc.result.stderr,
                )
        return _run(["docker", "logs", container_id], timeout=30)

    def _container_stop(self, container_id: str) -> None:
        """Best-effort stop using the configured engine."""
        if self._container_engine == "podman":
            result = self._podman.stop(container_id, stop_timeout=10, timeout=30, check=False)
            if result.returncode != 0:
                logger.warning("podman stop failed: %s", result.stderr)
            return
        _run(["docker", "stop", "-t", "10", container_id], timeout=30)

    def _container_rm(self, container_id: str) -> None:
        """Best-effort container removal using the configured engine."""
        if self._container_engine == "podman":
            result = self._podman.rm(container_id, force=True, timeout=30, check=False)
            if result.returncode != 0:
                logger.warning("podman rm failed: %s", result.stderr)
            return
        _run(["docker", "rm", "-f", container_id], timeout=30)

    def _container_engine_metadata(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {"container_engine": self._container_engine}
        if self._container_engine == "podman":
            metadata["sandbox_backend"] = "podman"
            metadata["podman_devices"] = self._podman_devices
            metadata["podman_gpu_mode"] = self._resolved_podman_gpu_mode or self._podman_gpu_mode
        return metadata

    # ------------------------------------------------------------------
    # Podman GPU helpers
    # ------------------------------------------------------------------

    def _podman_gpu_args(self, mode: str) -> list[str]:
        if self._podman_gpu_mode == "none" or mode == "none":
            return []
        if mode == "legacy_devices":
            args = _legacy_nvidia_device_args(self._podman_devices)
            if args and self._podman_disable_selinux_label:
                args.extend(["--security-opt", "label=disable"])
            return args
        return nvidia_cdi_args(
            self._podman_devices,
            disable_selinux_label=self._podman_disable_selinux_label,
        )

    def _podman_runtime_args_and_env(
        self,
        mode: str,
        env: dict[str, str],
    ) -> tuple[list[str], dict[str, str]]:
        args = self._podman_gpu_args(mode)
        run_env = dict(env)
        if mode == "legacy_devices" and args and self._podman_mount_driver_libs:
            driver_args, lib_dirs = _legacy_nvidia_driver_mounts()
            args.extend(driver_args)
            if lib_dirs:
                run_env["LD_LIBRARY_PATH"] = _prepend_path_entries(
                    lib_dirs,
                    run_env.get("LD_LIBRARY_PATH"),
                )
        return args, run_env

    def _initial_podman_gpu_mode(self) -> str:
        if self._podman_gpu_mode in {"none", "legacy_devices"}:
            return self._podman_gpu_mode
        return "cdi"

    @staticmethod
    def _sample_index(task: BenchmarkTask) -> int:
        """Return the runtime sample index for this execution."""
        raw_sample_index = task.metadata.get("sample_index", 0)
        try:
            return max(0, int(raw_sample_index))
        except (TypeError, ValueError):
            return 0


@register_agent("moltbench_podman")
class MoltBenchPodmanAgent(MoltBenchDockerAgent):
    """Podman-default alias for MoltBench task-container evaluation."""

    name = "moltbench_podman"
    default_container_engine = "podman"
