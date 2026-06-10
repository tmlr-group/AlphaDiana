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
import secrets
import shlex
import shutil
import socket
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
from alphadiana.benchmarks.moltbench.qjl_contract import (
    QJL_DISTORTION_THRESHOLD,
    QJL_HIDDEN_SEED_COUNT,
    QJL_RETENTION_THRESHOLD,
)
from alphadiana.benchmarks.moltbench.suitcase import EvaluationSuitcase, load_jsonl

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
        self._verifier_image: str = "localhost/alphadiana:qjl-verifier"
        self._verifier_startup_timeout: int = 120

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
        self._verifier_image = str(
            config.get("verifier_image", "localhost/alphadiana:qjl-verifier")
        )
        self._verifier_startup_timeout = int(config.get("verifier_startup_timeout", 120))
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
        if task.metadata.get("external_verifier") == "qjl":
            return self._solve_qjl(task)

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

    def _container_run(
        self,
        task_dir: str,
        *,
        network_mode: str | None = None,
        extra_mounts: list[str] | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> str:
        """Start a task container and return its ID."""
        cmd = self._build_run_command(
            task_dir,
            network_mode=network_mode,
            extra_mounts=extra_mounts,
            extra_env=extra_env,
        )
        result = _run(cmd, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(
                f"{self._container_engine} run failed (exit {result.returncode}): {result.stderr}"
            )
        return result.stdout.strip()

    def _build_run_command(
        self,
        task_dir: str,
        *,
        network_mode: str | None = None,
        extra_mounts: list[str] | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> list[str]:
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
        for volume in extra_mounts or []:
            cmd.extend(["-v", volume])
        if self._agent_type == "claude-code" and self._mount_claude_auth:
            claude_dir = Path.home() / ".claude"
            claude_json = Path.home() / ".claude.json"
            if claude_dir.exists():
                cmd.extend(["-v", f"{claude_dir}:/root/.claude:ro"])
            if claude_json.exists():
                cmd.extend(["-v", f"{claude_json}:/root/.claude.json:ro"])
        resolved_network = network_mode if network_mode is not None else self._network_mode
        if resolved_network:
            cmd.extend(["--network", resolved_network])

        # Environment variables
        env = {
            **self._env,
            **(extra_env or {}),
            "MOLTBENCH_AGENT": self._agent_type,
        }
        for key, val in env.items():
            cmd.extend(["-e", f"{key}={val}"])

        cmd.append(self._image)
        return cmd

    def _container_wait(self, container_id: str, timeout: int | None = None) -> int:
        """Wait for a container to exit. Returns exit code."""
        wait_timeout = self._timeout if timeout is None else timeout
        result = _run(
            [self._container_engine_binary, "wait", container_id],
            timeout=wait_timeout + 60,  # extra buffer
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

    def _solve_qjl(self, task: BenchmarkTask) -> AgentResponse:
        """Run QJL with a verifier outside the agent trust boundary."""
        started = time.monotonic()
        task_dir = tempfile.mkdtemp(prefix=f"moltbench_{task.task_id}_")
        task_payload = _prepare_task_payload(task, task_dir)
        (Path(task_dir) / "task.json").write_text(
            json.dumps(task_payload, indent=2),
            encoding="utf-8",
        )
        client_source = (
            Path(__file__).resolve().parents[3]
            / "moltbench"
            / "verifier"
            / "qjl"
            / "run_eval_client.py"
        )
        client_target = Path(task_dir) / "run_eval"
        shutil.copy2(client_source, client_target)
        client_target.chmod(0o755)

        sample_index = self._sample_index(task)
        execution_id = str(task.metadata.get("execution_id") or uuid4().hex)
        artifacts_local = (
            Path(self._output_dir)
            / task.task_id
            / f"sample_{sample_index}"
            / execution_id
        )
        suitcase = EvaluationSuitcase(
            artifacts_local,
            execution_id=execution_id,
            task_id=task.task_id,
            task_payload=task_payload,
            agent_type=self._agent_type,
            runtime_config={
                "agent_image": self._image,
                "agent_image_id": self._image_identity(self._image),
                "verifier_image": self._verifier_image,
                "verifier_image_id": self._image_identity(self._verifier_image),
                "container_engine": self._container_engine,
                "container_engine_binary": self._container_engine_binary,
                "container_runtime": self._container_runtime,
                "gpu_args": self._gpu_args(),
                "network_mode": self._network_mode,
                "model": self._env.get("OPENAI_MODEL_NAME", ""),
                "env": {
                    key: (
                        "<redacted>"
                        if any(marker in key.upper() for marker in ("KEY", "TOKEN", "SECRET"))
                        else value
                    )
                    for key, value in self._env.items()
                },
                "hardware_contract": task_payload.get("hardware", {}),
            },
        )
        feedback_seeds = tuple(
            secrets.randbelow(2**31 - 1) + 1
            for _ in range(QJL_HIDDEN_SEED_COUNT)
        )
        official_seeds = tuple(
            secrets.randbelow(2**31 - 1) + 1
            for _ in range(QJL_HIDDEN_SEED_COUNT)
        )
        task_budget = task_payload.get("budget", {})
        attempt_timeout = int(task_budget.get("attempt_timeout_sec", 1200))
        total_timeout = int(task_budget.get("total_timeout_sec", self._timeout))
        suitcase.write_verifier_config(
            {
                "feedback_seeds": list(feedback_seeds),
                "official_seeds": list(official_seeds),
                "distortion_threshold": QJL_DISTORTION_THRESHOLD,
                "attempt_timeout_sec": attempt_timeout,
                "total_timeout_sec": total_timeout,
                "visibility": "host",
            }
        )
        owns_network = not bool(self._network_mode)
        network_name = self._network_mode or f"mb-qjl-{execution_id[:12]}"
        verifier_name = f"mb-qjl-verifier-{execution_id[:12]}"
        verifier_port = self._unused_local_port() if network_name == "host" else 8081
        verifier_host = "127.0.0.1" if network_name == "host" else verifier_name
        run_eval_token = secrets.token_urlsafe(32)
        agent_container = ""
        verifier_container = ""
        exit_code: int | None = None
        timed_out = False
        raw_output = ""
        trajectory: list[dict[str, Any]] = []
        official_result = self._qjl_failure_result("official_eval_not_run")
        baseline_result = self._qjl_failure_result("baseline_eval_not_run")
        feedback_baseline_result = self._qjl_failure_result(
            "feedback_baseline_eval_not_run"
        )
        baseline_runtime = float(task.ground_truth.get("runtime_ms", 0.0))
        exit_status = "error"

        try:
            if owns_network:
                self._create_network(network_name)
            suitcase.event("workspace_init", {"network": network_name})
            verifier_container = self._start_qjl_verifier(
                verifier_name=verifier_name,
                network_name=network_name,
                artifacts_dir=artifacts_local,
                token=run_eval_token,
                task=task,
                verifier_port=verifier_port,
                hidden_seeds=feedback_seeds,
                attempt_timeout=attempt_timeout,
                total_timeout=total_timeout,
            )
            self._wait_qjl_verifier(
                verifier_container,
                verifier_port,
                startup_timeout=max(
                    self._verifier_startup_timeout,
                    attempt_timeout + 60,
                ),
            )
            baseline_path = artifacts_local / "baseline_eval.json"
            baseline_result = json.loads(baseline_path.read_text(encoding="utf-8"))
            feedback_baseline_result = baseline_result
            if not baseline_result.get("correct"):
                raise RuntimeError(
                    "same-seed baseline failed: "
                    f"{baseline_result.get('failure_reason', 'unknown')}"
                )
            baseline_runtime = float(
                baseline_result.get("metrics", {}).get("runtime_ms", 0.0)
            )
            if baseline_runtime <= 0:
                raise RuntimeError("same-seed baseline returned invalid runtime")
            suitcase.event("sandbox_event", {"verifier_started": verifier_container})

            agent_container = self._container_run(
                task_dir,
                network_mode=network_name,
                extra_mounts=[f"{client_target}:/eval/run_eval:ro"],
                extra_env={
                    "MOLTBENCH_VERIFIER_URL": (
                        f"http://{verifier_host}:{verifier_port}/eval/run_eval"
                    ),
                    "MOLTBENCH_RUN_EVAL_TOKEN": run_eval_token,
                    "MOLTBENCH_ATTEMPT_TIMEOUT_SEC": str(attempt_timeout),
                },
            )
            suitcase.event("agent_start", {"container_id": agent_container})
            try:
                exit_code = self._container_wait(
                    agent_container,
                    timeout=min(self._timeout, total_timeout),
                )
                exit_status = f"exit_{exit_code}"
            except subprocess.TimeoutExpired:
                timed_out = True
                exit_status = "timeout"
                suitcase.event("error_event", {"reason": "agent_timeout"})
                _run(
                    [self._container_engine_binary, "stop", "-t", "10", agent_container],
                    timeout=30,
                )

            agent_artifacts = artifacts_local / "agent"
            agent_artifacts.mkdir(exist_ok=True)
            self._container_cp(
                agent_container,
                "/workspace/artifacts/.",
                str(agent_artifacts),
            )
            logs_result = _run(
                [self._container_engine_binary, "logs", agent_container],
                timeout=30,
            )
            raw_output = logs_result.stdout[-10000:] if logs_result.stdout else ""
            (artifacts_local / "logs" / "agent_stdout.log").write_text(
                logs_result.stdout,
                encoding="utf-8",
            )
            (artifacts_local / "logs" / "agent_stderr.log").write_text(
                logs_result.stderr,
                encoding="utf-8",
            )
            trajectory = parse_trajectory(agent_artifacts)
            suitcase.record_trajectory(trajectory)

            final_snapshot = artifacts_local / "final_workspace_snapshot"
            self._container_cp(
                agent_container,
                "/workspace/repo/qjl_kernel/csrc/.",
                str(final_snapshot),
            )
            invalid_paths = self._qjl_non_editable_changes(agent_container)
            if invalid_paths:
                official_result = self._qjl_failure_result(
                    "non_editable_paths_changed: " + ", ".join(invalid_paths)
                )
                official_result["verifier_status"] = "REJECTED"
            elif any(final_snapshot.glob("*.cu")):
                candidate_path = suitcase.write_candidate_payload(final_snapshot)
                comparison_path = suitcase.write_candidate_payload(
                    artifacts_local / "best_workspace_snapshot",
                    "best_candidate.json",
                )
                remaining = total_timeout - (time.monotonic() - started)
                if remaining <= 0:
                    official_result = self._qjl_failure_result(
                        "episode_time_budget_exhausted"
                    )
                    official_result["verifier_status"] = "TIMEOUT"
                else:
                    official_result = self._run_qjl_official_eval(
                        candidate_path,
                        comparison_path=comparison_path,
                        artifacts_dir=artifacts_local,
                        hidden_seeds=official_seeds,
                        attempt_timeout=attempt_timeout,
                        timeout=max(1, int(remaining)),
                    )
                    official_baseline = official_result.get("baseline_result")
                    if isinstance(official_baseline, dict):
                        baseline_result = official_baseline
                        baseline_runtime = float(
                            baseline_result.get("metrics", {}).get(
                                "runtime_ms", baseline_runtime
                            )
                        )
                if official_result.get("correct"):
                    best_snapshot = artifacts_local / "best_workspace_snapshot"
                    state_path = artifacts_local / "attempt_state.json"
                    state = (
                        json.loads(state_path.read_text(encoding="utf-8"))
                        if state_path.exists()
                        else {}
                    )
                    official_gain = float(
                        official_result.get("metrics", {}).get("gain", 0.0)
                    )
                    comparison_gain = float(
                        official_result.get("comparison_result", {})
                        .get("metrics", {})
                        .get("gain", 0.0)
                    )
                    best_official_gain = max(1.0, comparison_gain, official_gain)
                    if official_gain >= best_official_gain:
                        suitcase.copy_snapshot(final_snapshot, "best_workspace_snapshot")
                        state["best_gain"] = official_gain
                        state_path.write_text(
                            json.dumps(state, indent=2, sort_keys=True),
                            encoding="utf-8",
                        )
                    elif 1.0 >= comparison_gain:
                        suitcase.copy_snapshot(
                            artifacts_local / "initial_workspace_snapshot",
                            "best_workspace_snapshot",
                        )
                        state["best_gain"] = 1.0
                        state_path.write_text(
                            json.dumps(state, indent=2, sort_keys=True),
                            encoding="utf-8",
                        )
            else:
                official_result = self._qjl_failure_result(
                    "final_candidate_snapshot_missing"
                )
        except Exception as exc:
            logger.exception("QJL episode %s failed", execution_id)
            suitcase.event("error_event", {"reason": str(exc)})
            official_result = self._qjl_failure_result(str(exc))
            raw_output = f"{raw_output}\n{exc}".strip()
        finally:
            wall_time = time.monotonic() - started
            verifier_results = load_jsonl(artifacts_local / "verifier_results.jsonl")
            gpu_time = sum(
                float(result.get("wall_time_sec", 0.0))
                for result in verifier_results
            )
            gpu_time += float(feedback_baseline_result.get("wall_time_sec", 0.0))
            gpu_time += float(official_result.get("wall_time_sec", 0.0))
            gpu_time += float(
                official_result.get("baseline_result", {}).get("wall_time_sec", 0.0)
            )
            gpu_time += float(
                official_result.get("comparison_result", {}).get(
                    "wall_time_sec", 0.0
                )
            )
            attempts = 0
            attempt_state_path = artifacts_local / "attempt_state.json"
            if attempt_state_path.exists():
                try:
                    attempts = int(
                        json.loads(attempt_state_path.read_text(encoding="utf-8")).get(
                            "attempts", 0
                        )
                    )
                except (ValueError, json.JSONDecodeError):
                    pass
            trajectory_cost = suitcase.trajectory_cost()
            verifier_calls = attempts
            if (
                feedback_baseline_result.get("failure_reason")
                != "feedback_baseline_eval_not_run"
            ):
                verifier_calls += 1
            if official_result.get("failure_reason") != "official_eval_not_run":
                verifier_calls += 1
                if isinstance(official_result.get("baseline_result"), dict):
                    verifier_calls += 1
                if isinstance(official_result.get("comparison_result"), dict):
                    verifier_calls += 1
            cost = {
                "attempts": attempts,
                "verifier_calls": verifier_calls,
                "wall_time_sec": wall_time,
                "gpu_time_sec": gpu_time,
                "model_calls": trajectory_cost["model_calls"],
                "tool_calls": trajectory_cost["tool_calls"],
                "input_tokens": trajectory_cost["input_tokens"],
                "output_tokens": trajectory_cost["output_tokens"],
                "tokens": (
                    trajectory_cost["input_tokens"]
                    + trajectory_cost["output_tokens"]
                ),
                "cost_usd": (
                    trajectory_cost["cost_usd"]
                    if trajectory_cost["cost_reported"]
                    else None
                ),
            }
            budget_violations = []
            for budget_key, cost_key in (
                ("max_model_calls", "model_calls"),
                ("max_tool_calls", "tool_calls"),
                ("max_tokens", "tokens"),
            ):
                limit = task_budget.get(budget_key)
                if limit is not None and cost[cost_key] > int(limit):
                    budget_violations.append(
                        f"{cost_key}={cost[cost_key]} exceeds {budget_key}={limit}"
                    )
            max_cost = task_budget.get("max_cost_usd")
            if (
                max_cost is not None
                and cost["cost_usd"] is not None
                and float(cost["cost_usd"]) > float(max_cost)
            ):
                budget_violations.append(
                    f"cost_usd={cost['cost_usd']} exceeds max_cost_usd={max_cost}"
                )
            if budget_violations:
                official_result = self._qjl_failure_result(
                    "budget_exceeded: " + "; ".join(budget_violations)
                )
                official_result["verifier_status"] = "REJECTED"
                suitcase.event(
                    "error_event",
                    {"reason": "budget_exceeded", "violations": budget_violations},
                )
            try:
                suitcase.finalize(
                    official_result=official_result,
                    baseline_result=baseline_result,
                    baseline_runtime_ms=baseline_runtime,
                    retention_threshold=float(
                        task.metadata.get(
                            "retention_threshold",
                            QJL_RETENTION_THRESHOLD,
                        )
                    ),
                    cost=cost,
                    exit_status=exit_status,
                )
            finally:
                if agent_container:
                    _run(
                        [self._container_engine_binary, "rm", "-f", agent_container],
                        timeout=30,
                    )
                if verifier_container:
                    _run(
                        [self._container_engine_binary, "rm", "-f", verifier_container],
                        timeout=30,
                    )
                if owns_network:
                    self._remove_network(network_name)
                shutil.rmtree(task_dir, ignore_errors=True)

        return AgentResponse(
            answer={"official_evaluation": "host_owned"},
            trajectory=trajectory,
            raw_output=raw_output,
            wall_time_sec=time.monotonic() - started,
            metadata={
                "artifacts_dir": str(artifacts_local),
                "container_id": agent_container,
                "exit_code": exit_code,
                "timeout": timed_out,
                "sample_index": sample_index,
                "execution_id": execution_id,
                "agent_type": self._agent_type,
                "image": self._image,
                "verifier_image": self._verifier_image,
                "container_engine": self._container_engine,
                "container_engine_binary": self._container_engine_binary,
                "official_eval_host_owned": True,
            },
        )

    def _create_network(self, name: str) -> None:
        result = _run(
            [self._container_engine_binary, "network", "create", name],
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"{self._container_engine} network create failed: {result.stderr}"
            )

    def _remove_network(self, name: str) -> None:
        _run(
            [self._container_engine_binary, "network", "rm", name],
            timeout=30,
        )

    def _gpu_args(self) -> list[str]:
        if self._container_engine == "podman":
            return list(self._podman_gpu_args)
        return ["--gpus", self._gpus]

    def _image_identity(self, image: str) -> str:
        try:
            result = _run(
                [
                    self._container_engine_binary,
                    "image",
                    "inspect",
                    "--format",
                    "{{.Id}}",
                    image,
                ],
                timeout=30,
            )
        except Exception:
            return ""
        return result.stdout.strip() if result.returncode == 0 else ""

    @staticmethod
    def _unused_local_port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def _qjl_non_editable_changes(self, container_id: str) -> list[str]:
        result = _run(
            [
                self._container_engine_binary,
                "exec",
                container_id,
                "git",
                "-C",
                "/workspace/repo",
                "status",
                "--porcelain",
                "--untracked-files=all",
            ],
            timeout=30,
        )
        if result.returncode != 0:
            diff_result = _run(
                [self._container_engine_binary, "diff", container_id],
                timeout=30,
            )
            if diff_result.returncode != 0:
                return ["<container_diff_unavailable>"]
            paths = []
            prefix = "/workspace/repo/"
            for line in diff_result.stdout.splitlines():
                absolute = line[2:].strip()
                if absolute == "/workspace/repo":
                    continue
                if absolute.startswith(prefix):
                    paths.append(absolute[len(prefix):])
            return self._filter_qjl_changed_paths(paths)
        paths = [
            line[3:].strip().split(" -> ")[-1]
            for line in result.stdout.splitlines()
        ]
        return self._filter_qjl_changed_paths(paths)

    @staticmethod
    def _filter_qjl_changed_paths(paths: list[str]) -> list[str]:
        ignored = (
            ".git/",
            "openclaw.json",
            "opencode.json",
            "qjl_kernel/build/",
            "qjl_kernel/__pycache__/",
        )
        ignored_exact = {
            ".git",
            "qjl_kernel",
            "qjl_kernel/csrc",
        }
        invalid: list[str] = []
        for path in paths:
            if path in ignored_exact:
                continue
            if (
                path.startswith("qjl_kernel/csrc/")
                and path.endswith(".cu")
                and Path(path).parent.as_posix() == "qjl_kernel/csrc"
            ):
                continue
            if path.startswith(ignored):
                continue
            if path.startswith("qjl_kernel/") and ".so" in Path(path).name:
                continue
            invalid.append(path)
        return sorted(set(invalid))

    def _start_qjl_verifier(
        self,
        *,
        verifier_name: str,
        network_name: str,
        artifacts_dir: Path,
        token: str,
        task: BenchmarkTask,
        verifier_port: int,
        hidden_seeds: tuple[int, ...],
        attempt_timeout: int,
        total_timeout: int,
    ) -> str:
        baseline_runtime = float(task.ground_truth["runtime_ms"])
        max_attempts = int(task.metadata.get("max_attempts", 5))
        cmd = [
            self._container_engine_binary,
            "run",
            "-d",
        ]
        if self._container_runtime:
            cmd.extend(["--runtime", self._container_runtime])
        cmd.extend([
            "--name",
            verifier_name,
            "--network",
            network_name,
            *self._gpu_args(),
            "--read-only",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=8g",
            "-v",
            f"{artifacts_dir.resolve()}:/artifacts",
            "-e",
            f"QJL_RUN_EVAL_TOKEN={token}",
            "-e",
            f"QJL_MAX_ATTEMPTS={max_attempts}",
            "-e",
            f"QJL_BASELINE_RUNTIME_MS={baseline_runtime}",
            "-e",
            f"QJL_DISTORTION_THRESHOLD={QJL_DISTORTION_THRESHOLD}",
            "-e",
            f"QJL_HIDDEN_SEEDS={','.join(str(seed) for seed in hidden_seeds)}",
            "-e",
            f"QJL_ATTEMPT_TIMEOUT_SEC={attempt_timeout}",
            "-e",
            f"QJL_TOTAL_TIMEOUT_SEC={total_timeout}",
            "-e",
            f"QJL_EXECUTION_ID={artifacts_dir.name}",
            "-e",
            f"QJL_BUILD_TIMEOUT_SEC={attempt_timeout}",
            "-e",
            f"QJL_EVAL_TIMEOUT_SEC={attempt_timeout}",
            "-e",
            f"QJL_VERIFIER_PORT={verifier_port}",
        ])
        if self._shm_size:
            cmd.extend(["--shm-size", str(self._shm_size)])
        if self._ipc_mode:
            cmd.extend(["--ipc", self._ipc_mode])
        cmd.append(self._verifier_image)
        result = _run(cmd, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(f"verifier start failed: {result.stderr}")
        return result.stdout.strip()

    def _wait_qjl_verifier(
        self,
        container_id: str,
        verifier_port: int,
        *,
        startup_timeout: int | None = None,
    ) -> None:
        deadline = time.monotonic() + (
            self._verifier_startup_timeout
            if startup_timeout is None
            else startup_timeout
        )
        probe = (
            "import urllib.request; "
            f"urllib.request.urlopen('http://127.0.0.1:{verifier_port}/health', "
            "timeout=2).read()"
        )
        while time.monotonic() < deadline:
            result = _run(
                [
                    self._container_engine_binary,
                    "exec",
                    container_id,
                    "python3",
                    "-c",
                    probe,
                ],
                timeout=10,
            )
            if result.returncode == 0:
                return
            time.sleep(1)
        raise RuntimeError("QJL verifier failed health check")

    def _run_qjl_official_eval(
        self,
        candidate_path: Path,
        *,
        comparison_path: Path,
        artifacts_dir: Path,
        hidden_seeds: tuple[int, ...],
        attempt_timeout: int,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        official_name = f"mb-qjl-official-{artifacts_dir.name[:12]}"
        cmd = [
            self._container_engine_binary,
            "run",
            "--rm",
            "--name",
            official_name,
        ]
        if self._container_runtime:
            cmd.extend(["--runtime", self._container_runtime])
        cmd.extend(
            [
                *self._gpu_args(),
                "--network",
                "none",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,size=8g",
                "-v",
                f"{artifacts_dir.resolve()}:/artifacts",
                "-e",
                f"QJL_BASELINE_RUNTIME_MS={float(1.0)}",
                "-e",
                f"QJL_DISTORTION_THRESHOLD={QJL_DISTORTION_THRESHOLD}",
                "-e",
                f"QJL_HIDDEN_SEEDS={','.join(str(seed) for seed in hidden_seeds)}",
                "-e",
                f"QJL_ATTEMPT_TIMEOUT_SEC={attempt_timeout}",
                "-e",
                f"QJL_BUILD_TIMEOUT_SEC={attempt_timeout}",
                "-e",
                f"QJL_EVAL_TIMEOUT_SEC={attempt_timeout}",
            ]
        )
        if self._shm_size:
            cmd.extend(["--shm-size", str(self._shm_size)])
        if self._ipc_mode:
            cmd.extend(["--ipc", self._ipc_mode])
        cmd.extend(
            [
                "--entrypoint",
                "python3",
                self._verifier_image,
                "/verifier/official_eval.py",
                "--candidate",
                f"/artifacts/{candidate_path.name}",
                "--comparison",
                f"/artifacts/{comparison_path.name}",
            ]
        )
        try:
            result = _run(
                cmd,
                timeout=self._timeout if timeout is None else timeout,
            )
        except subprocess.TimeoutExpired:
            _run(
                [self._container_engine_binary, "rm", "-f", official_name],
                timeout=30,
            )
            timed_out = self._qjl_failure_result("official_eval_timeout")
            timed_out["verifier_status"] = "TIMEOUT"
            return timed_out
        if result.returncode != 0:
            return self._qjl_failure_result(
                f"official_eval_failed: {result.stderr.strip()}"
            )
        try:
            payload = json.loads(result.stdout.splitlines()[-1])
        except (IndexError, json.JSONDecodeError):
            return self._qjl_failure_result("official_eval_invalid_json")
        if not isinstance(payload, dict):
            return self._qjl_failure_result("official_eval_invalid_payload")
        return payload

    @staticmethod
    def _qjl_failure_result(reason: str) -> dict[str, Any]:
        return {
            "correct": False,
            "score": 0.0,
            "metrics": {
                "runtime_ms": 0.0,
                "distortion": 1.0,
                "gain": 0.0,
            },
            "wall_time_sec": 0.0,
            "verifier_status": "FAIL",
            "failure_reason": reason,
        }

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
