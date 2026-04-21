from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from alphadiana.agent.terminal_bench2_common import (
    LocalCommandResult,
    NATIVE_AGENT_PROMPT,
    TerminalBench2ContainerMixin,
    TerminalBench2RuntimeContext,
    VerifierResult,
)
from alphadiana.agent.terminal_bench2_incontainer import (
    IN_CONTAINER_AGENT_PROMPT,
    TerminalBench2InContainerMixin,
)
from alphadiana.agent.terminal_bench2_opencode import TerminalBench2OpenCodeAgent
from alphadiana.agent.terminal_bench2_openclaw import TerminalBench2OpenClawAgent
from alphadiana.agent.terminal_bench2_zeroclaw import (
    TerminalBench2ZeroClawAgent,
    _ZEROCLAW_SYSTEM_PROMPT,
)
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.config.experiment_config import ExperimentConfig
from alphadiana.config.validator import ConfigValidator
from alphadiana.results.status import infer_score_status, is_valid_completed_record
from alphadiana.utils.rock_runtime import PREBUILT_SANDBOX_IMAGE


class _MixinHarness(TerminalBench2ContainerMixin):
    pass


class _InContainerHarness(TerminalBench2InContainerMixin):
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


def test_terminal_bench2_incontainer_db_wal_task_note_mentions_live_paths() -> None:
    harness = _InContainerHarness()
    task = BenchmarkTask(
        task_id="tb2_db-wal-recovery",
        problem="recover sqlite wal",
        ground_truth="1",
        metadata={"task_dir": "/tmp/terminal-bench-2/db-wal-recovery"},
    )

    note = harness._build_incontainer_task_note(task)

    assert "/app/main.db" in note
    assert "/app/main.db-wal" in note
    assert "/app/recovered.json" in note
    assert "bootstrap" not in note


def test_terminal_bench2_incontainer_workspace_detection_prefers_app_from_root() -> None:
    harness = _InContainerHarness()

    with patch.object(
        harness,
        "_docker_exec_capture",
        return_value=LocalCommandResult(stdout="/\n", stderr="", returncode=0),
    ), patch.object(harness, "_docker_path_exists", return_value=True):
        workspace = harness._detect_container_workspace("demo-container")

    assert workspace == "/app"


def test_terminal_bench2_incontainer_zeroclaw_overlay_uses_direct_binary_copy() -> None:
    dockerfile = _InContainerHarness._build_runtime_overlay_dockerfile("zeroclaw")

    assert "COPY --from=runtime /usr/local/bin/zeroclaw /usr/local/bin/zeroclaw" in dockerfile
    assert "ld-linux-x86-64.so.2" not in dockerfile


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


def test_terminal_bench2_openclaw_builds_local_cli_env() -> None:
    agent = TerminalBench2OpenClawAgent()
    agent.setup({
        "api_base": "http://localhost/v1",
        "api_key": "secret",
        "model_name": "minimax-m2.5",
    })

    assert agent._runtime_source_image == PREBUILT_SANDBOX_IMAGE
    assert agent._verbose == "on"
    assert agent._min_context_window == 32768

    env = agent._build_env("/tmp/openclaw-home", "/tmp/openclaw-plugins")

    assert env["HOME"] == "/tmp/openclaw-home"
    assert env["OPENCLAW_HOME"] == "/tmp/openclaw-home"
    assert env["OPENCLAW_BUNDLED_PLUGINS_DIR"] == "/tmp/openclaw-plugins"
    assert env["OPENAI_BASE_URL"] == "http://localhost/v1"
    assert env["OPENAI_MODEL_NAME"] == "minimax-m2.5"
    assert env["CUSTOM_API_KEY"] == "secret"


def test_terminal_bench2_openclaw_onboard_uses_noninteractive_local_mode() -> None:
    agent = TerminalBench2OpenClawAgent()
    agent.setup({
        "api_base": "http://localhost/v1",
        "api_key": "secret",
        "model_name": "minimax-m2.5",
        "openclaw_bin": "openclaw",
    })

    cmd = agent._build_onboard_command("/app")
    assert cmd[:4] == ["openclaw", "onboard", "--non-interactive", "--flow"]
    assert "--mode" in cmd and "local" in cmd
    assert "--workspace" in cmd and "/app" in cmd
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

    cmd = agent._build_agent_command()
    assert cmd[:2] == ["openclaw", "agent"]
    assert "--local" in cmd
    assert "--json" in cmd
    assert "--message" in cmd and '$(cat "$ALPHADIANA_PROMPT_FILE")' in cmd
    assert "--timeout" in cmd and "321" in cmd
    assert "--thinking" in cmd and "high" in cmd
    assert "--verbose" in cmd and "full" in cmd
    assert cmd[-2:] == ["--message", '$(cat "$ALPHADIANA_PROMPT_FILE")']


def test_terminal_bench2_openclaw_finds_latest_session_under_runtime_home() -> None:
    agent = TerminalBench2OpenClawAgent()
    agent.setup({
        "api_base": "http://localhost/v1",
        "api_key": "secret",
        "model_name": "minimax-m2.5",
    })

    with patch.object(
        agent,
        "_docker_exec_capture",
        return_value=LocalCommandResult(
            stdout="/tmp/openclaw/home/.openclaw/agents/main/sessions/newer.jsonl\n",
            stderr="",
            returncode=0,
        ),
    ) as mock_exec:
        session = agent._find_latest_remote_session_file(
            "demo-container",
            "/tmp/openclaw/home/.openclaw/agents",
        )

    assert session.endswith("newer.jsonl")
    assert "-printf '%T@ %p\\n'" in mock_exec.call_args.args[1]
    assert "tail -n 1" in mock_exec.call_args.args[1]


def test_terminal_bench2_openclaw_context_preflight_rejects_small_window(tmp_path: Path) -> None:
    agent = TerminalBench2OpenClawAgent()
    agent.setup({
        "api_base": "http://localhost/v1",
        "api_key": "secret",
        "model_name": "qwen/qwen3.5-27b",
        "min_context_window": 32768,
        "fail_on_small_context_window": True,
    })

    with patch.object(
        agent,
        "_resolve_context_window",
        return_value=(16000, "models_endpoint"),
    ):
        try:
            agent._write_context_preflight(tmp_path)
        except RuntimeError as exc:
            message = str(exc)
        else:
            raise AssertionError("Expected low context window preflight to fail")

    assert "resolved=16000" in message
    preflight = (tmp_path / "openclaw_context_preflight.json").read_text(encoding="utf-8")
    assert '"resolved_context_window": 16000' in preflight


def test_terminal_bench2_opencode_writes_native_prompt_and_provider_config(tmp_path: Path) -> None:
    agent = TerminalBench2OpenCodeAgent()
    agent.setup({
        "api_base": "http://localhost/v1",
        "api_key": "secret",
        "model_name": "minimax-m2.5",
        "model": "custom/minimax-m2.5",
        "opencode_bin": "opencode",
    })
    assert agent._runtime_source_image == "tmlrgroup/alphadiana:opencode"
    task = BenchmarkTask(task_id="tb2_demo", problem="demo", ground_truth="1")
    task_text, prompt_text = agent._build_incontainer_prompt(task)
    assert task_text == "demo"
    assert prompt_text.startswith(IN_CONTAINER_AGENT_PROMPT)
    assert "tb2-exec" not in prompt_text
    assert "do not assume they exist under `/app`" in prompt_text
    assert "Read `TASK.md` for the task details" not in prompt_text
    assert agent._opencode_command_prefix() == ["opencode"]
    run_cmd = agent._build_run_command(task_id="tb2_demo", container_workdir="/app")

    config_path = tmp_path / "xdg-config" / "opencode" / "opencode.json"
    agent._write_provider_config(config_path)
    payload = config_path.read_text(encoding="utf-8")

    assert '"tool_call": true' in payload
    assert '"baseURL": "http://localhost/v1"' in payload
    assert '"timeout": 1800000' in payload
    assert '"model": "custom/minimax-m2.5"' in payload
    assert run_cmd[-1] == '$(cat "$ALPHADIANA_PROMPT_FILE")'
    assert run_cmd[0:4] == ["opencode", "run", "--format", "json"]
    assert "--model" in run_cmd and "custom/minimax-m2.5" in run_cmd


def test_terminal_bench2_zeroclaw_writes_native_prompt_and_config(tmp_path: Path) -> None:
    agent = TerminalBench2ZeroClawAgent()
    agent.setup({
        "api_base": "http://localhost/v1",
        "api_key": "secret",
        "model": "minimax-m2.5",
    })

    assert agent._runtime_source_image == "zeroclaw-reasoning:0.6.9"
    assert agent._system_prompt == _ZEROCLAW_SYSTEM_PROMPT.strip()
    assert agent._workspace_only is False
    assert agent._security_sandbox_enabled is False

    task = BenchmarkTask(task_id="tb2_demo", problem="demo", ground_truth="1")
    _, prompt_text = agent._build_runtime_prompt(task)
    env = agent._build_env("/tmp/zeroclaw-home")
    config_text = agent._build_config_toml()

    assert env["OPENAI_BASE_URL"] == "http://localhost/v1"
    assert env["OPENAI_MODEL_NAME"] == "minimax-m2.5"
    assert 'default_model = "minimax-m2.5"' in config_text
    assert 'default_provider = "custom:http://localhost/v1"' in config_text
    assert "[security.sandbox]" in config_text
    assert "enabled = false" in config_text
    assert "workspace_only = false" in config_text
    assert '"./tb2-exec"' not in config_text
    assert '"./tb2-copy-from"' not in config_text
    assert '"./tb2-copy-to"' not in config_text
    assert prompt_text.endswith("--- Task ---\ndemo\n")
    assert "You are solving a terminal-bench-2 task from inside the task container." in prompt_text
    assert "do not assume they exist under `/app`" in prompt_text
    assert "Read `TASK.md` for the task details" not in prompt_text
    assert "You are already inside the target task container." in _ZEROCLAW_SYSTEM_PROMPT
    assert "You are NOT inside the target task container." not in prompt_text


def test_terminal_bench2_zeroclaw_agent_command_uses_local_prompt_file(tmp_path: Path) -> None:
    agent = TerminalBench2ZeroClawAgent()
    agent.setup({
        "api_base": "http://localhost/v1",
        "api_key": "secret",
        "model": "minimax-m2.5",
        "zeroclaw_bin": "zeroclaw",
        "solver_timeout_sec": 321,
    })
    cmd = agent._build_run_command({
        "workspace_dir": "/app",
        "task_path": str(tmp_path / "task.txt"),
        "stdout_path": str(tmp_path / "zeroclaw_output.txt"),
        "stderr_path": str(tmp_path / "zeroclaw_stderr.log"),
    })

    assert "cd /app" in cmd
    assert f"prompt=$(cat {tmp_path / 'task.txt'})" in cmd
    assert 'timeout 321 zeroclaw agent -m "$prompt"' in cmd
    assert str(tmp_path / "zeroclaw_output.txt") in cmd


def test_terminal_bench2_zeroclaw_solve_preserves_nonzero_solver_result(tmp_path: Path) -> None:
    agent = TerminalBench2ZeroClawAgent()
    agent.setup({
        "api_base": "http://localhost/v1",
        "api_key": "secret",
        "model": "minimax-m2.5",
    })

    task = BenchmarkTask(
        task_id="tb2_demo",
        problem="demo task",
        ground_truth="1",
        metadata={"task_dir": str(tmp_path)},
    )
    tempdir = tempfile.TemporaryDirectory(prefix="tb2-zeroclaw-solve-")
    runtime = TerminalBench2RuntimeContext(
        task=task,
        docker_image="demo:latest",
        container_id="demo-container",
        logs_dir=tmp_path / "logs",
        workdir=Path(tempdir.name),
        helper_paths={
            "task": tmp_path / "TASK.md",
            "task_hints": tmp_path / "TASK_HINTS.md",
            "agents": tmp_path / "AGENTS.md",
            "prompt": tmp_path / "PROMPT.txt",
            "test": tmp_path / "tb2-test",
        },
        _tempdir=tempdir,
    )
    for path in runtime.helper_paths.values():
        path.write_text("", encoding="utf-8")

    try:
        with patch.object(
            agent,
            "_prepare_incontainer_runtime",
            return_value=(runtime, {"runtime_injected": True}),
        ), patch.object(
            agent,
            "_detect_container_workspace",
            return_value="/app",
        ), patch.object(
            agent,
            "_stage_file_into_container",
            return_value=None,
        ), patch.object(
            agent,
            "_docker_exec_capture",
            side_effect=[
                LocalCommandResult(stdout="", stderr="", returncode=0),
                LocalCommandResult(stdout="", stderr="controller stderr", returncode=1),
            ],
        ), patch.object(
            agent,
            "_read_container_text",
            side_effect=lambda _container_id, remote_path, **_kwargs: (
                "2026-04-20T06:17:59.498566Z  INFO zeroclaw::config::schema: Config loaded\n"
                "Loop detector summary\n"
                if remote_path.endswith("zeroclaw_output.txt")
                else (
                    "Error: Agent loop aborted by loop detector\n"
                    if remote_path.endswith("zeroclaw_stderr.log")
                    else (
                        json.dumps(
                            {
                                "type": "assistant",
                                "message": {
                                    "role": "assistant",
                                    "content": "Loop detector summary",
                                    "thinking": "checked git state",
                                },
                            }
                        )
                        + "\n"
                        if remote_path.endswith("runtime-trace.jsonl")
                        else ""
                    )
                )
            ),
            ), patch.object(
                agent,
                "_run_verifier_and_read_reward",
                return_value=VerifierResult(
                    test_output="verifier output",
                    reward="0",
                    status="ok",
                    reward_path="/tmp/reward.txt",
                    verifier_output_path="/tmp/verifier",
                ),
            ), patch.object(
                agent,
                "_cleanup_runtime",
                return_value=None,
            ):
            response = agent.solve(task)
    finally:
        runtime.cleanup()

    assert response.answer == "0"
    assert len(response.trajectory) >= 2
    assert response.raw_output == "Loop detector summary"
    assert response.metadata["raw_output_sanitized"] is True
    assert response.metadata["runtime_logs_dropped_from_output"]
    assert response.metadata["returncode"] == 1
    assert response.metadata["solver_error"] == "Error: Agent loop aborted by loop detector"
    assert response.metadata["container_workdir"] == "/app"
    assert response.response_json["runtime_trace_present"] is True
    assert response.artifact_manifest["files"]["response_stream"] == "runtime_trace.jsonl"
    assert response.artifact_manifest["files"]["stdout_log"] == "zeroclaw_output.txt"
    assert response.artifact_manifest["files"]["stderr_log"] == "zeroclaw_stderr.log"
    assert len(response.reasoning_trajectory) >= 1


def _make_incontainer_runtime(tmp_path: Path, task: BenchmarkTask, prefix: str) -> TerminalBench2RuntimeContext:
    tempdir = tempfile.TemporaryDirectory(prefix=prefix)
    runtime = TerminalBench2RuntimeContext(
        task=task,
        docker_image="demo:latest",
        container_id="demo-container",
        logs_dir=tmp_path / "logs",
        workdir=Path(tempdir.name),
        helper_paths={
            "task": tmp_path / "TASK.md",
            "task_hints": tmp_path / "TASK_HINTS.md",
            "agents": tmp_path / "AGENTS.md",
            "prompt": tmp_path / "PROMPT.txt",
            "test": tmp_path / "tb2-test",
        },
        _tempdir=tempdir,
    )
    for path in runtime.helper_paths.values():
        path.write_text("", encoding="utf-8")
    return runtime


def test_terminal_bench2_openclaw_solve_preserves_session_trace_and_summary(tmp_path: Path) -> None:
    agent = TerminalBench2OpenClawAgent()
    agent.setup({
        "api_base": "http://localhost/v1",
        "api_key": "secret",
        "model_name": "minimax-m2.5",
    })

    task = BenchmarkTask(
        task_id="tb2_demo",
        problem="demo task",
        ground_truth="1",
        metadata={"task_dir": str(tmp_path)},
    )
    runtime = _make_incontainer_runtime(tmp_path, task, "tb2-openclaw-solve-")
    session_jsonl = "\n".join([
        json.dumps({"type": "message", "message": {"role": "user", "content": [{"type": "text", "text": "demo task"}]}}),
        json.dumps(
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "inspect logs"},
                        {"type": "toolCall", "name": "bash", "arguments": {"cmd": "ls"}},
                        {"type": "text", "text": "fixed"},
                    ],
                },
            }
        ),
    ])

    try:
        with patch.object(
            agent,
            "_prepare_incontainer_runtime",
            return_value=(runtime, {"runtime_injected": True}),
        ), patch.object(
            agent,
            "_detect_container_workspace",
            return_value="/app",
        ), patch.object(
            agent,
            "_write_context_preflight",
            return_value={"resolved_context_window": 131072, "source": "models_endpoint"},
        ), patch.object(
            agent,
            "_stage_file_into_container",
            return_value=None,
        ), patch.object(
            agent,
            "_copy_file_from_container",
            side_effect=lambda _container_id, remote_path, local_path, **_kwargs: local_path.write_text(
                session_jsonl if str(remote_path).endswith("session.jsonl") else "{\"models\":{}}",
                encoding="utf-8",
            ),
        ), patch.object(
            agent,
            "_patch_runtime_model_contract",
            return_value={"patched": False, "resolved_context_window": 131072},
        ), patch.object(
            agent,
            "_find_latest_remote_session_file",
            return_value="/tmp/openclaw/home/.openclaw/agents/main/sessions/demo-session.jsonl",
        ), patch.object(
            agent,
            "_docker_exec_capture",
            side_effect=[
                LocalCommandResult(stdout='{"status":"ok"}\n', stderr="", returncode=0),
                LocalCommandResult(
                    stdout='{"choices":[{"message":{"role":"assistant","content":"fixed"}}]}\n',
                    stderr="",
                    returncode=0,
                ),
            ],
            ), patch.object(
                agent,
                "_run_verifier_and_read_reward",
                return_value=VerifierResult(
                    test_output="verifier output",
                    reward="1",
                    status="ok",
                    reward_path="/tmp/reward.txt",
                    verifier_output_path="/tmp/verifier",
                ),
            ), patch.object(
                agent,
                "_cleanup_runtime",
                return_value=None,
            ):
            response = agent.solve(task)
    finally:
        runtime.cleanup()

    assert response.answer == "1"
    assert response.response_json["session_id"] == "demo-session"
    assert response.response_json["output_text"] == "fixed"
    assert response.artifact_manifest["files"]["session_trace"] == "session.jsonl"
    assert response.artifact_manifest["files"]["response_stream"] == "openclaw_stdout.log"
    assert len(response.reasoning_trajectory) >= 2
    assert any(
        step.get("thinking") == "inspect logs"
        for step in response.trajectory
        if step.get("role") == "assistant"
    )


def test_terminal_bench2_opencode_solve_preserves_stream_and_session_trace(tmp_path: Path) -> None:
    agent = TerminalBench2OpenCodeAgent()
    agent.setup({
        "api_base": "http://localhost/v1",
        "api_key": "secret",
        "model_name": "minimax-m2.5",
        "model": "custom/minimax-m2.5",
    })

    task = BenchmarkTask(
        task_id="tb2_demo",
        problem="demo task",
        ground_truth="1",
        metadata={"task_dir": str(tmp_path)},
    )
    runtime = _make_incontainer_runtime(tmp_path, task, "tb2-opencode-solve-")
    output_record = {
        "type": "assistant",
        "sessionID": "session-123",
        "message": {"role": "assistant", "content": "patched"},
    }
    session_record = {
        "type": "tool_call",
        "name": "bash",
        "content": "git status",
    }

    try:
        with patch.object(
            agent,
            "_prepare_incontainer_runtime",
            return_value=(runtime, {"runtime_injected": True}),
        ), patch.object(
            agent,
            "_detect_container_workspace",
            return_value="/app",
        ), patch.object(
            agent,
            "_stage_file_into_container",
            return_value=None,
        ), patch.object(
            agent,
            "_collect_session_trace",
            return_value=f"# file: /tmp/demo.jsonl\n{json.dumps(session_record)}\n",
        ), patch.object(
            agent,
            "_docker_exec_capture",
            return_value=LocalCommandResult(
                stdout=f"{json.dumps(output_record)}\n",
                stderr="stderr line",
                returncode=0,
            ),
            ), patch.object(
                agent,
                "_run_verifier_and_read_reward",
                return_value=VerifierResult(
                    test_output="verifier output",
                    reward="0",
                    status="ok",
                    reward_path="/tmp/reward.txt",
                    verifier_output_path="/tmp/verifier",
                ),
            ), patch.object(
                agent,
                "_cleanup_runtime",
                return_value=None,
            ):
            response = agent.solve(task)
    finally:
        runtime.cleanup()

    assert response.answer == "0"
    assert response.response_json["session_id"] == "session-123"
    assert response.response_json["num_session_records"] == 1
    assert response.artifact_manifest["files"]["response_stream"] == "opencode_stdout.log"
    assert response.artifact_manifest["files"]["session_trace"] == "opencode_session.jsonl"
    assert len(response.reasoning_trajectory) >= 1
    assert response.trajectory[-1]["content"] == "patched"


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
            verifier_result = harness._run_verifier_and_read_reward(runtime, timeout_sec=30)
    finally:
        runtime.cleanup()

    assert verifier_result.reward == "1"
    assert verifier_result.status == "ok"
    assert "reused existing verifier reward.txt" in verifier_result.test_output
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
        with patch.object(harness, "_probe_running_verifier", return_value=(True, "ok")), patch.object(
            harness,
            "_run_tests",
        ) as mock_run, patch(
            "alphadiana.agent.terminal_bench2_common.time.sleep",
            side_effect=_write_reward_after_wait,
        ):
            verifier_result = harness._run_verifier_and_read_reward(runtime, timeout_sec=5)
    finally:
        runtime.cleanup()

    assert verifier_result.reward == "1"
    assert verifier_result.status == "ok"
    assert "existing verifier" in verifier_result.test_output
    mock_run.assert_not_called()


def test_terminal_bench2_missing_reward_is_verifier_error_not_valid_scored() -> None:
    record = {
        "task_id": "tb2_demo",
        "benchmark_name": "terminal_bench2",
        "scorer_name": "terminal_bench2",
        "score": 0.0,
        "correct": False,
        "metadata": {
            "verifier_status": "missing_reward",
            "reward": "0",
        },
    }

    assert infer_score_status(record) == "verifier_error"
    assert is_valid_completed_record(record, scorer_name="terminal_bench2") is False


def test_terminal_bench2_reward_zero_with_verifier_ok_is_valid_scored() -> None:
    record = {
        "task_id": "tb2_demo",
        "benchmark_name": "terminal_bench2",
        "scorer_name": "terminal_bench2",
        "score": 0.0,
        "correct": False,
        "metadata": {
            "verifier_status": "ok",
            "reward": "0",
        },
    }

    assert infer_score_status(record) == "valid_scored"
    assert is_valid_completed_record(record, scorer_name="terminal_bench2") is True


def test_terminal_bench2_trajectory_error_does_not_invalidate_verifier_ok_result() -> None:
    record = {
        "task_id": "tb2_demo",
        "benchmark_name": "terminal_bench2",
        "scorer_name": "terminal_bench2",
        "score": 0.0,
        "correct": False,
        "metadata": {
            "verifier_status": "ok",
            "reward": "0",
            "trajectory_error": "Request was aborted",
        },
    }

    assert infer_score_status(record) == "valid_scored"
    assert is_valid_completed_record(record, scorer_name="terminal_bench2") is True


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
