"""Optional native Harbor terminus-2 adapter, with one job per task attempt."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from alphadiana.harness._internal.native_process import run_native
from alphadiana.harness.base import Agent, AgentResponse
from alphadiana.harness.proxies.native_capture import NativeProviderCapture
from alphadiana.engine.components import register_agent


class Terminus2RuntimeError(RuntimeError):
    """Retain the native trial failure even when its snapshot hook never ran."""

    def __init__(self, response: AgentResponse):
        exception_type = response.metadata["native_exception"]["exception_type"]
        super().__init__(
            f"Harbor trial failed with {exception_type}; "
            f"evidence: {response.metadata['native_output_dir']}"
        )
        self.error_type = response.metadata["failure_reason"]
        self.partial_response = response


def project_harbor_steps(steps):
    """Translate native ATIF roles/calls/observations at the adapter boundary.

    Raw Harbor evidence remains in the workspace sidecar. A task instruction is
    never an assistant action, and terminal output is a separate tool result.
    """
    projected = []
    for step in steps:
        source = step.get("source")
        roles = {"agent": "assistant", "user": "user", "system": "system"}
        if source not in roles:
            raise ValueError(f"Unsupported Harbor trajectory source: {source!r}")
        entry = {"role": roles[source], "content": step.get("message", "")}
        if step.get("reasoning_content"):
            entry["thinking"] = step["reasoning_content"]
        calls = [
            {
                "id": call.get("tool_call_id", ""),
                "name": call["function_name"],
                "input": deepcopy(call.get("arguments", {})),
            }
            for call in (step.get("tool_calls") or [])
        ]
        if calls:
            entry.update(type="tool_use", tool_calls=calls)
        projected.append(entry)
        for result in (step.get("observation") or {}).get("results", []):
            # Handoff references with no text remain in the native sidecar.
            content = result.get("content")
            if content is None:
                continue
            projected.append(
                {
                    "role": "tool",
                    "type": "tool_result",
                    "content": content,
                    "tool_results": [
                        {
                            "tool_use_id": result.get("source_call_id", ""),
                            "content": content,
                        }
                    ],
                }
            )
    return projected


def attribute_terminal_feedback(records, requests, steps):
    """Match textual terminal feedback to observed Harbor command observations.

    Harbor sends terminal output as a user message, not an OpenAI tool message.
    Require exact content hashes and unambiguous native command evidence; parser
    errors, initial instructions and completion-only observations are excluded.
    Unmatched requests keep their original provider-level attribution.
    """
    eligible = {}
    excluded = set()
    for step in steps:
        command = any(
            tool.get("function_name") == "bash_command"
            for tool in (step.get("tool_calls") or [])
            if isinstance(tool, dict)
        )
        contents = [
            item.get("content")
            for item in (step.get("observation") or {}).get("results", [])
            if isinstance(item, dict)
        ]
        if step.get("source") != "agent":
            contents.append(step.get("message"))
        for content in contents:
            if not isinstance(content, str) or not content:
                continue
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if command and step.get("source") == "agent":
                eligible.setdefault(digest, []).append(step.get("step_id"))
            else:
                excluded.add(digest)
    matched = {}
    for request in requests:
        digest = request.get("last_message_content_sha256")
        roles = request.get("message_roles_before") or []
        if (
            digest in eligible
            and digest not in excluded
            and request.get("call_index", 0) > 0
            and len(roles) >= 2
            and roles[-2:] == ["assistant", "user"]
        ):
            request["terminal_feedback_attribution"] = {
                "method": "harbor_command_observation_sha256",
                "step_ids": eligible[digest],
            }
            matched[request["call_id"]] = request["terminal_feedback_attribution"]
    for record in records:
        if record.get("call_id") in matched:
            record["follows_tool_result"] = True
            record["terminal_feedback_attribution"] = matched[record["call_id"]]


@register_agent("terminus2")
class Terminus2Agent(Agent):
    name = "terminus2"
    version = "2"

    def setup(self, config: dict) -> None:
        self.config = dict(config)
        self.retain_snapshot = bool(config.get("retain_verifier_snapshot", False))
        self.native_python = str(config.get("python_executable", ""))
        if self.retain_snapshot:
            if config.get("environment_type", "docker") != "docker":
                raise ValueError(
                    "Harbor verifier snapshots require the Docker environment"
                )
            if not self.native_python or not shutil.which(self.native_python):
                raise ValueError(
                    "Verifier snapshots require python_executable from the Harbor environment"
                )
        self.executable = str(config.get("executable") or shutil.which("harbor") or "")
        if not self.executable or not config.get("model") or not config.get("api_base"):
            raise ValueError(
                "Terminus-2 requires Harbor executable, model and api_base"
            )
        self.output = Path(
            config.get("output_dir", "results/terminus-native")
        ).resolve()
        self.output.mkdir(parents=True, exist_ok=True)

    def solve(self, task, sandbox=None) -> AgentResponse:
        if sandbox is not None:
            raise ValueError("Harbor owns its task environment; omit external sandbox")
        task_dir = Path(task.metadata.get("task_dir", ""))
        if not (task_dir / "task.toml").is_file():
            raise ValueError("Terminus-2 requires a native task directory")
        started = time.monotonic()
        directory = self.output / uuid4().hex
        directory.mkdir(mode=0o700)
        capture = NativeProviderCapture(self.config, directory)
        try:
            with capture:
                response = self._solve_native(
                    task, directory, capture.api_base, started
                )
        except Terminus2RuntimeError as exc:
            attribute_terminal_feedback(
                capture.records, capture.requests, exc.partial_response.trajectory
            )
            exc.partial_response.trajectory = project_harbor_steps(
                exc.partial_response.trajectory
            )
            capture.apply(exc.partial_response)
            raise
        attribute_terminal_feedback(
            capture.records, capture.requests, response.trajectory
        )
        response.trajectory = project_harbor_steps(response.trajectory)
        return capture.apply(response)

    def _solve_native(self, task, directory, api_base, started) -> AgentResponse:
        task_dir = Path(task.metadata["task_dir"])
        model = str(self.config["model"])
        model = model if model.startswith("openai/") else "openai/" + model
        llm_call_kwargs = deepcopy(self.config.get("llm_call_kwargs", {}))
        llm_call_kwargs.setdefault("max_tokens", self.config.get("max_tokens", 8192))
        job = {
            "job_name": "evaluation",
            "jobs_dir": str(directory),
            "n_attempts": 1,
            "n_concurrent_trials": 1,
            "retry": {"max_retries": 0},
            "tasks": [{"path": str(task_dir.resolve())}],
            "agents": [
                {
                    "name": "terminus-2",
                    "model_name": model,
                    "override_timeout_sec": float(
                        self.config.get("agent_timeout", 900)
                    ),
                    "kwargs": {
                        "api_base": api_base,
                        "temperature": self.config.get("temperature", 0.0),
                        "llm_call_kwargs": llm_call_kwargs,
                        **(
                            {"max_turns": int(self.config["max_turns"])}
                            if self.config.get("max_turns") is not None
                            else {}
                        ),
                        **(
                            {"model_info": deepcopy(self.config["model_info"])}
                            if self.config.get("model_info") is not None
                            else {}
                        ),
                    },
                }
            ],
            "environment": {
                "type": self.config.get("environment_type", "docker"),
                # Harbor's delete=True also removes the shared prebuilt image.
                # False still tears down the task's containers and network.
                "delete": False,
            },
        }
        config_path = directory / "job.json"
        config_path.write_text(json.dumps(job))
        env = dict(os.environ, OPENAI_API_KEY=self.config.get("api_key", "EMPTY"))
        snapshot_path = directory / "verifier_snapshot.json"
        argv = [self.executable, "run", "--config", str(config_path), "--yes"]
        if self.retain_snapshot:
            env["PYTHONPATH"] = os.pathsep.join(
                filter(
                    None,
                    [
                        str(Path(__file__).resolve().parents[3]),
                        env.get("PYTHONPATH", ""),
                    ],
                )
            )
            argv = [
                self.native_python,
                "-m",
                "alphadiana.harness.terminus2.job",
                str(config_path),
                str(snapshot_path),
                task.task_id,
            ]
        process_error = None
        try:
            run_native(
                argv,
                cwd=directory,
                env=env,
                log=directory / "native.log",
                timeout=float(self.config.get("timeout", 1800)),
            )
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            process_error = exc
        results = []
        for path in (directory / "evaluation").glob("*/result.json"):
            result = json.loads(path.read_text())
            if result.get("task_name") == task_dir.name:
                results.append((path, result))
        if len(results) != 1:
            if process_error is not None:
                raise process_error
            raise RuntimeError(f"Expected one native trial result, got {len(results)}")
        path, result = results[0]
        exception = result.get("exception_info") or {}
        agent_timing = result.get("agent_execution") or {}
        owned_timeout = (
            exception.get("exception_type") == "AgentTimeoutError"
            and bool(agent_timing.get("started_at"))
            and bool(agent_timing.get("finished_at"))
        )
        if exception and not owned_timeout:
            # The native API exception is authoritative. A missing snapshot or
            # nonzero wrapper exit must not replace it with a runtime failure.
            failure_reason = (
                "provider_error"
                if exception.get("exception_type")
                in {
                    "APIError",
                    "APIConnectionError",
                    "APITimeoutError",
                    "AuthenticationError",
                    "PermissionDeniedError",
                    "BadRequestError",
                    "RateLimitError",
                    "InternalServerError",
                    "NotFoundError",
                    "UnprocessableEntityError",
                }
                else "runtime_error"
            )
            trajectory_path = path.parent / "agent/trajectory.json"
            trace = (
                json.loads(trajectory_path.read_text())
                if trajectory_path.is_file()
                else {}
            )
            steps = trace.get("steps", []) if isinstance(trace, dict) else trace
            raise Terminus2RuntimeError(
                AgentResponse(
                    answer=None,
                    trajectory=steps,
                    wall_time_sec=time.monotonic() - started,
                    finish_reason="error",
                    metadata={
                        "native_runtime": "terminus-2",
                        "native_output_dir": str(directory),
                        "native_exception": exception,
                        "failure_reason": failure_reason,
                        "verifier_reward_observed": False,
                    },
                    workspace_file_contents={
                        "harbor_result.json": json.dumps(result),
                        "harbor_trajectory.json": json.dumps(trace),
                    },
                )
            ) from process_error
        if process_error is not None:
            raise process_error
        if self.retain_snapshot:
            snapshot = json.loads(snapshot_path.read_text())
            if snapshot.get("task_id") != task.task_id or not snapshot.get(
                "captured_before_verification"
            ):
                raise RuntimeError(
                    "Harbor snapshot does not match this task and capture phase"
                )
            task.metadata["verifier_snapshot"] = snapshot
        rewards = (result.get("verifier_result") or {}).get("rewards") or {}
        if "reward" not in rewards:
            raise RuntimeError(f"Harbor verifier produced no reward; evidence: {path}")
        reward = rewards["reward"]
        if (
            isinstance(reward, bool)
            or not isinstance(reward, (int, float))
            or reward not in (0, 1)
        ):
            raise ValueError(
                f"Expected binary TB2 reward, got {reward!r}; evidence: {path}"
            )
        agent_result = result.get("agent_result") or {}
        usage = {
            target: agent_result[source]
            for source, target in (
                ("n_input_tokens", "prompt_tokens"),
                ("n_output_tokens", "completion_tokens"),
            )
            if agent_result.get(source) is not None
        }
        trajectory_path = path.parent / "agent/trajectory.json"
        trace = (
            json.loads(trajectory_path.read_text()) if trajectory_path.exists() else {}
        )
        steps = trace.get("steps", []) if isinstance(trace, dict) else trace
        return AgentResponse(
            answer=str(int(rewards["reward"])),
            trajectory=steps,
            token_usage=usage,
            wall_time_sec=time.monotonic() - started,
            finish_reason="timeout" if owned_timeout else "stop",
            metadata={
                "native_runtime": "terminus-2",
                "native_output_dir": str(directory),
                "verifier_status": "ok",
                "verifier_reward_observed": True,
                "reward": rewards["reward"],
                "native_exception": exception,
                "timed_out": owned_timeout,
            },
            workspace_file_contents={
                "harbor_result.json": json.dumps(result),
                "harbor_trajectory.json": json.dumps(trace),
            },
        )
