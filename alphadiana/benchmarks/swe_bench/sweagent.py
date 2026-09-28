"""Optional upstream SWE-agent adapter; native control loop stays upstream."""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from uuid import uuid4

from alphadiana.harness._internal.native_process import run_native
from alphadiana.harness.base import Agent, AgentResponse
from alphadiana.harness.proxies.native_capture import NativeProviderCapture
from alphadiana.harness.registry import register_agent


@register_agent("sweagent")
class SWEAgent(Agent):
    name = "sweagent"
    version = "1.1.0"

    def setup(self, config: dict) -> None:
        self.config = dict(config)
        self.executable = str(
            config.get("executable") or shutil.which("sweagent") or ""
        )
        self.cwd = Path(config.get("checkout", ".")).resolve()
        self.native_config = Path(
            config.get("config_path", self.cwd / "config/default.yaml")
        ).resolve()
        if not self.executable or not self.native_config.is_file():
            raise ValueError(
                "SWE-agent requires its installed executable and native config_path"
            )
        if not config.get("model") or not config.get("api_base"):
            raise ValueError("SWE-agent requires explicit model and api_base")
        self.output = Path(
            config.get("output_dir", "results/sweagent-native")
        ).resolve()
        self.output.mkdir(parents=True, exist_ok=True)

    def solve(self, task, sandbox=None) -> AgentResponse:
        if sandbox is not None:
            raise ValueError(
                "SWE-agent owns its native environment; omit external sandbox"
            )
        started = time.monotonic()
        directory = self.output / uuid4().hex
        directory.mkdir(mode=0o700)
        with NativeProviderCapture(self.config, directory) as capture:
            response = self._solve_native(task, directory, capture.api_base, started)
        return capture.apply(response)

    def _solve_native(self, task, directory, api_base, started) -> AgentResponse:
        metadata = task.metadata
        instance_id = str(
            metadata.get("instance_id") or task.task_id.removeprefix("swe_")
        )
        base_commit = metadata.get("base_commit")
        if not base_commit:
            raise ValueError("SWE-agent requires a benchmark task with base_commit")
        image = (
            self.config.get("image_overrides", {}).get(instance_id)
            or metadata.get("docker_image")
            or f"docker.io/swebench/sweb.eval.x86_64.{instance_id.replace('__', '_1776_')}:latest".lower()
        )
        # Select only solver-visible inputs; reference patches and tests never enter prompts.
        instance = {
            "instance_id": instance_id,
            "problem_statement": task.problem,
            "image_name": image,
            "repo_name": "testbed",
            "base_commit": base_commit,
        }
        instances = directory / "instances.json"
        instances.write_text(json.dumps([instance]))
        model = str(self.config["model"])
        model = model if model.startswith("openai/") else "openai/" + model
        argv = [
            self.executable,
            "run-batch",
            "--config",
            str(self.native_config),
            "--output_dir",
            str(directory / "run"),
            "--instances.type",
            "file",
            "--instances.path",
            str(instances),
            "--instances.deployment.container_runtime",
            str(self.config.get("container_runtime", "docker")),
            "--agent.model.name",
            model,
            "--agent.model.api_base",
            api_base,
            "--agent.model.per_instance_cost_limit",
            "0",
            "--agent.model.total_cost_limit",
            "0",
            "--agent.model.per_instance_call_limit",
            str(self.config.get("max_calls", 30)),
            "--agent.model.max_output_tokens",
            str(self.config.get("max_tokens", 8192)),
            "--agent.model.temperature",
            str(self.config.get("temperature", 0.0)),
            "--progress_bar",
            "False",
        ]
        if "python_standalone_dir" in self.config:
            argv.extend(
                [
                    "--instances.deployment.python_standalone_dir",
                    self.config["python_standalone_dir"],
                ]
            )
        if self.config.get("completion_kwargs"):
            argv.extend(
                [
                    "--agent.model.completion_kwargs",
                    json.dumps(self.config["completion_kwargs"]),
                ]
            )
        env = dict(os.environ, OPENAI_API_KEY=self.config.get("api_key", "EMPTY"))
        run_native(
            argv,
            cwd=self.cwd,
            env=env,
            log=directory / "native.log",
            timeout=float(self.config.get("timeout", 1800)),
        )
        predictions_path = directory / "run/preds.json"
        if not predictions_path.is_file():
            raise RuntimeError(
                f"SWE-agent produced no predictions; evidence: {directory / 'native.log'}"
            )
        predictions = json.loads(predictions_path.read_text())
        if isinstance(predictions, dict):
            prediction = predictions.get(instance_id)
        else:
            prediction = next(
                (p for p in predictions if p.get("instance_id") == instance_id), None
            )
        if not isinstance(prediction, dict) or "model_patch" not in prediction:
            raise RuntimeError(
                "SWE-agent did not produce the requested instance prediction"
            )
        trajectory_file = directory / "run" / instance_id / f"{instance_id}.traj"
        trajectory = (
            json.loads(trajectory_file.read_text()) if trajectory_file.exists() else {}
        )
        stats = (trajectory.get("info") or {}).get("model_stats") or {}
        usage = {
            target: stats[source]
            for source, target in (
                ("tokens_sent", "prompt_tokens"),
                ("tokens_received", "completion_tokens"),
            )
            if source in stats
        }
        return AgentResponse(
            answer=prediction["model_patch"],
            trajectory=trajectory.get("trajectory", []),
            token_usage=usage,
            wall_time_sec=time.monotonic() - started,
            metadata={
                "native_runtime": "sweagent",
                "native_image": image,
                "native_output_dir": str(directory),
                "exit_status": (trajectory.get("info") or {}).get("exit_status"),
            },
            workspace_file_contents={
                "sweagent_trajectory.json": json.dumps(trajectory)
            },
            finish_reason="stop",
        )
