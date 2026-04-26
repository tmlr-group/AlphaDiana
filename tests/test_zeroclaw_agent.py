from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from alphadiana.agent.registry import AgentRegistry
from alphadiana.agent.zeroclaw import ZeroClawAgent
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.config.experiment_config import ExperimentConfig
from alphadiana.config.validator import ConfigValidator


def _is_zeroclaw_agent_command(command: str) -> bool:
    return "zeroclaw " in command and " agent " in command and " -m " in command


class _FakeSandbox:
    sandbox_id = "sandbox-123"
    name = "rock"

    def __init__(self, *, fail_read_prefixes: tuple[str, ...] = ()) -> None:
        self._files: dict[str, str] = {}
        self.commands: list[str] = []
        self.long_running_commands: list[tuple[str, int | None, int]] = []
        self._fail_read_prefixes = fail_read_prefixes

    def execute(self, command: str):
        self.commands.append(command)
        if command == "pwd":
            return type("Result", (), {"exit_code": 0, "stdout": "/sandbox/work\n", "stderr": ""})()
        if command.startswith("mkdir -p ") or command.startswith("chmod 600 "):
            return type("Result", (), {"exit_code": 0, "stdout": "", "stderr": ""})()
        if "command -v zeroclaw" in command:
            return type("Result", (), {"exit_code": 0, "stdout": "/usr/local/bin/zeroclaw\n", "stderr": ""})()
        if "zeroclaw --version" in command:
            return type("Result", (), {"exit_code": 0, "stdout": "zeroclaw 0.6.9\n", "stderr": ""})()
        if _is_zeroclaw_agent_command(command):
            task_path = next((path for path in self._files if path.endswith("/task.txt")), "")
            prompt = self._files.get(task_path, "")
            stdout_path = command.split("> ", 1)[1].split(" 2> ", 1)[0]
            stderr_path = command.split(" 2> ", 1)[1]
            answer = "42"
            if "[IMAGE:" in prompt:
                answer = "A"
            self._files[stdout_path] = f"Reasoning...\n\n$$\\boxed{{{answer}}}$$\n"
            self._files[stderr_path] = ""
            return type("Result", (), {"exit_code": 0, "stdout": "", "stderr": ""})()
        if command.startswith("cat "):
            filename = command[4:]
            if filename in self._files:
                return type("Result", (), {"exit_code": 0, "stdout": self._files[filename], "stderr": ""})()
            return type("Result", (), {"exit_code": 1, "stdout": "", "stderr": "not found"})()
        if command.startswith("bash -lc ") and " && cat " in command:
            inner = command[len("bash -lc "):].strip()
            if inner and inner[0] in {"'", '"'} and inner[-1] == inner[0]:
                inner = inner[1:-1]
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
        self._files[filename] = content.decode("latin1")

    def read_text(self, filename: str) -> str:
        if any(filename.startswith(prefix) for prefix in self._fail_read_prefixes):
            raise RuntimeError("read_text unavailable")
        return self._files[filename]

    def metadata(self) -> dict:
        return {"sandbox_id": self.sandbox_id}


class _FakeErrorSandbox(_FakeSandbox):
    def execute(self, command: str):
        if _is_zeroclaw_agent_command(command):
            self.commands.append(command)
            task_path = next((path for path in self._files if path.endswith("/task.txt")), "")
            stdout_path = command.split("> ", 1)[1].split(" 2> ", 1)[0]
            stderr_path = command.split(" 2> ", 1)[1]
            self._files[stdout_path] = (
                "Error: All providers/models failed. Attempts:\n"
                "provider=custom:test model=qwen attempt 1/3: rate_limited; "
                "error={\"type\":\"proxy_error\"}"
            )
            self._files[stderr_path] = ""
            runtime_trace_path = str(Path(task_path).parent / "state" / "runtime-trace.jsonl")
            self._files[runtime_trace_path] = ""
            return type("Result", (), {"exit_code": 0, "stdout": "", "stderr": ""})()
        return super().execute(command)


class _FakeRockLikeSandbox(_FakeSandbox):
    def execute(self, command: str):
        if command.startswith("cat "):
            filename = command[4:]
            if filename not in self._files:
                raise RuntimeError("missing file")
        if command.startswith("bash -lc ") and "test -f " in command and "|| true" in command:
            return type("Result", (), {"exit_code": 0, "stdout": "", "stderr": ""})()
        return super().execute(command)


class _FakeNonZeroProviderSandbox(_FakeSandbox):
    def execute(self, command: str):
        if _is_zeroclaw_agent_command(command):
            self.commands.append(command)
            task_path = next((path for path in self._files if path.endswith("/task.txt")), "")
            stdout_path = command.split("> ", 1)[1].split(" 2> ", 1)[0]
            stderr_path = command.split(" 2> ", 1)[1]
            provider_error = (
                "Error: All providers/models failed. Attempts:\n"
                "provider=custom:http://172.17.0.1:8011/v1 model=Qwen/Qwen3.5-27B "
                "attempt 1/3: non_retryable; error=Custom API error (400 Bad Request): "
                '{"error":{"message":"System message must be at the beginning.","type":"BadRequestError","code":400}}'
            )
            self._files[stdout_path] = ""
            self._files[stderr_path] = provider_error
            runtime_trace_path = str(Path(task_path).parent / "state" / "runtime-trace.jsonl")
            self._files[runtime_trace_path] = ""
            return type("Result", (), {"exit_code": 1, "stdout": "", "stderr": provider_error})()
        return super().execute(command)


class _FakeRuntimeOnlySandbox(_FakeSandbox):
    def execute(self, command: str):
        if _is_zeroclaw_agent_command(command):
            self.commands.append(command)
            task_path = next((path for path in self._files if path.endswith("/task.txt")), "")
            stdout_path = command.split("> ", 1)[1].split(" 2> ", 1)[0]
            stderr_path = command.split(" 2> ", 1)[1]
            self._files[stdout_path] = (
                "2026-04-25T00:00:00Z INFO zeroclaw::agent::loop_: Memory initialized\n"
                "2026-04-25T00:00:01Z INFO zeroclaw::security::detect: No sandbox backend available\n"
            )
            self._files[stderr_path] = ""
            runtime_trace_path = str(Path(task_path).parent / "state" / "runtime-trace.jsonl")
            self._files[runtime_trace_path] = ""
            return type("Result", (), {"exit_code": 0, "stdout": "", "stderr": ""})()
        return super().execute(command)


class _FakeLoopDetectorSandbox(_FakeSandbox):
    _stderr = (
        "Error: Agent loop aborted by loop detector: Circuit breaker: "
        "tool 'web_search_tool' called 5 times consecutively"
    )

    def execute(self, command: str):
        if _is_zeroclaw_agent_command(command):
            self.commands.append(command)
            task_path = next((path for path in self._files if path.endswith("/task.txt")), "")
            stdout_path = command.split("> ", 1)[1].split(" 2> ", 1)[0]
            stderr_path = command.split(" 2> ", 1)[1]
            self._files[stdout_path] = (
                "2026-04-25T00:00:00Z INFO zeroclaw::tools::web_search_tool: Searching\n"
            )
            self._files[stderr_path] = self._stderr
            runtime_trace_path = str(Path(task_path).parent / "state" / "runtime-trace.jsonl")
            self._files[runtime_trace_path] = ""
            return type("Result", (), {"exit_code": 0, "stdout": "", "stderr": ""})()
        return super().execute(command)

    def read_text(self, filename: str) -> str:
        if filename.endswith("zeroclaw_stderr.log"):
            return self._stderr
        return super().read_text(filename)


class _FakeTimeoutWithLogprobsSandbox(_FakeSandbox):
    def execute(self, command: str):
        if _is_zeroclaw_agent_command(command):
            self.commands.append(command)
            task_path = next((path for path in self._files if path.endswith("/task.txt")), "")
            stdout_path = command.split("> ", 1)[1].split(" 2> ", 1)[0]
            stderr_path = command.split(" 2> ", 1)[1]
            self._files[stdout_path] = (
                "2026-04-25T00:00:00Z INFO zeroclaw::agent::loop_: still running\n"
            )
            self._files[stderr_path] = ""
            runtime_trace_path = str(Path(task_path).parent / "state" / "runtime-trace.jsonl")
            self._files[runtime_trace_path] = ""
            return type("Result", (), {"exit_code": 124, "stdout": "", "stderr": ""})()
        return super().execute(command)


class _FakeLogprobProxy:
    def __init__(self, upstream: str, top_logprobs: int, **_: object) -> None:
        self.upstream = upstream
        self.top_logprobs = top_logprobs
        self.started = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    @property
    def proxy_url(self) -> str:
        return "http://172.17.0.1:4567"

    def drain_records(self) -> list[dict]:
        return [
            {
                "token_index": idx,
                "token": f"tok{idx}",
                "logprob": -0.1,
                "top_logprobs": [{"token": f"tok{idx}", "logprob": -0.1}],
            }
            for idx in range(5)
        ]


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


def test_zeroclaw_config_toml_honors_request_controls_and_security_config():
    agent = ZeroClawAgent()
    agent.setup({
        "model": "demo-model",
        "api_base": "https://example.test/v1",
        "api_key": "sk-test",
        "temperature": 0.2,
        "provider_timeout_secs": 600,
        "provider_max_tokens": 32768,
        "reasoning_enabled": True,
        "reasoning_effort": "high",
        "security_sandbox_enabled": False,
        "security_sandbox_backend": "auto",
    })

    config_toml = agent._build_config_toml()

    assert "default_temperature = 0.2" in config_toml
    assert "provider_timeout_secs = 600" in config_toml
    assert "provider_max_tokens = 32768" in config_toml
    assert "[runtime]" in config_toml
    assert "reasoning_enabled = true" in config_toml
    assert 'reasoning_effort = "high"' in config_toml
    assert "[security.sandbox]" in config_toml
    assert "enabled = false" in config_toml
    assert 'backend = "auto"' in config_toml


def test_zeroclaw_config_toml_maps_max_tokens_to_provider_max_tokens():
    agent = ZeroClawAgent()
    agent.setup({
        "model": "demo-model",
        "api_base": "https://example.test/v1",
        "api_key": "sk-test",
        "max_tokens": 65536,
        "top_p": 0.95,
    })

    config_toml = agent._build_config_toml()

    assert "provider_max_tokens = 65536" in config_toml
    assert "top_p" not in config_toml
    assert "logprobs" not in config_toml


def test_zeroclaw_logprob_overrides_include_provider_extra_body():
    agent = ZeroClawAgent()
    agent.setup({
        "model": "demo-model",
        "api_base": "https://example.test/v1",
        "api_key": "sk-test",
        "max_tokens": 65536,
        "top_p": 0.95,
        "temperature": 0.0,
        "enable_thinking": False,
        "reasoning_enabled": False,
        "extra_body": {"metadata": {"run": "smoke"}},
    })

    overrides = agent._logprob_request_overrides()

    assert overrides["temperature"] == 0.0
    assert overrides["top_p"] == 0.95
    assert overrides["max_tokens"] == 65536
    assert overrides["chat_template_kwargs"] == {"enable_thinking": False}
    assert overrides["reasoning"] == {"enabled": False}
    assert overrides["metadata"] == {"run": "smoke"}


def test_zeroclaw_provider_max_tokens_overrides_common_max_tokens():
    agent = ZeroClawAgent()
    agent.setup({
        "model": "demo-model",
        "api_base": "https://example.test/v1",
        "api_key": "sk-test",
        "max_tokens": 65536,
        "provider_max_tokens": 32768,
    })

    config_toml = agent._build_config_toml()

    assert "provider_max_tokens = 32768" in config_toml
    assert "provider_max_tokens = 65536" not in config_toml


@pytest.mark.parametrize(
    "config",
    [
        {"disable_tools": True},
        {"multimodal_via_proxy": True},
        {"use_gateway_in_sandbox": False},
        {"gateway_api_base": "http://proxy.test/v1"},
        {"gateway_pool": ["http://proxy-a.test/v1"]},
        {"sandbox_id": "sandbox-123"},
    ],
)
def test_zeroclaw_rejects_removed_path_selection_keys(config):
    agent = ZeroClawAgent()
    with pytest.raises(ValueError, match="native sandbox CLI path"):
        agent.setup({
            "model": "demo-model",
            "api_base": "https://example.test/v1",
            "api_key": "sk-test",
            **config,
        })


def test_zeroclaw_requires_live_sandbox_session():
    task = BenchmarkTask(task_id="aime_no_sandbox", problem="Compute 6*7.", ground_truth="42")

    agent = ZeroClawAgent()
    agent.setup({
        "model": "demo-model",
        "api_base": "https://example.test/v1",
        "api_key": "sk-test",
    })

    with pytest.raises(RuntimeError, match="requires a live sandbox/container session"):
        agent.solve(task)


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
    assert response.artifact_manifest["files"]["zeroclaw_output"] == "zeroclaw_output.txt"
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
    assert response.metadata["transport"] == "zeroclaw_cli_sandbox"
    assert response.artifact_manifest["files"]["zeroclaw_output"] == "zeroclaw_output.txt"
    assert 'level = "full"' in sandbox.read_text(config_path)
    assert "What is 40+2?" in sandbox.read_text(task_path)
    assert len(sandbox.long_running_commands) == 1
    long_running_command, wait_timeout, wait_interval = sandbox.long_running_commands[0]
    assert "zeroclaw --config-dir /sandbox/work/.alphadiana_zeroclaw/exec-123/home/.zeroclaw" in long_running_command
    assert "--session-state-file /sandbox/work/.alphadiana_zeroclaw/exec-123/workspace/state/zeroclaw-session-state.json" in long_running_command
    assert "ZEROCLAW_CONFIG_DIR=/sandbox/work/.alphadiana_zeroclaw/exec-123/home/.zeroclaw" in long_running_command
    assert "XDG_CONFIG_HOME=/sandbox/work/.alphadiana_zeroclaw/exec-123/home/.config" in long_running_command
    assert wait_timeout == agent._request_timeout + 60
    assert wait_interval == 10
    assert any("env " in command and "bash -lc" in command for command in sandbox.commands)


def test_zeroclaw_sandbox_solve_uploads_workspace_attachments_and_image_markers():
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

    assert response.answer == "A"
    assert response.metadata["transport"] == "zeroclaw_cli_sandbox"
    assert "Workspace Attachments" in task_text
    assert "attachments/image_1.png (image/png)" in task_text
    assert "[IMAGE:/sandbox/work/.alphadiana_zeroclaw/exec-attachments/workspace/attachments/image_1.png]" in task_text
    assert sandbox.read_text(attachment_path) == "fake-png"


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


def test_zeroclaw_sandbox_provider_failures_preserve_failure_reason():
    task = BenchmarkTask(
        task_id="hle_provider_error",
        problem="Use the image to answer.",
        ground_truth="A",
        metadata={"execution_id": "exec-provider-error"},
        attachments={
            "image_1": b"fake-png",
            "image_1_mime": b"image/png",
        },
    )
    sandbox = _FakeErrorSandbox()

    agent = ZeroClawAgent()
    agent.setup({
        "model": "demo-model",
        "api_base": "https://example.test/v1",
        "api_key": "sk-test",
    })

    with pytest.raises(RuntimeError) as excinfo:
        agent.solve(task, sandbox=sandbox)

    partial = getattr(excinfo.value, "partial_response", None)
    assert partial is not None
    assert getattr(excinfo.value, "error_type", "") == "proxy_error"
    assert partial.metadata["failure_reason"] == "proxy_error"
    assert partial.metadata["transport"] == "zeroclaw_cli_sandbox"


def test_zeroclaw_nonzero_sandbox_provider_failures_classify_as_provider_error():
    task = BenchmarkTask(
        task_id="swe_provider_error",
        problem="Fix the issue.",
        ground_truth="diff --git a/foo b/foo",
        metadata={
            "execution_id": "exec-nonzero-provider-error",
            "instance_id": "django__django-11790",
            "repo": "django/django",
        },
    )
    sandbox = _FakeNonZeroProviderSandbox()

    agent = ZeroClawAgent()
    agent.setup({
        "model": "Qwen/Qwen3.5-27B",
        "api_base": "http://172.17.0.1:8011/v1",
        "api_key": "LOCAL_DUMMY",
    })

    with pytest.raises(RuntimeError) as excinfo:
        agent.solve(task, sandbox=sandbox)

    partial = getattr(excinfo.value, "partial_response", None)
    assert partial is not None
    assert getattr(excinfo.value, "error_type", "") == "provider_error"
    assert partial.metadata["failure_reason"] == "provider_error"
    assert partial.metadata["transport"] == "zeroclaw_cli_sandbox"


def test_zeroclaw_runtime_only_output_with_logprobs_scores_zero(monkeypatch):
    monkeypatch.setattr("alphadiana.agent.zeroclaw.LogprobCaptureProxy", _FakeLogprobProxy)
    task = BenchmarkTask(
        task_id="aime_truncated",
        problem="Compute 6*7.",
        ground_truth="42",
        metadata={"execution_id": "exec-runtime-only"},
    )
    sandbox = _FakeRuntimeOnlySandbox()
    agent = ZeroClawAgent()
    agent.setup({
        "model": "Qwen/Qwen3.5-27B",
        "api_base": "http://127.0.0.1:8011/v1",
        "api_key": "EMPTY",
        "capture_logprobs": True,
        "max_tokens": 5,
    })

    response = agent.solve(task, sandbox=sandbox)

    assert response.answer is None
    assert response.finish_reason == "length"
    assert response.metadata["zeroclaw_empty_assistant_scored_zero"] is True
    assert response.metadata["logprobs_capture_status"] == "captured"
    assert response.token_entropy_stats["n_tokens"] == 5


def test_zeroclaw_loop_detector_with_logprobs_scores_zero(monkeypatch):
    monkeypatch.setattr("alphadiana.agent.zeroclaw.LogprobCaptureProxy", _FakeLogprobProxy)
    task = BenchmarkTask(
        task_id="hle_loop_detector",
        problem="Pick A.",
        ground_truth="A",
        metadata={"execution_id": "exec-loop-detector"},
    )
    sandbox = _FakeLoopDetectorSandbox()
    agent = ZeroClawAgent()
    agent.setup({
        "model": "Qwen/Qwen3.5-27B",
        "api_base": "http://127.0.0.1:8011/v1",
        "api_key": "EMPTY",
        "capture_logprobs": True,
        "max_tokens": 10,
    })

    response = agent.solve(task, sandbox=sandbox)

    assert response.answer is None
    assert response.finish_reason == "incomplete"
    assert response.metadata["zeroclaw_empty_assistant_scored_zero"] is True
    assert "Agent loop aborted" in response.metadata["zeroclaw_terminal_cli_error"]
    assert response.metadata["logprobs_capture_status"] == "captured"


def test_zeroclaw_timeout_with_logprobs_scores_zero(monkeypatch):
    monkeypatch.setattr("alphadiana.agent.zeroclaw.LogprobCaptureProxy", _FakeLogprobProxy)
    task = BenchmarkTask(
        task_id="imo_timeout",
        problem="Solve a long problem.",
        ground_truth="42",
        metadata={"execution_id": "exec-timeout"},
    )
    sandbox = _FakeTimeoutWithLogprobsSandbox()
    agent = ZeroClawAgent()
    agent.setup({
        "model": "Qwen/Qwen3.5-27B",
        "api_base": "http://127.0.0.1:8011/v1",
        "api_key": "EMPTY",
        "capture_logprobs": True,
        "max_tokens": 10,
        "request_timeout": 30,
    })

    response = agent.solve(task, sandbox=sandbox)

    assert response.answer is None
    assert response.finish_reason == "timeout"
    assert response.metadata["zeroclaw_timeout_preserved_partial_response"] is True
    assert response.metadata["zeroclaw_timeout_seconds"] == 30
    assert response.metadata["zeroclaw_empty_assistant_scored_zero"] is True
    assert response.metadata["logprobs_capture_status"] == "captured"
