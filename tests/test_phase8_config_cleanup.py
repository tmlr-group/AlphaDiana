from __future__ import annotations

import signal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from alphadiana.cli import main
from alphadiana.config.experiment_config import ExperimentConfig, parse_override
from alphadiana.config.validator import ConfigValidator
from alphadiana.runner.runner import Runner


def _write_config(tmp_path: Path) -> Path:
    path = tmp_path / "phase8-config.yaml"
    path.write_text(
        (
            'run_id: "phase8"\n'
            "agent:\n"
            '  name: "direct_llm"\n'
            '  version: "2026.4.21"\n'
            "  config:\n"
            '    model: "gpt-test"\n'
            '    api_base: "http://127.0.0.1:8080/v1"\n'
            "benchmark:\n"
            '  name: "aime"\n'
            "  config:\n"
            '    dataset: "demo/aime"\n'
            "scorer:\n"
            '  name: "numeric"\n'
        ),
        encoding="utf-8",
    )
    return path


def test_batch_runs_config_validator(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    calls: list[str] = []

    def _validate(self, config):
        calls.append(config.run_id)
        return []

    class _FakeBatchRunner:
        def __init__(self, configs, parallel=False):
            self.configs = configs

        def run(self):
            return [
                SimpleNamespace(run_id=cfg.run_id, accuracy=1.0)
                for cfg in self.configs
            ]

    monkeypatch.setattr("alphadiana.config.validator.ConfigValidator.validate", _validate)
    monkeypatch.setattr("alphadiana.runner.batch_runner.BatchRunner", _FakeBatchRunner)

    result = CliRunner().invoke(main, ["batch", str(config_path)])

    assert result.exit_code == 0, result.output
    assert calls == ["phase8"]


def test_runner_setup_cleans_up_on_scorer_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    config = ExperimentConfig(
        run_id="phase8-runner",
        agent_name="agent",
        agent_version="2026.4.21",
        benchmark_name="bench",
        scorer_name="scorer",
        output_dir="/tmp/phase8-runner",
    )
    runner = Runner(config)

    class _FakeBenchmark:
        pass

    class _FakeAgent:
        def setup(self, _config):
            return None

    class _FakeScorer:
        def setup(self, _config):
            raise RuntimeError("scorer boom")

    monkeypatch.setattr("alphadiana.runner.runner.BenchmarkRegistry.get", lambda _name: _FakeBenchmark)
    monkeypatch.setattr("alphadiana.runner.runner.AgentRegistry.get", lambda _name: _FakeAgent)
    monkeypatch.setattr("alphadiana.runner.runner.ScorerRegistry.get", lambda _name: _FakeScorer)

    teardown_calls: list[str] = []
    monkeypatch.setattr(runner, "teardown", lambda: teardown_calls.append("called"))

    with pytest.raises(RuntimeError, match="scorer boom"):
        runner.setup()

    assert teardown_calls == ["called"]


def test_openclaw_teardown_stops_gateway() -> None:
    from alphadiana.agent.openclaw_container_runtime import OpenClawContainerRuntimeManager

    sandbox = MagicMock()
    runtime = OpenClawContainerRuntimeManager({"openclaw_config_path": "openclaw_deploy/openclaw.json"})
    runtime._managed_sandboxes = {"sbx": sandbox}
    runtime._gateway_pidfile = "/tmp/openclaw-gateway.pid"

    runtime.teardown()

    sandbox.execute.assert_called_once()
    assert "openclaw-gateway.pid" in sandbox.execute.call_args.args[0]


def test_zeroclaw_teardown_stops_bridge() -> None:
    from alphadiana.agent.zeroclaw_runtime import ZeroClawRuntimeManager

    sandbox = MagicMock()
    runtime = ZeroClawRuntimeManager({})
    runtime._managed_sandboxes = {"sbx": sandbox}
    runtime._bridge_pidfile = "/tmp/zeroclaw-bridge.pid"

    runtime.teardown()

    sandbox.execute.assert_called_once()
    assert "zeroclaw-bridge.pid" in sandbox.execute.call_args.args[0]


def test_parse_override_rejects_malformed_key_paths() -> None:
    for value in ("", "=1", ".x=1", "x.=1", "a..b=1", ".=1"):
        with pytest.raises(ValueError):
            parse_override(value)


def test_parse_override_accepts_normal_path() -> None:
    assert parse_override("agent.model=gpt-4") == {"agent": {"model": "gpt-4"}}


def test_from_yaml_bad_top_level_not_dict(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("- just\n- a\n- list\n", encoding="utf-8")

    with pytest.raises(ValueError, match="top-level YAML document must be a mapping"):
        ExperimentConfig.from_yaml(str(path))


def test_validator_numeric_guard_rejects_string() -> None:
    validator = ConfigValidator()
    config = ExperimentConfig(
        agent_name="direct_llm",
        agent_version="2026.4.21",
        benchmark_name="aime",
        scorer_name="numeric",
        agent_config={"model": "gpt-test", "api_base": "http://127.0.0.1:8080/v1"},
    )
    config.max_concurrent = "hot"  # type: ignore[assignment]

    errors = validator.validate(config)

    assert any("max_concurrent" in error and "integer" in error for error in errors)


def test_validator_direct_llm_requires_model() -> None:
    validator = ConfigValidator()
    config = ExperimentConfig(
        agent_name="direct_llm",
        agent_version="2026.4.21",
        benchmark_name="aime",
        scorer_name="numeric",
        agent_config={"api_base": "http://127.0.0.1:8080/v1"},
    )

    errors = validator.validate(config)

    assert any("model" in error for error in errors)


def test_validator_direct_llm_rejects_empty_model() -> None:
    validator = ConfigValidator()
    config = ExperimentConfig(
        agent_name="direct_llm",
        agent_version="2026.4.21",
        benchmark_name="aime",
        scorer_name="numeric",
        agent_config={"model": "   ", "api_base": "http://127.0.0.1:8080/v1"},
    )

    errors = validator.validate(config)

    assert any("model" in error for error in errors)
