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
from alphadiana.engine.components import register_agent


class SWEAgentRuntimeError(RuntimeError):
    """Native per-instance failure, even when run-batch exits successfully."""

    def __init__(self, response: AgentResponse, *, error_type: str):
        super().__init__(f"SWE-Agent {response.metadata['exit_status']}; evidence: {response.metadata['native_output_dir']}")
        self.error_type = error_type
        self.partial_response = response


def project_sweagent_history(history):
    """Use native message roles, not trajectory rows containing whole queries.

    SWE-Agent's trajectory rows mix decisions, observations and repeated prompt
    history. Its retained history provides the actual assistant/tool boundaries.
    """
    projected = []
    for message in history:
        role = message.get("role")
        if role not in {"system", "user", "assistant", "tool"}:
            raise ValueError(f"Unsupported SWE-Agent history role: {role!r}")
        content = message.get("content") or message.get("thought") or ""
        entry = {"role": role, "content": content}
        if role == "tool":
            entry["type"] = "tool_result"
            entry["tool_results"] = [
                {"tool_use_id": identity, "content": content}
                for identity in (message.get("tool_call_ids") or [""])
            ]
        calls = []
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            arguments = function.get("arguments", {})
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    # A malformed model call is still native evidence.
                    arguments = {"unparsed_arguments": arguments}
            calls.append(
                {
                    "id": call.get("id", ""),
                    "name": function.get("name", ""),
                    "input": arguments,
                }
            )
        if calls:
            entry.update(type="tool_use", tool_calls=calls)
        projected.append(entry)
    return projected


@register_agent("sweagent")
class SWEAgent(Agent):
    name = "sweagent"
    version = "1.1.0"

    def setup(self, config: dict) -> None:
        if "history_processors" in config and not isinstance(config["history_processors"], list):
            raise ValueError("history_processors must be a native configuration list")
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
        capture = NativeProviderCapture(self.config, directory, measure_usage=True)
        try:
            with capture:
                response = self._solve_native(task, directory, capture.api_base, started)
        except SWEAgentRuntimeError as exc:
            capture.apply(exc.partial_response)
            raise
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
        if "top_p" in self.config:
            # Upstream's dotted CLI parser preserves strings, so a second
            # config file is needed to pass an actual null instead of "null".
            sampling = directory / "model-sampling.json"
            sampling.write_text(json.dumps({"agent": {"model": {"top_p": self.config["top_p"]}}}))
            argv.extend(["--config", str(sampling)])
        if "history_processors" in self.config:
            # A typed empty list explicitly disables provider-specific cache marks.
            history = directory / "history-processors.json"
            history.write_text(json.dumps({"agent": {"history_processors": self.config["history_processors"]}}))
            argv.extend(["--config", str(history)])
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
        response = AgentResponse(
            answer=prediction["model_patch"],
            trajectory=project_sweagent_history(trajectory.get("history", [])),
            token_usage={},
            wall_time_sec=time.monotonic() - started,
            metadata={
                "native_runtime": "sweagent",
                "native_history_messages": len(trajectory.get("history", [])),
                "native_token_usage_estimate": usage,
                "native_usage_semantics": "SWE-Agent tokenizer estimate of message text; excludes tool-call output",
                "native_image": image,
                "native_output_dir": str(directory),
                "exit_status": (trajectory.get("info") or {}).get("exit_status"),
            },
            workspace_file_contents={
                "sweagent_trajectory.json": json.dumps(trajectory)
            },
            finish_reason="stop",
        )
        exit_status = response.metadata["exit_status"]
        if exit_status in {"exit_api", "exit_error", "exit_environment_error"}:
            raise SWEAgentRuntimeError(
                response,
                error_type="provider_error" if exit_status == "exit_api" else "runtime_error",
            )
        return response
