from __future__ import annotations

from pathlib import Path

from alphadiana.agent.terminal_bench2_common import TerminalBench2ContainerMixin
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.config.experiment_config import ExperimentConfig
from alphadiana.config.validator import ConfigValidator


class _MixinHarness(TerminalBench2ContainerMixin):
    pass


def test_logs_dir_isolated_by_sample_and_execution_id(tmp_path: Path) -> None:
    harness = _MixinHarness()
    harness._logs_base_dir = tmp_path
    task = BenchmarkTask(
        task_id="tb2_demo",
        problem="demo",
        ground_truth="1",
        metadata={"sample_index": 2, "execution_id": "abc123456789"},
    )

    logs_dir = harness._logs_dir_for_task(task)

    assert logs_dir == tmp_path / "tb2_demo_s2_abc12345"


def test_terminal_bench2_openclaw_validation_accepts_auto_deploy() -> None:
    config = ExperimentConfig(
        run_id="tb2-openclaw",
        agent_name="terminal_bench2_openclaw",
        agent_version="1.0",
        agent_config={
            "rock_agent_config_path": "openclaw_deploy/rock_agent_config.prebuilt.yaml",
            "openclaw_config_path": "openclaw_deploy/openclaw.json",
        },
        benchmark_name="terminal_bench2",
        benchmark_config={"tasks_dir": "/tmp/tb2"},
        scorer_name="terminal_bench2",
    )

    assert ConfigValidator().validate(config) == []


def test_terminal_bench2_docker_validation_requires_api_base() -> None:
    config = ExperimentConfig(
        run_id="tb2-direct",
        agent_name="terminal_bench2_docker",
        agent_version="1.0",
        agent_config={},
        benchmark_name="terminal_bench2",
        benchmark_config={"tasks_dir": str(Path("/tmp/tb2"))},
        scorer_name="terminal_bench2",
    )

    errors = ConfigValidator().validate(config)

    assert any("terminal_bench2_docker" in error and "api_base" in error for error in errors)
