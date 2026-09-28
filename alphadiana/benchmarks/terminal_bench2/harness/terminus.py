"""Optional native Harbor terminus-2 adapter, with one job per task attempt."""

from __future__ import annotations

import json
import os
import shutil
import time
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from alphadiana.harness._internal.native_process import run_native
from alphadiana.harness.base import Agent, AgentResponse
from alphadiana.harness.registry import register_agent


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
                        "api_base": self.config["api_base"],
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
                        str(Path(__file__).resolve().parents[4]),
                        env.get("PYTHONPATH", ""),
                    ],
                )
            )
            argv = [
                self.native_python,
                "-m",
                "alphadiana.benchmarks.terminal_bench2.harness.harbor_job",
                str(config_path),
                str(snapshot_path),
                task.task_id,
            ]
        run_native(
            argv,
            cwd=directory,
            env=env,
            log=directory / "native.log",
            timeout=float(self.config.get("timeout", 1800)),
        )
        if self.retain_snapshot:
            snapshot = json.loads(snapshot_path.read_text())
            if snapshot.get("task_id") != task.task_id or not snapshot.get(
                "captured_before_verification"
            ):
                raise RuntimeError(
                    "Harbor snapshot does not match this task and capture phase"
                )
            task.metadata["verifier_snapshot"] = snapshot
        results = []
        for path in (directory / "evaluation").glob("*/result.json"):
            result = json.loads(path.read_text())
            if result.get("task_name") == task_dir.name:
                results.append((path, result))
        if len(results) != 1:
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
            raise RuntimeError(f"Harbor trial failed; evidence: {path}")
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
