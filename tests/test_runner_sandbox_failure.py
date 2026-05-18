from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from alphadiana.agent.base import Agent, AgentResponse
from alphadiana.agent.registry import AgentRegistry
from alphadiana.benchmark.base import Benchmark, BenchmarkTask
from alphadiana.benchmark.registry import BenchmarkRegistry
from alphadiana.config.experiment_config import ExperimentConfig
from alphadiana.runner.runner import Runner
from alphadiana.sandbox.base import Sandbox
from alphadiana.sandbox.registry import SandboxRegistry
from alphadiana.scorer.base import ScoreResult, Scorer
from alphadiana.scorer.registry import ScorerRegistry


class _OneTaskBenchmark(Benchmark):
    name = "phase9_fake_benchmark"

    def load_tasks(self, config: dict) -> list[BenchmarkTask]:
        return [
            BenchmarkTask(
                task_id="phase9_task",
                problem="fix it",
                ground_truth="diff --git a/x b/x",
                metadata={"instance_id": "demo__demo-1"},
            )
        ]


class _UnusedAgent(Agent):
    name = "phase9_unused_agent"

    def setup(self, config: dict) -> None:
        self.config = dict(config)

    def solve(self, task: BenchmarkTask, sandbox: Any = None) -> AgentResponse:
        raise AssertionError("agent should not run after sandbox creation failure")


class _FailingSandbox(Sandbox):
    @property
    def name(self) -> str:
        return "phase9_failing_sandbox"

    def setup(self, config: dict) -> None:
        self.config = dict(config)

    def supports_shared_session(self) -> bool:
        return False

    def create_session(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("instance image build failed")


class _UnusedScorer(Scorer):
    @property
    def name(self) -> str:
        return "phase9_unused_scorer"

    def score(self, task: BenchmarkTask, response: AgentResponse) -> ScoreResult:
        raise AssertionError("scorer should not run after sandbox creation failure")


def test_runner_writes_task_json_when_sandbox_create_fails(tmp_path: Path) -> None:
    BenchmarkRegistry.register(_OneTaskBenchmark.name, _OneTaskBenchmark)
    AgentRegistry.register(_UnusedAgent.name, _UnusedAgent)
    SandboxRegistry.register(_FailingSandbox().name, _FailingSandbox)
    ScorerRegistry.register(_UnusedScorer().name, _UnusedScorer)

    config = ExperimentConfig(
        run_id="phase9_sandbox_failure",
        agent_name=_UnusedAgent.name,
        agent_version="test",
        benchmark_name=_OneTaskBenchmark.name,
        scorer_name=_UnusedScorer().name,
        sandbox_name=_FailingSandbox().name,
        sandbox_config={
            "container_engine": "podman",
            "podman_socket": "/tmp/podman.sock",
            "network_mode": "host",
        },
        output_dir=str(tmp_path / "results"),
        redo_all=True,
    )

    runner = Runner(config)
    try:
        runner.setup()
        runner.run()
    finally:
        runner.teardown()

    task_json = (
        tmp_path
        / "results"
        / "phase9_sandbox_failure"
        / "tasks"
        / "phase9_task.json"
    )
    data = json.loads(task_json.read_text(encoding="utf-8"))
    record = data[0]

    assert record["score_status"] == "runtime_error"
    assert record["metadata"]["sandbox_create_failed"] is True
    assert record["metadata"]["failure_stage"] == "sandbox_create"
    assert record["metadata"]["container_engine"] == "podman"
    assert record["sandbox_metadata"]["container_engine"] == "podman"
    assert record["sandbox_metadata"]["docker_api_version"] == "1.41"
    assert record["sandbox_metadata"]["podman_socket"] == "unix:///tmp/podman.sock"
    assert record["artifact_manifest"]["files"]["sandbox_metadata"].endswith("sandbox_meta.json")
