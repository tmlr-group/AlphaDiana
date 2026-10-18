"""CLI startup and ROCK preflight routing without external services."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml
from click.testing import CliRunner

from alphadiana.cli import main
from alphadiana.engine.runner import Runner
from alphadiana.engine.sandbox import rock_ports


def test_run_reaches_config_validation(tmp_path):
    path = tmp_path / "invalid.yaml"
    path.write_text("{}\n", encoding="utf-8")

    result = CliRunner().invoke(main, ["run", str(path)])

    assert result.exit_code == 1
    assert "Config validation failed:" in result.output
    assert "agent_name is required" in result.output


@pytest.mark.parametrize(
    "agent,sandbox,settings,expects_rock",
    [
        pytest.param("direct_llm", "", {}, False, id="ordinary"),
        pytest.param("direct_llm", "rock", {}, True, id="explicit-rock"),
        pytest.param("openclaw", "", {}, True, id="autodeploy"),
        pytest.param("openclaw", "", {"api_base": "http://fixture"}, False, id="direct-gateway"),
        pytest.param("openclaw", "", {"gateway_pool": ["http://fixture"]}, False, id="gateway-pool"),
        pytest.param("openclaw", "", {"runtime_backend": "podman"}, False, id="podman"),
    ],
)
def test_run_routes_rock_preflight(tmp_path, monkeypatch, agent, sandbox, settings, expects_rock):
    agent_config = (
        {"rock_agent_config_path": "rock.yaml", "openclaw_config_path": "openclaw.json"}
        if agent == "openclaw"
        else {"api_base": "http://fixture", "model": "fixture"}
    )
    agent_config.update(settings)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({
        "run_id": "cli-fixture",
        "output_dir": str(tmp_path / "results"),
        "agent": {"name": agent, "version": "v1", "config": agent_config},
        "benchmark": {"name": "custom"},
        "scorer": {"name": "exact_match"},
        "sandbox": {"name": sandbox},
    }), encoding="utf-8")

    setup, teardown = Mock(), Mock()
    run = Mock(return_value=SimpleNamespace(
        run_id="cli-fixture", accuracy=1.0, mean_score=1.0,
        samples_independent=True, num_samples=1, pass_at_k=1.0, avg_at_k=1.0,
        completed=1, total_tasks=1, evaluation_metrics={}, strict_report_failed=False,
    ))
    monkeypatch.setattr(Runner, "setup", setup)
    monkeypatch.setattr(Runner, "run", run)
    monkeypatch.setattr(Runner, "teardown", teardown)
    monkeypatch.setattr(rock_ports, "resolve_rock_ports_from_env", lambda: SimpleNamespace(
        admin_port=1, proxy_port=2, redis_port=3,
    ))
    health = Mock(return_value={"admin": True, "proxy": True, "redis": True})
    ownership = Mock(return_value={"admin": True, "proxy": True, "redis": True})
    monkeypatch.setattr(rock_ports, "check_rock_services", health)
    monkeypatch.setattr(rock_ports, "check_rock_service_ownership", ownership)

    result = CliRunner().invoke(main, ["run", str(path)])

    assert result.exit_code == 0, (result.output, result.exception)
    assert "Run completed: cli-fixture" in result.output
    assert health.call_count == int(expects_rock)
    assert ownership.call_count == int(expects_rock)
    setup.assert_called_once()
    run.assert_called_once()
    teardown.assert_called_once()
