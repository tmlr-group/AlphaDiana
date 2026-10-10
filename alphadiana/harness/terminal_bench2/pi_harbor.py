"""AlphaDiana harness that delegates Pi + Terminal-Bench execution to Harbor."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import signal
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from alphadiana.benchmarks.base import BenchmarkTask
from alphadiana.harness.terminal_bench2.pi_harbor_support import (
    _artifact_entry,
    _duration_seconds,
    _explicit_provider_error,
    _input_identity,
    _provider_reasoning_effort,
    _read_pi_trace,
    _request_audit_error,
    validate_pi_harbor_experiment_config,
)
from alphadiana.harness.base import Agent, AgentResponse
from alphadiana.engine.components import AgentRegistry

if TYPE_CHECKING:
    from alphadiana.engine.config.experiment_config import ExperimentConfig


_SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9_.-]+")
class HarborTrialError(RuntimeError):
    """Preserve a completed Harbor trial as an AlphaDiana error record."""

    def __init__(self, message: str, partial_response: AgentResponse) -> None:
        super().__init__(message)
        self.partial_response = partial_response


def _required_path(value: object, label: str, *, directory: bool = False) -> Path:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"terminal_bench2_pi_harbor requires agent.config.{label}")
    path = Path(text).expanduser().resolve()
    valid = path.is_dir() if directory else path.is_file()
    if not valid:
        kind = "directory" if directory else "file"
        raise ValueError(f"agent.config.{label} must be an existing {kind}: {path}")
    return path


class TerminalBench2PiHarborAgent(Agent):
    """Use Harbor as the runtime boundary, then import frozen artifacts read-only."""

    name = "terminal_bench2_pi_harbor"
    version = "1.0"
    owns_task_runtime = True
    runtime_isolation_mode = "harbor_disposable_task_container"

    @classmethod
    def validate_experiment_config(
        cls, config: "ExperimentConfig"
    ) -> list[str]:
        return validate_pi_harbor_experiment_config(config)

    def setup(self, config: dict) -> None:
        harbor_value = str(config.get("harbor_bin", "harbor") or "harbor")
        self._harbor_bin = shutil.which(harbor_value) or harbor_value
        if not Path(self._harbor_bin).is_file():
            raise ValueError(f"Harbor executable not found: {harbor_value}")
        self._pi_install = _required_path(
            config.get("pi_install_path"), "pi_install_path", directory=True
        )
        self._pi_executable = _required_path(
            self._pi_install / "pi", "pi_install_path/pi"
        )
        self._node_bin = _required_path(config.get("node_bin_path"), "node_bin_path")
        assets = Path(__file__).resolve().parent / "assets"
        self._candidate = _required_path(
            config.get("candidate_path") or assets / "pi_baseline.ts", "candidate_path"
        )
        self._audit = _required_path(
            config.get("audit_path") or assets / "pi_audit.ts", "audit_path"
        )
        self._models = _required_path(config.get("models_path"), "models_path")
        default_compose = Path(__file__).resolve().parent / "deploy" / "compose.pi.yaml"
        self._compose = _required_path(
            config.get("compose_path", default_compose), "compose_path"
        )
        self._jobs_dir = (
            Path(str(config.get("jobs_dir") or "./jobs/alphadiana-pi-harbor"))
            .expanduser()
            .resolve()
        )
        self._jobs_dir.mkdir(parents=True, exist_ok=True)
        self._pi_version = str(config.get("pi_version", "local-pi"))
        self._pi_model = str(config.get("pi_model", "")).strip()
        self._harbor_model = str(config.get("harbor_model", self._pi_model)).strip()
        self._reasoning = str(config.get("reasoning", "max") or "max").strip()
        self._dataset = str(
            config.get("dataset", "terminal-bench@2.0") or "terminal-bench@2.0"
        )
        self._missing_reward_policy = str(
            config.get("missing_reward_policy", "error") or "error"
        ).strip()
        if self._missing_reward_policy not in {"error", "zero"}:
            raise ValueError("missing_reward_policy must be 'error' or 'zero'")
        self._timeout_multiplier = float(config.get("timeout_multiplier", 1.0))
        self._max_retries = int(config.get("harbor_max_retries", 0))
        self._subprocess_timeout = float(config.get("subprocess_timeout_sec", 14400))
        if not self._pi_model or not self._harbor_model:
            raise ValueError("agent.config.pi_model and harbor_model must be non-empty")
        self._provider_reasoning = _provider_reasoning_effort(
            self._models,
            self._pi_model,
            self._reasoning,
        )
        self._runtime_inputs = {
            "node_binary": _input_identity(
                self._node_bin, access="docker_bind_mount_read_only"
            ),
            "pi_entrypoint": _input_identity(
                self._pi_executable, access="docker_bind_mount_read_only"
            ),
            "candidate_extension": _input_identity(
                self._candidate, access="uploaded_copy"
            ),
            "audit_extension": _input_identity(self._audit, access="uploaded_copy"),
            "models_config": _input_identity(
                self._models, access="uploaded_copy_hash_only"
            ),
            "compose_overlay": _input_identity(self._compose, access="harbor_input"),
        }
        package_lock = self._pi_install / "package-lock.json"
        if package_lock.is_file():
            self._runtime_inputs["pi_package_lock"] = _input_identity(
                package_lock, access="docker_bind_mount_read_only"
            )

    def _job_name(self, task: BenchmarkTask) -> str:
        task_name = task.task_id.removeprefix("tb2_")
        execution_id = str(task.metadata.get("execution_id", "") or os.urandom(8).hex())
        sample_index = int(task.metadata.get("sample_index", 0) or 0)
        # A Runner retry keeps the same execution_id. A per-solve nonce keeps
        # Harbor from attaching that retry to an earlier job or container.
        attempt_nonce = os.urandom(4).hex()
        raw = (
            f"alphadiana-pi-{task_name}-s{sample_index}-"
            f"{execution_id[:12]}-{attempt_nonce}"
        )
        return _SAFE_NAME_RE.sub("-", raw).strip("-")

    def _command(self, task: BenchmarkTask, job_name: str) -> list[str]:
        task_name = task.task_id.removeprefix("tb2_")
        return [
            self._harbor_bin,
            "run",
            "--agent-import-path",
            "alphadiana.harness.terminal_bench2.pi_harbor_adapter:PiHarborInstalledAgent",
            "--agent-kwarg",
            f"candidate_path={self._candidate}",
            "--agent-kwarg",
            f"audit_path={self._audit}",
            "--agent-kwarg",
            f"models_path={self._models}",
            "--agent-kwarg",
            f"pi_version={self._pi_version}",
            "--agent-kwarg",
            f"pi_model={self._pi_model}",
            "--agent-kwarg",
            f"reasoning={self._reasoning}",
            "--agent-kwarg",
            f"provider_reasoning={self._provider_reasoning}",
            "--model",
            self._harbor_model,
            "--dataset",
            self._dataset,
            "--env",
            "docker",
            "--n-attempts",
            "1",
            "--n-concurrent",
            "1",
            "--timeout-multiplier",
            str(self._timeout_multiplier),
            "--max-retries",
            str(self._max_retries),
            "--extra-docker-compose",
            str(self._compose),
            "--jobs-dir",
            str(self._jobs_dir),
            "--job-name",
            job_name,
            "--include-task-name",
            task_name,
            "--no-delete",
            "--yes",
        ]

    def _run_harbor(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        environment = dict(os.environ)
        package_root = str(Path(__file__).resolve().parents[3])
        current_pythonpath = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = package_root + (
            os.pathsep + current_pythonpath if current_pythonpath else ""
        )
        environment["ALPHADIANA_PI_NODE_BIN"] = str(self._node_bin)
        environment["ALPHADIANA_PI_INSTALL"] = str(self._pi_install)
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=self._subprocess_timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                stdout, stderr = process.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                stdout, stderr = process.communicate()
            raise RuntimeError(
                f"Harbor subprocess exceeded {self._subprocess_timeout:.0f}s; "
                f"process group terminated; stderr tail: {stderr[-1000:]}"
            )
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)

    def solve(
        self, task: BenchmarkTask, sandbox: Optional[Any] = None
    ) -> AgentResponse:
        if sandbox is not None:
            raise ValueError(
                "terminal_bench2_pi_harbor owns its Harbor isolation; do not configure a second sandbox"
            )
        job_name = self._job_name(task)
        command = self._command(task, job_name)
        completed = self._run_harbor(command)
        job_dir = self._jobs_dir / job_name
        result_paths = sorted(job_dir.glob("*/result.json"))
        if len(result_paths) != 1:
            raise RuntimeError(
                f"expected exactly one Harbor trial result in {job_dir}, found {len(result_paths)}; "
                f"Harbor exit={completed.returncode}; stderr tail: {completed.stderr[-1000:]}"
            )
        result_path = result_paths[0]
        trial_dir = result_path.parent
        result = json.loads(result_path.read_text())
        expected_task = task.task_id.removeprefix("tb2_")
        if result.get("task_name") != expected_task:
            raise RuntimeError(
                f"Harbor task mismatch: expected {expected_task!r}, got {result.get('task_name')!r}"
            )
        pi_trace = trial_dir / "agent" / "pi.jsonl"
        request_audit = trial_dir / "agent" / "request-audit.jsonl"
        trajectory, reasoning, final_answer = _read_pi_trace(pi_trace)
        agent_result = result.get("agent_result") or {}
        exception = result.get("exception_info") or {}
        exception_type = str(exception.get("exception_type") or "")
        rewards = (result.get("verifier_result") or {}).get("rewards") or {}
        raw_reward = rewards.get("reward")
        reward_observed = (
            not isinstance(raw_reward, bool)
            and isinstance(raw_reward, (int, float))
            and math.isfinite(float(raw_reward))
        )
        # A configured zero policy keeps a completed trial without a numeric
        # reward in the denominator. The default error policy fails closed.
        # Both preserve the raw Harbor condition in metadata and artifacts.
        reward = float(raw_reward) if reward_observed else 0.0
        trial_log = trial_dir / "trial.log"
        trial_log_text = (
            trial_log.read_text(errors="replace") if trial_log.is_file() else ""
        )
        verifier_timeout_observed = "VerifierTimeoutError" in trial_log_text
        finish_reason = (
            "timeout"
            if exception_type == "AgentTimeoutError"
            else ("error" if exception_type else "stop")
        )
        artifact_files: dict[str, Any] = {}
        for name, path in {
            "harbor_trial_result": result_path,
            "pi_jsonl": pi_trace,
            "request_audit": request_audit,
            "mechanism_firings": trial_dir / "agent" / "mechanism-firings.jsonl",
            "verifier_reward": trial_dir / "verifier" / "reward.txt",
            "verifier_stdout": trial_dir / "verifier" / "test-stdout.txt",
            "harbor_trial_log": trial_log,
            "harbor_exception": trial_dir / "exception.txt",
        }.items():
            if path.is_file():
                artifact_files[name] = _artifact_entry(path)

        response = AgentResponse(
            answer=str(reward).removesuffix(".0"),
            trajectory=trajectory,
            reasoning_trajectory=reasoning,
            raw_output=final_answer,
            token_usage={
                "prompt_tokens": int(agent_result.get("n_input_tokens", 0) or 0),
                "completion_tokens": int(agent_result.get("n_output_tokens", 0) or 0),
                "cache_tokens": int(agent_result.get("n_cache_tokens", 0) or 0),
            },
            wall_time_sec=_duration_seconds(result),
            metadata={
                "runner": "harbor",
                "transport": "pi_jsonl",
                "agent_final_answer": final_answer,
                # `verifier_status=ok` means AlphaDiana received a protocol-
                # defined score. The raw Harbor condition remains separate.
                "verifier_status": (
                    "ok"
                    if reward_observed or self._missing_reward_policy == "zero"
                    else "error"
                ),
                "harbor_verifier_status": (
                    "reward_observed"
                    if reward_observed
                    else "missing_reward_normalized_zero"
                ),
                "verifier_reward_observed": reward_observed,
                "verifier_reward": float(raw_reward) if reward_observed else None,
                "normalized_reward": reward,
                "reward_normalization": (
                    "none"
                    if reward_observed
                    else (
                        "configured_missing_reward_zero"
                        if self._missing_reward_policy == "zero"
                        else "missing_reward_error"
                    )
                ),
                "harbor_verifier_timeout_observed": verifier_timeout_observed,
                "harbor_returncode": completed.returncode,
                "harbor_job_name": job_name,
                "harbor_job_dir": str(job_dir),
                "harbor_trial_name": str(result.get("trial_name") or trial_dir.name),
                "harbor_exception_type": exception_type,
                "harbor_exception_message": str(
                    exception.get("exception_message") or ""
                ),
                "cost_usd": agent_result.get("cost_usd"),
                "metric_phase": "after_harbor_completion",
            },
            response_json={
                "harbor_result_id": result.get("id"),
                "task_checksum": result.get("task_checksum"),
            },
            sandbox_id=str(result.get("trial_name") or trial_dir.name),
            artifact_manifest={
                "kind": "harbor_external_read_only",
                "files": artifact_files,
                "runtime_inputs": self._runtime_inputs,
            },
            workspace_snapshot_paths=[str(trial_dir)],
            sandbox_metadata={
                "container_engine": "docker",
                "isolation_mode": "harbor_disposable_task_container",
                "alphadiana_in_agent_runtime": False,
                "metrics_in_agent_runtime": False,
                "post_run_import_only": True,
            },
            finish_reason=finish_reason,
        )
        provider_error = _explicit_provider_error(pi_trace)
        if provider_error:
            response.metadata["failure_reason"] = "provider_error"
            raise HarborTrialError(
                f"Pi provider failure in completed Harbor trial: {provider_error}",
                response,
            )
        if not reward_observed and self._missing_reward_policy == "error":
            response.metadata["failure_reason"] = "missing_verifier_reward"
            raise HarborTrialError(
                "Harbor trial did not produce a finite numeric verifier reward",
                response,
            )
        if reward_observed and exception_type and exception_type != "AgentTimeoutError":
            response.metadata["failure_reason"] = "runtime_error"
            raise HarborTrialError(
                f"Harbor trial ended with {exception_type}: "
                f"{response.metadata['harbor_exception_message']}",
                response,
            )
        if (
            reward_observed
            and completed.returncode != 0
            and exception_type != "AgentTimeoutError"
        ):
            response.metadata["failure_reason"] = "runtime_error"
            raise HarborTrialError(
                f"Harbor exited with status {completed.returncode}: {completed.stderr[-1000:]}",
                response,
            )
        audit_error = _request_audit_error(
            request_audit,
            self._pi_model.split("/", 1)[-1],
            self._provider_reasoning,
        )
        if audit_error:
            # A timeout may happen before Pi reaches its first provider call.
            # It remains Harbor's official scored timeout outcome, with the
            # absence of request evidence made explicit in metadata.
            if (
                exception_type == "AgentTimeoutError" or not reward_observed
            ) and audit_error in {
                "request-audit.jsonl is missing",
                "request-audit.jsonl contains no provider requests",
            }:
                response.metadata["request_audit_status"] = (
                    "no_requests_before_timeout"
                    if exception_type == "AgentTimeoutError"
                    else "no_requests_before_terminal_zero"
                )
            else:
                response.metadata["failure_reason"] = "runtime_error"
                response.metadata["request_audit_status"] = "invalid"
                raise HarborTrialError(
                    f"Pi request provenance validation failed: {audit_error}",
                    response,
                )
        else:
            response.metadata["request_audit_status"] = "validated"
        total_solver_tokens = sum(
            int(response.token_usage.get(key, 0) or 0)
            for key in ("prompt_tokens", "completion_tokens", "cache_tokens")
        )
        if (
            total_solver_tokens == 0
            and exception_type != "AgentTimeoutError"
            and reward_observed
        ):
            response.metadata["failure_reason"] = "runtime_error"
            raise HarborTrialError(
                "Harbor trial reported numeric reward but zero Pi solver token usage",
                response,
            )
        return response

    def teardown(self) -> None:
        pass


AgentRegistry.register("terminal_bench2_pi_harbor", TerminalBench2PiHarborAgent)
