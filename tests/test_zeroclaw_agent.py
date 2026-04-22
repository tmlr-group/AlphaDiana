from __future__ import annotations

import json
import textwrap
from types import SimpleNamespace
from pathlib import Path

import pytest

from alphadiana.agent.registry import AgentRegistry
from alphadiana.agent.zeroclaw import ZeroClawAgent
from alphadiana.agent.zeroclaw_runtime import ZeroClawRuntimeManager
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.config.experiment_config import ExperimentConfig
from alphadiana.config.validator import ConfigValidator


class _FakeSandbox:
    def __init__(self, *, fail_read_prefixes: tuple[str, ...] = ()) -> None:
        self._files: dict[str, str] = {}
        self.commands: list[str] = []
        self.long_running_commands: list[tuple[str, int | None, int]] = []
        self._fail_read_prefixes = fail_read_prefixes

    def execute(self, command: str):
        self.commands.append(command)
        if command == "pwd":
            return type("Result", (), {"exit_code": 0, "stdout": "/sandbox/work\n", "stderr": ""})()
        if "command -v zeroclaw" in command:
            return type("Result", (), {"exit_code": 0, "stdout": "/usr/local/bin/zeroclaw\n", "stderr": ""})()
        if "zeroclaw --version" in command:
            return type("Result", (), {"exit_code": 0, "stdout": "zeroclaw 0.6.9\n", "stderr": ""})()
        if "zeroclaw agent -m" in command:
            stdout_path = command.split("> ", 1)[1].split(" 2> ", 1)[0]
            stderr_path = command.split(" 2> ", 1)[1]
            self._files[stdout_path] = "Reasoning...\n\n$$\\boxed{42}$$\n"
            self._files[stderr_path] = ""
            return type("Result", (), {"exit_code": 0, "stdout": "", "stderr": ""})()
        if command.startswith("cat "):
            filename = command[4:]
            if filename in self._files:
                return type("Result", (), {"exit_code": 0, "stdout": self._files[filename], "stderr": ""})()
            return type("Result", (), {"exit_code": 1, "stdout": "", "stderr": "not found"})()
        if command.startswith("bash -lc ") and " && cat " in command:
            inner = command[len("bash -lc "):].strip().strip("'")
            filename = inner.split(" && cat ", 1)[1].split(" || true", 1)[0]
            if filename in self._files:
                return type("Result", (), {"exit_code": 0, "stdout": self._files[filename], "stderr": ""})()
            return type("Result", (), {"exit_code": 0, "stdout": "", "stderr": ""})()
        return type("Result", (), {"exit_code": 0, "stdout": "", "stderr": ""})()

    def execute_long_running(
        self,
        command: str,
        *,
        wait_timeout: int | None = None,
        wait_interval: int = 10,
    ):
        self.long_running_commands.append((command, wait_timeout, wait_interval))
        return self.execute(command)

    def upload(self, filename: str, content: bytes) -> None:
        self._files[filename] = content.decode("utf-8")

    def read_text(self, filename: str) -> str:
        if any(filename.startswith(prefix) for prefix in self._fail_read_prefixes):
            raise RuntimeError("read_text unavailable")
        return self._files[filename]


class _FakeProxySandbox(_FakeSandbox):
    sandbox_id = "sandbox-123"
    name = "rock"

    def proxy_v1_base(self) -> str:
        return "http://proxy.test/apis/envs/sandbox/v1/sandboxes/sandbox-123/proxy/v1"

    def metadata(self) -> dict:
        return {"sandbox_id": self.sandbox_id, "proxy_base_url": self.proxy_v1_base()}


class _FakeRockLikeSandbox(_FakeSandbox):
    def execute(self, command: str):
        if command.startswith("cat "):
            filename = command[4:]
            if filename not in self._files:
                raise RuntimeError("missing file")
        if command.startswith("bash -lc 'test -f ") and "|| true'" in command:
            return type("Result", (), {"exit_code": 0, "stdout": "", "stderr": ""})()
        return super().execute(command)


def test_zeroclaw_agent_is_registered():
    assert AgentRegistry.get("zeroclaw") is ZeroClawAgent


def test_experiment_config_applies_openai_env_defaults_for_zeroclaw(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL_NAME", "qwen-test")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    yaml_path = tmp_path / "zeroclaw.yaml"
    yaml_path.write_text(textwrap.dedent("""\
        agent:
          name: zeroclaw
          version: "0.6.9"
          config: {}
        benchmark:
          name: aime
        scorer:
          name: numeric
    """))

    config = ExperimentConfig.from_yaml(str(yaml_path))
    assert config.agent_config["model"] == "qwen-test"
    assert config.agent_config["api_base"] == "https://example.test/v1"
    assert config.agent_config["api_key"] == "sk-test"


def test_config_validator_accepts_zeroclaw_api_agent():
    config = ExperimentConfig(
        agent_name="zeroclaw",
        agent_version="0.6.9",
        benchmark_name="aime",
        scorer_name="numeric",
        agent_config={"api_base": "https://example.test/v1"},
    )

    errors = ConfigValidator().validate(config)
    assert errors == []


def test_zeroclaw_config_toml_uses_snake_case_fields():
    agent = ZeroClawAgent()
    agent.setup({
        "model": "demo-model",
        "api_base": "https://example.test/v1",
        "api_key": "sk-test",
    })

    config_toml = agent._build_config_toml()

    assert "workspace_only = false" in config_toml
    assert "max_actions_per_hour = 200" in config_toml
    assert "max_tool_iterations = 100" in config_toml
    assert "default_temperature = 0.0" in config_toml
    assert "provider_timeout_secs = 1200" in config_toml
    assert 'default_provider = "custom:https://example.test/v1"' in config_toml
    assert "[provider]" not in config_toml
    assert "workspace-only" not in config_toml
    assert "max-actions-per-hour" not in config_toml
    assert "max-tool-iterations" not in config_toml


def test_zeroclaw_config_toml_honors_explicit_temperature():
    agent = ZeroClawAgent()
    agent.setup({
        "model": "demo-model",
        "api_base": "https://example.test/v1",
        "api_key": "sk-test",
        "temperature": 0.2,
    })

    config_toml = agent._build_config_toml()

    assert "default_temperature = 0.2" in config_toml


def test_zeroclaw_config_toml_honors_explicit_provider_timeout():
    agent = ZeroClawAgent()
    agent.setup({
        "model": "demo-model",
        "api_base": "https://example.test/v1",
        "api_key": "sk-test",
        "provider_timeout_secs": 600,
    })

    config_toml = agent._build_config_toml()

    assert "provider_timeout_secs = 600" in config_toml


def test_zeroclaw_config_toml_honors_request_controls():
    agent = ZeroClawAgent()
    agent.setup({
        "model": "demo-model",
        "api_base": "https://example.test/v1",
        "api_key": "sk-test",
        "provider_max_tokens": 32768,
        "reasoning_enabled": True,
        "reasoning_effort": "high",
    })

    config_toml = agent._build_config_toml()

    assert "provider_max_tokens = 32768" in config_toml
    assert "[runtime]" in config_toml
    assert "reasoning_enabled = true" in config_toml
    assert 'reasoning_effort = "high"' in config_toml


def test_zeroclaw_config_toml_honors_security_sandbox_controls():
    agent = ZeroClawAgent()
    agent.setup({
        "model": "demo-model",
        "api_base": "https://example.test/v1",
        "api_key": "sk-test",
        "security_sandbox_enabled": False,
        "security_sandbox_backend": "auto",
    })

    config_toml = agent._build_config_toml()

    assert "[security.sandbox]" in config_toml
    assert "enabled = false" in config_toml
    assert 'backend = "auto"' in config_toml


def test_zeroclaw_local_solve_extracts_boxed_answer(monkeypatch):
    task = BenchmarkTask(task_id="aime_1", problem="Compute 6*7.", ground_truth="42")

    agent = ZeroClawAgent()
    agent.setup({
        "model": "demo-model",
        "api_base": "https://example.test/v1",
        "api_key": "sk-test",
        "system_prompt": "Solve carefully.",
    })

    monkeypatch.setattr(ZeroClawAgent, "_ensure_local_binary", lambda self, env, cwd: "zeroclaw 0.6.9")

    def fake_run(command, shell, cwd, env, capture_output, text):
        stdout_path = command.split("> ", 1)[1].split(" 2> ", 1)[0]
        stderr_path = command.split(" 2> ", 1)[1]
        Path(stdout_path).write_text("analysis\n\n$$\\boxed{42}$$\n", encoding="utf-8")
        Path(stderr_path).write_text("", encoding="utf-8")
        return type("Proc", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("alphadiana.agent.zeroclaw.subprocess.run", fake_run)

    response = agent.solve(task)

    assert response.answer == "42"
    assert response.raw_output.endswith("$$\\boxed{42}$$")
    assert response.metadata["model"] == "demo-model"
    assert response.metadata["zeroclaw_version"] == "zeroclaw 0.6.9"
    assert len(response.trajectory) >= 4
    assert len(response.reasoning_trajectory) >= 1
    assert response.artifact_manifest["files"]["response_stream"] == "zeroclaw_output.txt"


def test_zeroclaw_local_solve_strips_runtime_logs_from_top_level_output(monkeypatch):
    task = BenchmarkTask(task_id="aime_logs", problem="Compute 6*7.", ground_truth="42")

    agent = ZeroClawAgent()
    agent.setup({
        "model": "demo-model",
        "api_base": "https://example.test/v1",
        "api_key": "sk-test",
        "system_prompt": "Solve carefully.",
    })

    monkeypatch.setattr(ZeroClawAgent, "_ensure_local_binary", lambda self, env, cwd: "zeroclaw 0.6.9")

    def fake_run(command, shell, cwd, env, capture_output, text):
        stdout_path = command.split("> ", 1)[1].split(" 2> ", 1)[0]
        stderr_path = command.split(" 2> ", 1)[1]
        Path(stdout_path).write_text(
            "\n".join(
                [
                    '2026-04-19T14:23:34.007139Z  WARN zeroclaw::config::schema: Unknown config key ignored: "provider".',
                    "2026-04-19T14:23:34.089414Z  INFO zeroclaw::agent::loop_: Memory initialized backend=\"sqlite\"",
                    "analysis",
                    "",
                    "$$\\boxed{42}$$",
                ]
            ),
            encoding="utf-8",
        )
        Path(stderr_path).write_text("", encoding="utf-8")
        return type("Proc", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("alphadiana.agent.zeroclaw.subprocess.run", fake_run)

    response = agent.solve(task)

    assert response.answer == "42"
    assert response.raw_output == "analysis\n\n$$\\boxed{42}$$"
    assert response.metadata["raw_output_sanitized"] is True


def test_zeroclaw_disable_tools_uses_direct_provider_with_inline_images(monkeypatch):
    task = BenchmarkTask(
        task_id="hle_no_tools",
        problem="Use the image to answer.",
        ground_truth="A",
        attachments={
            "image_1": b"fake-png",
            "image_1_mime": b"image/png",
        },
    )

    agent = ZeroClawAgent()
    agent.setup({
        "model": "qwen3vl",
        "api_base": "https://example.test/v1",
        "api_key": "sk-test",
        "disable_tools": True,
        "system_prompt": "Solve carefully.",
    })

    monkeypatch.setattr(
        ZeroClawAgent,
        "_ensure_local_binary",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("CLI path should not run")),
    )

    seen: dict[str, object] = {}

    def fake_post(url, headers, json, timeout, trust_env):
        seen["url"] = url
        seen["payload"] = json
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "choices": [{"message": {"content": "analysis\n\n$$\\boxed{A}$$"}}],
            },
            text="",
        )

    monkeypatch.setattr("httpx.post", fake_post)

    response = agent.solve(task)

    user_content = seen["payload"]["messages"][1]["content"]
    assert seen["url"] == "https://example.test/v1/chat/completions"
    assert user_content[0]["type"] == "text"
    assert user_content[1]["type"] == "image_url"
    assert response.answer == "A"
    assert response.metadata["gateway_mode"] == "no-tools-direct"


def test_zeroclaw_no_tools_transport_error_preserves_failure_evidence(monkeypatch):
    import httpx

    task = BenchmarkTask(task_id="hle_no_tools_error", problem="Use the image to answer.", ground_truth="A")

    agent = ZeroClawAgent()
    agent.setup({
        "model": "qwen3vl",
        "api_base": "https://example.test/v1",
        "api_key": "sk-test",
        "disable_tools": True,
    })

    def fake_post(url, headers, json, timeout, trust_env):
        raise httpx.ConnectError("provider unavailable", request=httpx.Request("POST", url))

    monkeypatch.setattr("httpx.post", fake_post)

    with pytest.raises(RuntimeError) as excinfo:
        agent.solve(task)

    partial = getattr(excinfo.value, "partial_response", None)
    assert partial is not None
    assert getattr(excinfo.value, "error_type", "") == "provider_error"
    assert partial.metadata["gateway_mode"] == "no-tools-direct"
    assert partial.metadata["failure_reason"] == "provider_error"
    assert partial.response_json["transport_error"] == "provider unavailable"


def test_zeroclaw_no_tools_empty_response_preserves_failure_evidence(monkeypatch):
    task = BenchmarkTask(task_id="hle_no_tools_empty", problem="Use the image to answer.", ground_truth="A")

    agent = ZeroClawAgent()
    agent.setup({
        "model": "qwen3vl",
        "api_base": "https://example.test/v1",
        "api_key": "sk-test",
        "disable_tools": True,
    })

    def fake_post(url, headers, json, timeout, trust_env):
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"choices": [{"message": {"content": ""}}]},
            text='{"choices":[{"message":{"content":""}}]}',
        )

    monkeypatch.setattr("httpx.post", fake_post)

    with pytest.raises(RuntimeError) as excinfo:
        agent.solve(task)

    partial = getattr(excinfo.value, "partial_response", None)
    assert partial is not None
    assert getattr(excinfo.value, "error_type", "") == "empty_response"
    assert partial.metadata["gateway_mode"] == "no-tools-direct"
    assert partial.metadata["failure_reason"] == "empty_response"
    assert partial.response_json["provider_status_code"] == 200


def test_build_cli_response_promotes_runtime_trace_into_native_fields():
    agent = ZeroClawAgent()
    agent.setup({
        "model": "demo-model",
        "api_base": "https://example.test/v1",
        "api_key": "sk-test",
        "system_prompt": "Solve carefully.",
    })

    response = agent._build_cli_response(
        prompt="Solve carefully.\n\nProblem:\nCompute 6*7.",
        raw_output="analysis\n\n$$\\boxed{42}$$",
        raw_stderr="",
        runtime_trace="\n".join([
            json.dumps({"type": "tool", "name": "python"}),
            json.dumps({"type": "message", "message": "checked by python"}),
        ]),
        attachment_items=[],
        metadata={"model": "demo-model"},
        wall_time_sec=1.2,
        system_prompt="Solve carefully.",
        artifact_manifest={},
        request_messages=[
            {"role": "system", "content": "Solve carefully."},
            {"role": "user", "content": "Compute 6*7."},
        ],
    )

    assert len(response.trajectory) >= 3
    assert response.trajectory[0]["role"] == "system"
    assert any(step.get("type") == "tool" for step in response.trajectory)
    assert response.reasoning_trajectory[0]["type"] == "tool"
    assert response.response_json["runtime_trace_present"] is True
    assert response.artifact_manifest["files"]["response_stream"] == "runtime_trace.jsonl"
    assert '"type": "tool"' in response.workspace_file_contents["runtime_trace.jsonl"]


def test_zeroclaw_sandbox_solve_uses_uploaded_workspace_and_returns_answer():
    task = BenchmarkTask(
        task_id="aime_2",
        problem="What is 40+2?",
        ground_truth="42",
        metadata={"execution_id": "exec-123"},
    )
    sandbox = _FakeSandbox()

    agent = ZeroClawAgent()
    agent.setup({
        "model": "demo-model",
        "api_base": "https://example.test/v1",
        "api_key": "sk-test",
        "system_prompt": "Use tools if needed.",
    })

    response = agent.solve(task, sandbox=sandbox)

    config_path = "/sandbox/work/.alphadiana_zeroclaw/exec-123/home/.zeroclaw/config.toml"
    task_path = "/sandbox/work/.alphadiana_zeroclaw/exec-123/workspace/task.txt"

    assert response.answer == "42"
    assert response.metadata["execution_id"] == "exec-123"
    assert response.metadata["workspace_dir"].endswith("/workspace")
    assert 'level = "full"' in sandbox.read_text(config_path)
    assert "What is 40+2?" in sandbox.read_text(task_path)
    assert len(sandbox.long_running_commands) == 1
    long_running_command, wait_timeout, wait_interval = sandbox.long_running_commands[0]
    assert "zeroclaw agent -m" in long_running_command
    assert wait_timeout == agent._request_timeout + 60
    assert wait_interval == 10
    assert any("zeroclaw agent -m" in command for command in sandbox.commands)
    assert any(f"cat {task_path}" in command for command in sandbox.commands)
    assert any("env " in command and "bash -lc" in command for command in sandbox.commands)


def test_zeroclaw_sandbox_solve_falls_back_to_cat_when_read_text_fails():
    task = BenchmarkTask(
        task_id="aime_3",
        problem="What is 40+2?",
        ground_truth="42",
        metadata={"execution_id": "exec-456"},
    )
    sandbox = _FakeSandbox(fail_read_prefixes=("/sandbox/work/.alphadiana_zeroclaw/exec-456/",))

    agent = ZeroClawAgent()
    agent.setup({
        "model": "demo-model",
        "api_base": "https://example.test/v1",
        "api_key": "sk-test",
    })

    response = agent.solve(task, sandbox=sandbox)

    assert response.answer == "42"
    assert any("/sandbox/work/.alphadiana_zeroclaw/exec-456/zeroclaw_output.txt" in command for command in sandbox.commands)


def test_zeroclaw_read_sandbox_file_ignores_missing_files_on_rock_like_backend():
    agent = ZeroClawAgent()
    agent.setup({
        "model": "demo-model",
        "api_base": "https://example.test/v1",
        "api_key": "sk-test",
    })

    sandbox = _FakeRockLikeSandbox()

    assert agent._read_sandbox_file(sandbox, "/sandbox/work/missing.txt") == ""


def test_zeroclaw_sandbox_solve_uploads_workspace_attachments():
    task = BenchmarkTask(
        task_id="hle_0",
        problem="What letter matches the image?",
        ground_truth="A",
        metadata={"execution_id": "exec-attachments"},
        attachments={
            "image_1": b"fake-png",
            "image_1_mime": b"image/png",
        },
    )
    sandbox = _FakeSandbox()

    agent = ZeroClawAgent()
    agent.setup({
        "model": "demo-model",
        "api_base": "https://example.test/v1",
        "api_key": "sk-test",
    })

    response = agent.solve(task, sandbox=sandbox)

    task_path = "/sandbox/work/.alphadiana_zeroclaw/exec-attachments/workspace/task.txt"
    attachment_path = "/sandbox/work/.alphadiana_zeroclaw/exec-attachments/workspace/attachments/image_1.png"
    task_text = sandbox.read_text(task_path)

    assert response.answer == "42"
    assert "Workspace Attachments" in task_text
    assert "attachments/image_1.png (image/png)" in task_text
    assert sandbox.read_text(attachment_path) == "fake-png"


def test_zeroclaw_gateway_mode_uses_rock_proxy(monkeypatch):
    task = BenchmarkTask(task_id="aime_4", problem="What is 6*7?", ground_truth="42")
    sandbox = _FakeProxySandbox()

    agent = ZeroClawAgent()
    agent.setup({
        "model": "demo-model",
        "api_base": "https://example.test/v1",
        "api_key": "sk-test",
        "system_prompt": "Solve carefully.",
        "temperature": 0.2,
        "use_gateway_in_sandbox": True,
        "gateway_token": "ZEROCLAW",
    })

    class _FakeRuntimeManager:
        is_configured = True

        def ensure_ready(self, sandbox):
            return {
                "api_base": sandbox.proxy_v1_base(),
                "gateway_url": f"{sandbox.proxy_v1_base()}/chat/completions",
                "sandbox_id": sandbox.sandbox_id,
            }

        def collect_artifacts(self, sandbox):
            return {
                "artifact_manifest": {"files": {"bridge_log_source": "/tmp/zeroclaw-gateway.log"}},
                "gateway_log_excerpt": "bridge started",
                "workspace_snapshot_paths": [],
                "workspace_file_contents": {},
                "sandbox_metadata": sandbox.metadata(),
            }

    agent._runtime_manager = _FakeRuntimeManager()

    def fake_post(url, headers, json, timeout, trust_env):
        assert url.endswith("/chat/completions")
        assert headers["Authorization"] == "bearer ZEROCLAW"
        assert json["messages"][0]["role"] == "system"
        assert json["temperature"] == 0.2
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "choices": [
                    {
                        "message": {
                            "content": "analysis\n\n$$\\boxed{42}$$",
                        }
                    }
                ]
            },
            text="",
        )

    monkeypatch.setattr("httpx.post", fake_post)

    response = agent.solve(task, sandbox=sandbox)

    assert response.answer == "42"
    assert response.gateway_url.endswith("/chat/completions")
    assert response.sandbox_id == "sandbox-123"
    assert response.gateway_log_excerpt == "bridge started"
    assert response.metadata["gateway_mode"] == "rock-proxy"
    assert len(response.trajectory) >= 4
    assert len(response.reasoning_trajectory) >= 1
    assert response.artifact_manifest["files"]["response_stream"] == "zeroclaw_output.txt"
    assert response.artifact_manifest["files"]["request_payload"] == "gateway_request.json"
    assert response.artifact_manifest["files"]["selected_response"] == "gateway_response.json"


def test_zeroclaw_gateway_mode_serializes_attachments(monkeypatch):
    task = BenchmarkTask(
        task_id="hle_attachments",
        problem="Use the image in the workspace.",
        ground_truth="A",
        attachments={
            "image_1": b"fake-png",
            "image_1_mime": b"image/png",
        },
    )
    sandbox = _FakeProxySandbox()

    agent = ZeroClawAgent()
    agent.setup({
        "model": "demo-model",
        "api_base": "https://example.test/v1",
        "api_key": "sk-test",
        "use_gateway_in_sandbox": True,
        "gateway_token": "ZEROCLAW",
    })

    class _FakeRuntimeManager:
        is_configured = True

        def ensure_ready(self, sandbox):
            return {
                "api_base": sandbox.proxy_v1_base(),
                "gateway_url": f"{sandbox.proxy_v1_base()}/chat/completions",
                "sandbox_id": sandbox.sandbox_id,
            }

        def collect_artifacts(self, sandbox):
            return {
                "artifact_manifest": {},
                "gateway_log_excerpt": "",
                "workspace_snapshot_paths": [],
                "workspace_file_contents": {},
                "sandbox_metadata": sandbox.metadata(),
            }

    agent._runtime_manager = _FakeRuntimeManager()

    def fake_post(url, headers, json, timeout, trust_env):
        attachments = json["attachments"]
        assert len(attachments) == 1
        assert attachments[0]["path"] == "attachments/image_1.png"
        assert attachments[0]["mime"] == "image/png"
        assert json["messages"][0]["content"].startswith(agent._system_prompt)
        assert "Workspace Attachments" in json["messages"][1]["content"]
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "choices": [{"message": {"content": "analysis\n\n$$\\boxed{A}$$"}}],
            },
            text="",
        )

    monkeypatch.setattr("httpx.post", fake_post)

    response = agent.solve(task, sandbox=sandbox)

    assert response.answer == "A"


def test_zeroclaw_gateway_disable_tools_falls_back_to_provider_without_cli(monkeypatch):
    task = BenchmarkTask(task_id="hle_gateway_no_tools", problem="Read the image.", ground_truth="A")
    sandbox = _FakeProxySandbox()

    agent = ZeroClawAgent()
    agent.setup({
        "model": "qwen3vl",
        "api_base": "https://example.test/v1",
        "api_key": "sk-test",
        "disable_tools": True,
        "use_gateway_in_sandbox": True,
        "gateway_token": "ZEROCLAW",
    })

    class _FakeRuntimeManager:
        is_configured = True

        def ensure_ready(self, sandbox):
            return {
                "api_base": sandbox.proxy_v1_base(),
                "gateway_url": f"{sandbox.proxy_v1_base()}/chat/completions",
                "sandbox_id": sandbox.sandbox_id,
            }

        def collect_artifacts(self, sandbox):
            return {
                "artifact_manifest": {"files": {"bridge_log_source": "/tmp/zeroclaw-gateway.log"}},
                "gateway_log_excerpt": "bridge failed",
                "workspace_snapshot_paths": [],
                "workspace_file_contents": {},
                "sandbox_metadata": sandbox.metadata(),
            }

    agent._runtime_manager = _FakeRuntimeManager()

    def fake_post(url, headers, json, timeout, trust_env):
        if url.startswith("http://proxy.test/"):
            return SimpleNamespace(
                status_code=500,
                json=lambda: {"error": {"message": "bridge failed"}},
                text='{"error":{"message":"bridge failed"}}',
            )
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "choices": [{"message": {"content": "analysis\n\n$$\\boxed{A}$$"}}],
            },
            text="",
        )

    monkeypatch.setattr("httpx.post", fake_post)

    response = agent.solve(task, sandbox=sandbox)

    assert response.answer == "A"
    assert response.metadata["gateway_mode"] == "rock-proxy-fallback-no-tools"
    assert sandbox.long_running_commands == []


def test_zeroclaw_gateway_mode_falls_back_to_direct_sandbox(monkeypatch):
    task = BenchmarkTask(task_id="aime_5", problem="What is 6*7?", ground_truth="42")
    sandbox = _FakeProxySandbox()

    agent = ZeroClawAgent()
    agent.setup({
        "model": "demo-model",
        "api_base": "https://example.test/v1",
        "api_key": "sk-test",
        "use_gateway_in_sandbox": True,
        "gateway_token": "ZEROCLAW",
    })

    class _FakeRuntimeManager:
        is_configured = True

        def ensure_ready(self, sandbox):
            return {
                "api_base": sandbox.proxy_v1_base(),
                "gateway_url": f"{sandbox.proxy_v1_base()}/chat/completions",
                "sandbox_id": sandbox.sandbox_id,
            }

        def collect_artifacts(self, sandbox):
            return {
                "artifact_manifest": {"files": {"bridge_log_source": "/tmp/zeroclaw-gateway.log"}},
                "gateway_log_excerpt": "bridge started",
                "workspace_snapshot_paths": [],
                "workspace_file_contents": {},
                "sandbox_metadata": sandbox.metadata(),
            }

    agent._runtime_manager = _FakeRuntimeManager()

    def fake_post(url, headers, json, timeout, trust_env):
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"status": "Failed", "message": "http proxy failed", "error": "", "result": None},
            text="",
        )

    monkeypatch.setattr("httpx.post", fake_post)

    response = agent.solve(task, sandbox=sandbox)

    assert response.answer == "42"
    assert response.metadata["gateway_mode"] == "rock-proxy-fallback-to-cli"
    assert "http proxy failed" in response.metadata["gateway_fallback_reason"]
    assert sandbox.long_running_commands


def test_zeroclaw_gateway_bootstrap_failure_falls_back_to_direct_sandbox(monkeypatch):
    task = BenchmarkTask(task_id="aime_5_bootstrap", problem="What is 6*7?", ground_truth="42")
    sandbox = _FakeProxySandbox()

    agent = ZeroClawAgent()
    agent.setup({
        "model": "demo-model",
        "api_base": "https://example.test/v1",
        "api_key": "sk-test",
        "use_gateway_in_sandbox": True,
        "gateway_token": "ZEROCLAW",
    })

    class _FakeRuntimeManager:
        is_configured = True

        def ensure_ready(self, sandbox):
            raise RuntimeError("warmup status=200 body={'status': 'Failed', 'message': 'http proxy failed'}")

        def collect_artifacts(self, sandbox):
            return {
                "artifact_manifest": {"files": {"bridge_log_source": "/tmp/zeroclaw-gateway.log"}},
                "gateway_log_excerpt": "bridge startup failed",
                "workspace_snapshot_paths": [],
                "workspace_file_contents": {},
                "sandbox_metadata": sandbox.metadata(),
            }

    agent._runtime_manager = _FakeRuntimeManager()

    response = agent.solve(task, sandbox=sandbox)

    assert response.answer == "42"
    assert response.metadata["gateway_mode"] == "rock-proxy-bootstrap-fallback-to-cli"
    assert "http proxy failed" in response.metadata["gateway_fallback_reason"]
    assert response.gateway_log_excerpt == "bridge startup failed"
    assert sandbox.long_running_commands


def test_zeroclaw_gateway_mode_falls_back_to_direct_sandbox_on_transport_error(monkeypatch):
    import httpx

    task = BenchmarkTask(task_id="aime_5b", problem="What is 6*7?", ground_truth="42")
    sandbox = _FakeProxySandbox()

    agent = ZeroClawAgent()
    agent.setup({
        "model": "demo-model",
        "api_base": "https://example.test/v1",
        "api_key": "sk-test",
        "use_gateway_in_sandbox": True,
        "gateway_token": "ZEROCLAW",
    })

    class _FakeRuntimeManager:
        is_configured = True

        def ensure_ready(self, sandbox):
            return {
                "api_base": sandbox.proxy_v1_base(),
                "gateway_url": f"{sandbox.proxy_v1_base()}/chat/completions",
                "sandbox_id": sandbox.sandbox_id,
            }

        def collect_artifacts(self, sandbox):
            return {
                "artifact_manifest": {"files": {"bridge_log_source": "/tmp/zeroclaw-gateway.log"}},
                "gateway_log_excerpt": "bridge started",
                "workspace_snapshot_paths": [],
                "workspace_file_contents": {},
                "sandbox_metadata": sandbox.metadata(),
            }

    agent._runtime_manager = _FakeRuntimeManager()

    def fake_post(url, headers, json, timeout, trust_env):
        raise httpx.ConnectError("proxy unavailable", request=httpx.Request("POST", url))

    monkeypatch.setattr("httpx.post", fake_post)

    response = agent.solve(task, sandbox=sandbox)

    assert response.answer == "42"
    assert response.sandbox_id == "sandbox-123"
    assert response.metadata["gateway_mode"] == "rock-proxy-fallback-to-cli"
    assert "proxy unavailable" in response.metadata["gateway_fallback_reason"]


def test_zeroclaw_gateway_fallback_survives_artifact_collection_failure(monkeypatch):
    task = BenchmarkTask(task_id="aime_5c", problem="What is 6*7?", ground_truth="42")
    sandbox = _FakeProxySandbox()

    agent = ZeroClawAgent()
    agent.setup({
        "model": "demo-model",
        "api_base": "https://example.test/v1",
        "api_key": "sk-test",
        "use_gateway_in_sandbox": True,
        "gateway_token": "ZEROCLAW",
    })

    class _FakeRuntimeManager:
        is_configured = True

        def ensure_ready(self, sandbox):
            return {
                "api_base": sandbox.proxy_v1_base(),
                "gateway_url": f"{sandbox.proxy_v1_base()}/chat/completions",
                "sandbox_id": sandbox.sandbox_id,
            }

        def collect_artifacts(self, sandbox):
            raise RuntimeError("artifact dir missing")

    agent._runtime_manager = _FakeRuntimeManager()

    def fake_post(url, headers, json, timeout, trust_env):
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"status": "Failed", "message": "http proxy failed", "error": "", "result": None},
            text="",
        )

    monkeypatch.setattr("httpx.post", fake_post)

    response = agent.solve(task, sandbox=sandbox)

    assert response.answer == "42"
    assert response.metadata["gateway_mode"] == "rock-proxy-fallback-to-cli"
    assert "http proxy failed" in response.metadata["gateway_fallback_reason"]


def test_zeroclaw_runtime_collect_artifacts_ignores_missing_files():
    manager = ZeroClawRuntimeManager({
        "model": "demo-model",
        "api_base": "https://example.test/v1",
        "api_key": "sk-test",
    })

    class _FailingArtifactSandbox:
        def execute(self, command: str):
            raise RuntimeError("missing file")

        def metadata(self) -> dict:
            return {"sandbox_id": "sandbox-123"}

    artifacts = manager.collect_artifacts(_FailingArtifactSandbox())

    assert artifacts["gateway_log_excerpt"] == ""
    assert artifacts["workspace_snapshot_paths"] == []
    assert artifacts["workspace_file_contents"] == {}
    assert artifacts["sandbox_metadata"] == {"sandbox_id": "sandbox-123"}


def test_start_zeroclaw_script_checks_expected_routes():
    script = Path("scripts/start_zeroclaw.sh").read_text(encoding="utf-8")
    assert "/apis/envs/sandbox/v1/start_async" in script
    assert "/apis/envs/sandbox/v1/sandboxes" in script
    assert "zeroclaw-reasoning:0.6.9" in script
    assert "source scripts/rock_env.sh" in script
    assert "alphadiana.cli validate" in script
    assert "security_guard.py" in script
    assert '-p "127.0.0.1:${_redis_port}:6379"' in script


def test_zeroclaw_gateway_pool_mode_round_robins_without_live_sandbox(monkeypatch):
    task1 = BenchmarkTask(task_id="aime_5", problem="What is 6*7?", ground_truth="42")
    task2 = BenchmarkTask(task_id="aime_6", problem="What is 6*7?", ground_truth="42")

    agent = ZeroClawAgent()
    agent.setup({
        "model": "demo-model",
        "api_base": "https://example.test/v1",
        "api_key": "sk-test",
        "sandbox_id": "sandbox-a",
        "gateway_pool": [
            "http://proxy.test/apis/envs/sandbox/v1/sandboxes/sandbox-a/proxy/v1",
            "http://proxy.test/apis/envs/sandbox/v1/sandboxes/sandbox-b/proxy/v1",
        ],
        "gateway_token": "ZEROCLAW",
    })

    seen_urls: list[str] = []

    def fake_post(url, headers, json, timeout, trust_env):
        seen_urls.append(url)
        assert headers["Authorization"] == "bearer ZEROCLAW"
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "choices": [
                    {
                        "message": {
                            "content": "analysis\n\n$$\\boxed{42}$$",
                        }
                    }
                ]
            },
            text="",
        )

    monkeypatch.setattr("httpx.post", fake_post)

    response1 = agent.solve(task1)
    response2 = agent.solve(task2)

    assert response1.answer == "42"
    assert response2.answer == "42"
    assert response1.sandbox_id == "sandbox-a"
    assert response2.sandbox_id == "sandbox-b"
    assert response1.metadata["gateway_mode"] == "gateway-pool"
    assert response2.metadata["gateway_mode"] == "gateway-pool"
    assert seen_urls == [
        "http://proxy.test/apis/envs/sandbox/v1/sandboxes/sandbox-a/proxy/v1/chat/completions",
        "http://proxy.test/apis/envs/sandbox/v1/sandboxes/sandbox-b/proxy/v1/chat/completions",
    ]


def test_zeroclaw_gateway_empty_response_preserves_partial_failure(monkeypatch):
    task = BenchmarkTask(task_id="aime_gateway_empty", problem="What is 6*7?", ground_truth="42")

    agent = ZeroClawAgent()
    agent.setup({
        "model": "demo-model",
        "api_base": "https://example.test/v1",
        "api_key": "sk-test",
        "gateway_api_base": "http://proxy.test/apis/envs/sandbox/v1/sandboxes/sandbox-a/proxy/v1",
        "sandbox_id": "sandbox-a",
    })

    def fake_post(url, headers, json, timeout, trust_env):
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"choices": [{"message": {"content": ""}}]},
            text='{"choices":[{"message":{"content":""}}]}',
        )

    monkeypatch.setattr("httpx.post", fake_post)

    with pytest.raises(RuntimeError) as excinfo:
        agent.solve(task)

    partial = getattr(excinfo.value, "partial_response", None)
    assert partial is not None
    assert getattr(excinfo.value, "error_type", "") == "empty_response"
    assert partial.metadata["gateway_mode"] == "gateway-pool"
    assert partial.metadata["failure_reason"] == "empty_response"
    assert partial.response_json["gateway_status_code"] == 200


def test_zeroclaw_local_timeout_raises_with_partial_response(monkeypatch):
    task = BenchmarkTask(task_id="aime_timeout", problem="Compute 6*7.", ground_truth="42")

    agent = ZeroClawAgent()
    agent.setup({
        "model": "demo-model",
        "api_base": "https://example.test/v1",
        "api_key": "sk-test",
    })

    monkeypatch.setattr(ZeroClawAgent, "_ensure_local_binary", lambda self, env, cwd: "zeroclaw 0.6.9")

    def fake_run(command, shell, cwd, env, capture_output, text):
        stdout_path = command.split("> ", 1)[1].split(" 2> ", 1)[0]
        stderr_path = command.split(" 2> ", 1)[1]
        Path(stdout_path).write_text("partial reasoning", encoding="utf-8")
        Path(stderr_path).write_text("timeout stderr", encoding="utf-8")
        return type("Proc", (), {"returncode": 124, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("alphadiana.agent.zeroclaw.subprocess.run", fake_run)

    with pytest.raises(RuntimeError) as excinfo:
        agent.solve(task)

    partial = getattr(excinfo.value, "partial_response", None)
    assert partial is not None
    assert partial.raw_output == "partial reasoning"
    assert partial.workspace_file_contents["zeroclaw_stderr.log"] == "timeout stderr"
    assert partial.request_messages[-1]["content"] == task.problem
