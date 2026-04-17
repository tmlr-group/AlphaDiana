from __future__ import annotations

from types import SimpleNamespace

from click.testing import CliRunner

from alphadiana.cli import main


def _write_openclaw_config(tmp_path, *, api_base: str | None) -> str:
    api_base_block = ""
    if api_base is not None:
        api_base_block = f'    api_base: "{api_base}"\n'

    config_path = tmp_path / "openclaw-cli.yaml"
    config_path.write_text(
        (
            "run_id: \"cli-openclaw-test\"\n"
            "agent:\n"
            "  name: openclaw\n"
            "  version: \"2026.3.20\"\n"
            "  config:\n"
            f"{api_base_block}"
            "    rock_agent_config_path: \"openclaw_deploy/rock_agent_config.prebuilt.yaml\"\n"
            "    openclaw_config_path: \"openclaw_deploy/openclaw.json\"\n"
            "benchmark:\n"
            "  name: imo_answerbench\n"
            "scorer:\n"
            "  name: math_verify\n"
        ),
        encoding="utf-8",
    )
    return str(config_path)


def _patch_runner(monkeypatch) -> None:
    monkeypatch.setattr(
        "alphadiana.config.validator.ConfigValidator.validate",
        lambda self, config: [],
    )
    monkeypatch.setattr("alphadiana.runner.runner.Runner.setup", lambda self: None)
    monkeypatch.setattr("alphadiana.runner.runner.Runner.teardown", lambda self: None)
    monkeypatch.setattr(
        "alphadiana.runner.runner.Runner.run",
        lambda self: SimpleNamespace(
            run_id=self.config.run_id,
            accuracy=1.0,
            mean_score=1.0,
            pass_at_k=1.0,
            avg_at_k=1.0,
            completed=1,
            total_tasks=1,
            num_samples=1,
        ),
    )


def test_run_skips_rock_preflight_for_openclaw_direct_gateway(monkeypatch, tmp_path) -> None:
    _patch_runner(monkeypatch)
    config_path = _write_openclaw_config(tmp_path, api_base="http://127.0.0.1:18083/v1")

    check_called = False

    def _fail_if_called(*args, **kwargs):
        nonlocal check_called
        check_called = True
        raise AssertionError("ROCK pre-flight should be skipped for direct openclaw gateway runs")

    monkeypatch.setattr("alphadiana.utils.rock_ports.check_rock_services", _fail_if_called)

    result = CliRunner().invoke(main, ["run", config_path])

    assert result.exit_code == 0, result.output
    assert check_called is False
    assert "Run completed: cli-openclaw-test" in result.output


def test_run_keeps_rock_preflight_for_openclaw_autodeploy(monkeypatch, tmp_path) -> None:
    _patch_runner(monkeypatch)
    config_path = _write_openclaw_config(tmp_path, api_base=None)

    check_called = False

    def _check_services(*args, **kwargs):
        nonlocal check_called
        check_called = True
        return {"admin": True, "proxy": True, "redis": True, "docker": True}

    monkeypatch.setattr("alphadiana.utils.rock_ports.check_rock_services", _check_services)
    monkeypatch.setattr(
        "alphadiana.utils.rock_ports.resolve_rock_ports_from_env",
        lambda: SimpleNamespace(admin_port=9000, proxy_port=9005, redis_port=6383),
    )

    result = CliRunner().invoke(main, ["run", config_path])

    assert result.exit_code == 0, result.output
    assert check_called is True
    assert "Pre-flight passed: admin" in result.output
