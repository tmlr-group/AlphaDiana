from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from alphadiana.agent.terminal_bench2_common import (
    LocalCommandResult,
    NATIVE_AGENT_PROMPT,
    TerminalBench2ContainerMixin,
    TerminalBench2RuntimeContext,
)
from alphadiana.agent.terminal_bench2_opencode import TerminalBench2OpenCodeAgent
from alphadiana.agent.terminal_bench2_openclaw import TerminalBench2OpenClawAgent
from alphadiana.agent.terminal_bench2_zeroclaw import TerminalBench2ZeroClawAgent
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


def test_terminal_bench2_helper_workspace_uses_control_side_helpers(tmp_path: Path) -> None:
    harness = _MixinHarness()
    task = BenchmarkTask(task_id="tb2_demo", problem="demo task", ground_truth="1")

    helper_paths = harness._write_helper_scripts(
        tmp_path,
        "demo-container",
        task,
        prompt_text="demo prompt",
    )

    assert helper_paths["task"].read_text(encoding="utf-8") == "demo task"
    assert not helper_paths["task_hints"].exists()
    assert "docker exec demo-container bash -lc" in helper_paths["exec"].read_text(encoding="utf-8")
    assert "docker cp demo-container:" in helper_paths["copy_from"].read_text(encoding="utf-8")
    assert "docker cp \"$1\" demo-container:" in helper_paths["copy_to"].read_text(encoding="utf-8")
    assert "bash /tests/test.sh" in helper_paths["test"].read_text(encoding="utf-8")
    assert helper_paths["prompt"].read_text(encoding="utf-8") == "demo prompt"
    assert "Do not call `docker` directly" in helper_paths["agents"].read_text(encoding="utf-8")
    assert "do not call it during normal solving" in NATIVE_AGENT_PROMPT
    assert "manual debugging" in helper_paths["agents"].read_text(encoding="utf-8")


def test_terminal_bench2_db_wal_task_note_mentions_workspace_snapshot(tmp_path: Path) -> None:
    harness = _MixinHarness()
    task = BenchmarkTask(
        task_id="tb2_db-wal-recovery",
        problem="recover sqlite wal",
        ground_truth="1",
        metadata={"task_dir": "/tmp/terminal-bench-2/db-wal-recovery"},
    )
    for prefix in ("app", "original"):
        snapshot_dir = tmp_path / "bootstrap" / prefix
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        (snapshot_dir / "main.db").write_text("db", encoding="utf-8")
        (snapshot_dir / "main.db-wal").write_text("wal", encoding="utf-8")

    note = harness._task_runtime_note(task, tmp_path)
    helper_paths = harness._write_helper_scripts(
        tmp_path,
        "demo-container",
        task,
        task_note=note,
    )

    assert "./bootstrap/app/main.db" in note
    assert "./bootstrap/app/main.db-wal" in note
    assert "./bootstrap/original/main.db" in note
    assert "./bootstrap/original/main.db-wal" in note
    assert "sqlite3 \"file:/app/main.db?mode=ro\"" in note
    assert helper_paths["task_hints"].exists()
    assert "Task-specific guidance:" in helper_paths["task_hints"].read_text(encoding="utf-8")
    assert "--- Task Hints ---" in helper_paths["task"].read_text(encoding="utf-8")


def test_terminal_bench2_native_agents_disable_manual_test_helper(tmp_path: Path) -> None:
    harness = _MixinHarness()
    task = BenchmarkTask(task_id="tb2_demo", problem="demo task", ground_truth="1")

    helper_paths = harness._write_helper_scripts(
        tmp_path,
        "demo-container",
        task,
        prompt_text="demo prompt",
    )
    harness._disable_test_helper(helper_paths)

    helper_text = helper_paths["test"].read_text(encoding="utf-8")
    assert "tb2-test is disabled for native agents" in helper_text
    assert "harness will run verification" in helper_text


def test_terminal_bench2_openclaw_validation_requires_api_base() -> None:
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

    errors = ConfigValidator().validate(config)

    assert any("terminal_bench2_openclaw" in error and "api_base" in error for error in errors)


def test_terminal_bench2_zeroclaw_validation_requires_api_base() -> None:
    config = ExperimentConfig(
        run_id="tb2-zeroclaw",
        agent_name="terminal_bench2_zeroclaw",
        agent_version="0.6.9",
        agent_config={},
        benchmark_name="terminal_bench2",
        benchmark_config={"tasks_dir": "/tmp/tb2"},
        scorer_name="terminal_bench2",
    )

    errors = ConfigValidator().validate(config)

    assert any("terminal_bench2_zeroclaw" in error and "api_base" in error for error in errors)


def test_terminal_bench2_zeroclaw_config_reads_openai_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_MODEL_NAME", "minimax-m2.5")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")

    yaml_path = tmp_path / "tb2_zeroclaw.yaml"
    yaml_path.write_text(
        """
agent:
  name: terminal_bench2_zeroclaw
  version: "0.6.9"
  config: {}
benchmark:
  name: terminal_bench2
  config:
    tasks_dir: /tmp/tb2
scorer:
  name: terminal_bench2
""".strip(),
        encoding="utf-8",
    )

    config = ExperimentConfig.from_yaml(str(yaml_path))

    assert config.agent_config["model"] == "minimax-m2.5"
    assert config.agent_config["model_name"] == "minimax-m2.5"
    assert config.agent_config["api_base"] == "http://localhost/v1"
    assert config.agent_config["api_key"] == "secret"


def test_terminal_bench2_openclaw_builds_local_cli_env(tmp_path: Path) -> None:
    agent = TerminalBench2OpenClawAgent()
    agent.setup({
        "api_base": "http://localhost/v1",
        "api_key": "secret",
        "model_name": "minimax-m2.5",
    })

    assert agent._controller_mode == "docker"
    assert agent._controller_image == "alphadiana/tb2-openclaw-controller:latest"
    assert agent._verbose == "on"

    env, openclaw_home = agent._build_env(tmp_path)

    assert env["OPENCLAW_HOME"] == str(openclaw_home)
    assert env["OPENCLAW_BUNDLED_PLUGINS_DIR"].startswith(str(tmp_path))
    assert env["OPENAI_BASE_URL"] == "http://localhost/v1"
    assert env["OPENAI_MODEL_NAME"] == "minimax-m2.5"
    assert env["CUSTOM_API_KEY"] == "secret"


def test_terminal_bench2_openclaw_onboard_uses_noninteractive_local_mode(tmp_path: Path) -> None:
    agent = TerminalBench2OpenClawAgent()
    agent.setup({
        "api_base": "http://localhost/v1",
        "api_key": "secret",
        "model_name": "minimax-m2.5",
        "openclaw_bin": "openclaw",
    })

    with patch.object(
        agent,
        "_run_controller_process",
        return_value=LocalCommandResult(stdout="{}", stderr="", returncode=0),
    ) as mock_run:
        agent._run_onboard(tmp_path, {"OPENAI_API_KEY": "secret"})

    cmd = mock_run.call_args.args[0]
    assert cmd[:4] == ["openclaw", "onboard", "--non-interactive", "--flow"]
    assert "--mode" in cmd and "local" in cmd
    assert "--workspace" in cmd and str(tmp_path) in cmd
    assert "--custom-base-url" in cmd and "http://localhost/v1" in cmd
    assert "--custom-model-id" in cmd and "minimax-m2.5" in cmd
    assert "--skip-health" in cmd


def test_terminal_bench2_openclaw_agent_command_uses_local_json_mode(tmp_path: Path) -> None:
    agent = TerminalBench2OpenClawAgent()
    agent.setup({
        "api_base": "http://localhost/v1",
        "api_key": "secret",
        "model_name": "minimax-m2.5",
        "openclaw_bin": "openclaw",
        "thinking": "high",
        "verbose": "debug",
        "solver_timeout_sec": 321,
    })

    with patch.object(
        agent,
        "_run_controller_process",
        return_value=LocalCommandResult(stdout='{"ok":true}', stderr="", returncode=0),
    ) as mock_run:
        agent._run_openclaw_agent(tmp_path, {"OPENAI_API_KEY": "secret"}, "demo prompt")

    cmd = mock_run.call_args.args[0]
    assert cmd[:2] == ["openclaw", "agent"]
    assert "--local" in cmd
    assert "--json" in cmd
    assert "--message" in cmd and "demo prompt" in cmd
    assert "--timeout" in cmd and "321" in cmd
    assert "--thinking" in cmd and "high" in cmd
    assert "--verbose" in cmd and "full" in cmd


def test_terminal_bench2_opencode_writes_native_prompt_and_provider_config(tmp_path: Path) -> None:
    agent = TerminalBench2OpenCodeAgent()
    agent.setup({
        "api_base": "http://localhost/v1",
        "api_key": "secret",
        "model_name": "minimax-m2.5",
        "model": "custom/minimax-m2.5",
        "opencode_bin": "opencode",
    })
    assert agent._controller_mode == "docker"
    assert agent._controller_image == "alphadiana/tb2-opencode-controller:latest"
    (tmp_path / "PROMPT.txt").write_text(f"{NATIVE_AGENT_PROMPT}\n\n--- Task ---\ndemo\n", encoding="utf-8")

    with patch.object(
        agent,
        "_run_controller_process",
        return_value=LocalCommandResult(stdout="", stderr="", returncode=0),
    ) as mock_run:
        agent._run_opencode(tmp_path, "tb2_demo")

    config_path = tmp_path / "xdg-config" / "opencode" / "opencode.json"
    payload = config_path.read_text(encoding="utf-8")
    cmd = mock_run.call_args.args[0]
    env = mock_run.call_args.kwargs["env"]

    assert '"tool_call": true' in payload
    assert '"baseURL": "http://localhost/v1"' in payload
    assert cmd[:5] == ["node", "/usr/lib/node_modules/opencode-ai/bin/opencode", "run", "--format", "json"]
    assert "--title" in cmd and "tb2_demo" in cmd
    assert env["XDG_CONFIG_HOME"] == str(tmp_path / "xdg-config")


def test_terminal_bench2_zeroclaw_writes_native_prompt_and_config(tmp_path: Path) -> None:
    agent = TerminalBench2ZeroClawAgent()
    agent.setup({
        "api_base": "http://localhost/v1",
        "api_key": "secret",
        "model": "minimax-m2.5",
    })

    assert agent._controller_mode == "docker"
    assert agent._controller_image == "alphadiana/tb2-zeroclaw-controller:latest"
    assert agent._system_prompt == NATIVE_AGENT_PROMPT.strip()

    env, paths = agent._prepare_zeroclaw_home(tmp_path, f"{NATIVE_AGENT_PROMPT}\n\n--- Task ---\ndemo\n")

    config_text = (tmp_path / ".zeroclaw-home" / ".zeroclaw" / "config.toml").read_text(encoding="utf-8")
    config_mode = (tmp_path / ".zeroclaw-home" / ".zeroclaw" / "config.toml").stat().st_mode & 0o777
    assert env["OPENAI_BASE_URL"] == "http://localhost/v1"
    assert env["OPENAI_MODEL_NAME"] == "minimax-m2.5"
    assert 'default_model = "minimax-m2.5"' in config_text
    assert 'default_provider = "custom:http://localhost/v1"' in config_text
    assert config_mode == 0o600
    assert Path(paths["prompt_path"]).read_text(encoding="utf-8").endswith("--- Task ---\ndemo\n")


def test_terminal_bench2_zeroclaw_agent_command_uses_local_prompt_file(tmp_path: Path) -> None:
    agent = TerminalBench2ZeroClawAgent()
    agent.setup({
        "api_base": "http://localhost/v1",
        "api_key": "secret",
        "model": "minimax-m2.5",
        "zeroclaw_bin": "zeroclaw",
        "solver_timeout_sec": 321,
    })
    env, paths = agent._prepare_zeroclaw_home(tmp_path, "demo prompt")

    with patch.object(
        agent,
        "_run_controller_process",
        return_value=LocalCommandResult(stdout="", stderr="", returncode=0),
    ) as mock_run:
        agent._run_zeroclaw(tmp_path, env, paths)

    cmd = mock_run.call_args.args[0]
    assert cmd[:2] == ["bash", "-lc"]
    assert "prompt=$(cat " in cmd[2]
    assert "zeroclaw agent -m" in cmd[2]
    assert "zeroclaw_output.txt" in cmd[2]


def test_terminal_bench2_opencode_host_mode_keeps_configured_binary() -> None:
    agent = TerminalBench2OpenCodeAgent()
    agent.setup({
        "api_base": "http://localhost/v1",
        "api_key": "secret",
        "model_name": "minimax-m2.5",
        "controller_mode": "host",
        "opencode_bin": "opencode --fast",
    })

    assert agent._opencode_command_prefix() == ["opencode", "--fast"]


def test_terminal_bench2_controller_process_wraps_command_in_docker_run(tmp_path: Path) -> None:
    harness = _MixinHarness()
    harness._setup_controller_config(
        {
            "controller_mode": "docker",
            "controller_image": "alphadiana/tb2-opencode-controller:latest",
        },
        default_mode="docker",
        default_image="alphadiana/tb2-opencode-controller:latest",
    )

    with patch.object(
        harness,
        "_run_local_process",
        return_value=LocalCommandResult(stdout="", stderr="", returncode=0),
    ) as mock_run:
        harness._run_controller_process(
            ["opencode", "run", "demo"],
            cwd=tmp_path,
            env={"OPENAI_API_KEY": "secret"},
            timeout_sec=30,
        )

    docker_cmd = mock_run.call_args.args[0]
    assert docker_cmd[:3] == ["docker", "run", "--rm"]
    assert "--network" in docker_cmd and "host" in docker_cmd
    assert "/var/run/docker.sock:/var/run/docker.sock" in docker_cmd
    assert f"{tmp_path}:{tmp_path}" in docker_cmd
    assert "--entrypoint" in docker_cmd and "/bin/bash" in docker_cmd
    assert "alphadiana/tb2-opencode-controller:latest" in docker_cmd
    assert docker_cmd[-2:] == ["-lc", "opencode run demo"]


def test_terminal_bench2_controller_process_preserves_explicit_home_and_tmpdir(tmp_path: Path) -> None:
    harness = _MixinHarness()
    harness._setup_controller_config(
        {
            "controller_mode": "docker",
            "controller_image": "alphadiana/tb2-zeroclaw-controller:latest",
        },
        default_mode="docker",
        default_image="alphadiana/tb2-zeroclaw-controller:latest",
    )

    explicit_home = tmp_path / ".zeroclaw-home"
    explicit_tmp = tmp_path / ".zeroclaw-tmp"
    explicit_cache = tmp_path / ".zeroclaw-cache"

    with patch.object(
        harness,
        "_run_local_process",
        return_value=LocalCommandResult(stdout="", stderr="", returncode=0),
    ) as mock_run:
        harness._run_controller_process(
            ["zeroclaw", "agent", "-m", "demo"],
            cwd=tmp_path,
            env={
                "HOME": str(explicit_home),
                "TMPDIR": str(explicit_tmp),
                "XDG_CACHE_HOME": str(explicit_cache),
                "OPENAI_API_KEY": "secret",
            },
            timeout_sec=30,
        )

    docker_cmd = mock_run.call_args.args[0]
    assert "-e" in docker_cmd
    assert f"HOME={explicit_home}" in docker_cmd
    assert f"TMPDIR={explicit_tmp}" in docker_cmd
    assert f"TMP={explicit_tmp}" in docker_cmd
    assert f"TEMP={explicit_tmp}" in docker_cmd
    assert f"XDG_CACHE_HOME={explicit_cache}" in docker_cmd


def test_terminal_bench2_verifier_reuses_existing_reward(tmp_path: Path) -> None:
    harness = _MixinHarness()
    task = BenchmarkTask(task_id="tb2_demo", problem="demo", ground_truth="1")
    reward_dir = tmp_path / "verifier"
    reward_dir.mkdir(parents=True)
    (reward_dir / "reward.txt").write_text("1", encoding="utf-8")
    tempdir = tempfile.TemporaryDirectory(prefix="tb2-test-")
    runtime = TerminalBench2RuntimeContext(
        task=task,
        docker_image="demo:latest",
        container_id="demo-container",
        logs_dir=tmp_path,
        workdir=Path(tempdir.name),
        helper_paths={},
        _tempdir=tempdir,
    )
    try:
        with patch.object(harness, "_run_tests") as mock_run:
            test_output, reward = harness._run_verifier_and_read_reward(runtime, timeout_sec=30)
    finally:
        runtime.cleanup()

    assert reward == "1"
    assert "reused existing verifier reward.txt" in test_output
    mock_run.assert_not_called()


def test_terminal_bench2_verifier_waits_for_existing_run_to_finish(tmp_path: Path) -> None:
    harness = _MixinHarness()
    task = BenchmarkTask(task_id="tb2_demo", problem="demo", ground_truth="1")
    reward_dir = tmp_path / "verifier"
    reward_dir.mkdir(parents=True)
    reward_path = reward_dir / "reward.txt"
    tempdir = tempfile.TemporaryDirectory(prefix="tb2-test-")
    runtime = TerminalBench2RuntimeContext(
        task=task,
        docker_image="demo:latest",
        container_id="demo-container",
        logs_dir=tmp_path,
        workdir=Path(tempdir.name),
        helper_paths={},
        _tempdir=tempdir,
    )

    def _write_reward_after_wait(_: float) -> None:
        reward_path.write_text("1", encoding="utf-8")

    try:
        with patch.object(harness, "_has_running_verifier", return_value=True), patch.object(
            harness,
            "_run_tests",
        ) as mock_run, patch(
            "alphadiana.agent.terminal_bench2_common.time.sleep",
            side_effect=_write_reward_after_wait,
        ):
            test_output, reward = harness._run_verifier_and_read_reward(runtime, timeout_sec=5)
    finally:
        runtime.cleanup()

    assert reward == "1"
    assert "existing verifier" in test_output
    mock_run.assert_not_called()


def test_terminal_bench2_verifier_probe_skips_its_own_shell() -> None:
    harness = _MixinHarness()

    with patch("alphadiana.agent.terminal_bench2_common.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = ""
        assert harness._has_running_verifier("demo-container") is False

    probe = mock_run.call_args.args[0][-1]
    assert 'me="$$"' in probe
    assert '[ "${pid##*/}" = "$me" ] && continue' in probe


def test_terminal_bench2_verifier_bootstraps_uvx_shim() -> None:
    harness = _MixinHarness()

    with patch("alphadiana.agent.terminal_bench2_common.subprocess.run") as mock_run:
        harness._ensure_verifier_tooling("demo-container")

    bootstrap = mock_run.call_args.args[0][-1]
    assert '/root/.local/bin/env' in bootstrap
    assert '/usr/local/bin/uvx' in bootstrap
    assert '--break-system-packages' in bootstrap
    assert 'python3 -m pytest' in bootstrap


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
