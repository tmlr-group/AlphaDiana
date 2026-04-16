from __future__ import annotations

import textwrap
from types import SimpleNamespace
from pathlib import Path

from alphadiana.agent.registry import AgentRegistry
from alphadiana.agent.zeroclaw import ZeroClawAgent
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.config.experiment_config import ExperimentConfig
from alphadiana.config.validator import ConfigValidator


class _FakeSandbox:
    def __init__(self, *, fail_read_prefixes: tuple[str, ...] = ()) -> None:
        self._files: dict[str, str] = {}
        self.commands: list[str] = []
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
        return type("Result", (), {"exit_code": 0, "stdout": "", "stderr": ""})()

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
    assert 'default_provider = "custom:https://example.test/v1"' in config_toml
    assert "workspace-only" not in config_toml
    assert "max-actions-per-hour" not in config_toml
    assert "max-tool-iterations" not in config_toml


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
    assert any(command.startswith("cat /sandbox/work/.alphadiana_zeroclaw/exec-456/zeroclaw_output.txt") for command in sandbox.commands)


def test_zeroclaw_gateway_mode_uses_rock_proxy(monkeypatch):
    task = BenchmarkTask(task_id="aime_4", problem="What is 6*7?", ground_truth="42")
    sandbox = _FakeProxySandbox()

    agent = ZeroClawAgent()
    agent.setup({
        "model": "demo-model",
        "api_base": "https://example.test/v1",
        "api_key": "sk-test",
        "system_prompt": "Solve carefully.",
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


def test_start_zeroclaw_script_checks_expected_routes():
    script = Path("scripts/start_zeroclaw.sh").read_text(encoding="utf-8")
    assert "/apis/envs/sandbox/v1/start_async" in script
    assert "/apis/envs/sandbox/v1/sandboxes" in script
    assert "zeroclaw-reasoning:0.6.9" in script
    assert "alphadiana.cli validate" in script


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
