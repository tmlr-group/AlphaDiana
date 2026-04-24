from __future__ import annotations

from alphadiana.agent.logprob_proxy import (
    LogprobCaptureProxy,
    normalize_openai_proxy_upstream,
    resolve_logprob_proxy_advertise_host,
)
from alphadiana.agent.openclaw import OpenClawAgent
from alphadiana.agent.zeroclaw import ZeroClawAgent
from alphadiana.benchmark.base import BenchmarkTask


def test_logprob_proxy_normalizes_upstream_and_advertise_host() -> None:
    assert normalize_openai_proxy_upstream("http://127.0.0.1:8011/v1") == "http://127.0.0.1:8011"
    assert resolve_logprob_proxy_advertise_host("http://127.0.0.1:8011") == "172.17.0.1"
    assert resolve_logprob_proxy_advertise_host("http://10.0.0.3:8011") == "10.0.0.3"
    assert resolve_logprob_proxy_advertise_host("http://127.0.0.1:8011", "host.test") == "host.test"


class _FakeOpenClawRuntime:
    is_configured = True

    def __init__(self) -> None:
        self.provider_api_base = ""
        self.provider_api_key = ""

    def set_provider_api_base(self, api_base: str) -> None:
        self.provider_api_base = api_base

    def set_provider_api_key(self, api_key: str) -> None:
        self.provider_api_key = api_key


class _FakeProxy:
    instances: list["_FakeProxy"] = []

    def __init__(
        self,
        upstream: str,
        top_logprobs: int,
        *,
        bind_host: str = "127.0.0.1",
        advertise_host: str = "",
        client_timeout: float = 120.0,
        upstream_api_key: str = "",
        proxy_api_key: str = "",
    ) -> None:
        self.upstream = normalize_openai_proxy_upstream(upstream)
        self.top_logprobs = top_logprobs
        self.bind_host = bind_host
        self.advertise_host = advertise_host
        self.client_timeout = client_timeout
        self.upstream_api_key = upstream_api_key
        self.proxy_api_key = proxy_api_key
        self.started = False
        _FakeProxy.instances.append(self)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    @property
    def proxy_url(self) -> str:
        return "http://172.17.0.1:4567"

    @property
    def local_url(self) -> str:
        return "http://127.0.0.1:4567"

    def drain_records(self) -> list[dict]:
        return []


def test_openclaw_runtime_logprob_proxy_overrides_upstream_base(monkeypatch) -> None:
    _FakeProxy.instances = []
    monkeypatch.setattr("alphadiana.agent.openclaw.LogprobCaptureProxy", _FakeProxy)
    monkeypatch.setattr("alphadiana.agent.openclaw.secrets.token_urlsafe", lambda _: "proxy-token")
    runtime = _FakeOpenClawRuntime()
    agent = OpenClawAgent()
    agent.setup(
        {
            "OPENAI_BASE_URL": "http://127.0.0.1:8011/v1",
            "OPENAI_API_KEY": "EMPTY",
            "OPENAI_MODEL_NAME": "Qwen/Qwen3.5-27B",
            "capture_logprobs": True,
            "top_logprobs": 7,
        }
    )
    agent._runtime_manager = runtime

    proxy = agent._ensure_runtime_logprob_proxy()

    assert proxy is _FakeProxy.instances[0]
    assert _FakeProxy.instances[0].started is True
    assert _FakeProxy.instances[0].upstream == "http://127.0.0.1:8011"
    assert _FakeProxy.instances[0].top_logprobs == 7
    assert _FakeProxy.instances[0].bind_host == "0.0.0.0"
    assert _FakeProxy.instances[0].upstream_api_key == "EMPTY"
    assert _FakeProxy.instances[0].proxy_api_key == "proxy-token"
    assert runtime.provider_api_base == "http://172.17.0.1:4567/v1"
    assert runtime.provider_api_key == "proxy-token"


class _ZeroClawProxy(_FakeProxy):
    def drain_records(self) -> list[dict]:
        return [
            {
                "token_index": 0,
                "token": "D",
                "logprob": -0.1,
                "top_logprobs": [{"token": "D", "logprob": -0.1}],
            }
        ]


class _ZeroClawSandbox:
    sandbox_id = "sandbox-zero"
    name = "rock"

    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.commands: list[str] = []

    def execute(self, command: str):
        self.commands.append(command)
        if command == "pwd":
            return type("Result", (), {"exit_code": 0, "stdout": "/sandbox/work\n", "stderr": ""})()
        if command.startswith("mkdir -p ") or command.startswith("chmod "):
            return type("Result", (), {"exit_code": 0, "stdout": "", "stderr": ""})()
        if "command -v zeroclaw" in command:
            return type("Result", (), {"exit_code": 0, "stdout": "/usr/local/bin/zeroclaw\n", "stderr": ""})()
        if "zeroclaw --version" in command:
            return type("Result", (), {"exit_code": 0, "stdout": "zeroclaw 0.6.9\n", "stderr": ""})()
        if "zeroclaw agent -m" in command:
            stdout_path = command.split("> ", 1)[1].split(" 2> ", 1)[0]
            stderr_path = command.split(" 2> ", 1)[1]
            self.files[stdout_path] = "$$\\boxed{D}$$"
            self.files[stderr_path] = ""
            return type("Result", (), {"exit_code": 0, "stdout": "", "stderr": ""})()
        if command.startswith("bash -lc ") and "cat " in command:
            filename = command.split("cat ", 1)[1].split(" || true", 1)[0].strip("'\"")
            return type("Result", (), {"exit_code": 0, "stdout": self.files.get(filename, ""), "stderr": ""})()
        return type("Result", (), {"exit_code": 0, "stdout": "", "stderr": ""})()

    def upload(self, filename: str, content: bytes) -> None:
        self.files[filename] = content.decode("utf-8", errors="replace")

    def read_text(self, filename: str) -> str:
        return self.files.get(filename, "")

    def metadata(self) -> dict:
        return {"sandbox_id": self.sandbox_id}


def test_zeroclaw_uses_logprob_proxy_records(monkeypatch) -> None:
    _FakeProxy.instances = []
    monkeypatch.setattr("alphadiana.agent.zeroclaw.LogprobCaptureProxy", _ZeroClawProxy)
    monkeypatch.setattr("alphadiana.agent.zeroclaw.secrets.token_urlsafe", lambda _: "proxy-token")
    agent = ZeroClawAgent()
    agent.setup(
        {
            "model": "Qwen/Qwen3.5-27B",
            "api_base": "http://127.0.0.1:8011/v1",
            "api_key": "EMPTY",
            "capture_logprobs": True,
            "top_logprobs": 20,
        }
    )
    sandbox = _ZeroClawSandbox()
    task = BenchmarkTask(task_id="gpqa_0", problem="Pick D.", ground_truth="D", metadata={"execution_id": "z-proxy"})

    response = agent.solve(task, sandbox=sandbox)

    run_commands = [command for command in sandbox.commands if "zeroclaw agent -m" in command]
    assert run_commands
    assert "OPENAI_BASE_URL=http://172.17.0.1:4567/v1" in run_commands[0]
    assert "OPENAI_API_KEY=proxy-token" in run_commands[0]
    assert "ZEROCLAW_PROVIDER=custom:http://172.17.0.1:4567/v1" in run_commands[0]
    assert response.metadata["logprobs_capture_status"] == "captured"
    assert response.metadata["logprob_probe_proxy_count"] == 1
    assert response.token_entropy_stats["n_tokens"] == 1
    assert response.metadata["logprob_records"][0]["token"] == "D"
    config_path = next(path for path in sandbox.files if path.endswith("config.toml"))
    assert "[model_providers.custom.options]" not in sandbox.files[config_path]
