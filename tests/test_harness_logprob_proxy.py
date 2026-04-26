from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import http.server
import json
import threading
import time
import urllib.request

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


def test_logprob_proxy_injects_request_overrides() -> None:
    seen: dict = {}

    class _CaptureHandler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            seen["path"] = self.path
            seen["authorization"] = self.headers.get("Authorization", "")
            seen["payload"] = json.loads(self.rfile.read(length))
            body = {
                "choices": [
                    {
                        "message": {"content": "D"},
                        "logprobs": {
                            "content": [
                                {
                                    "token": "D",
                                    "logprob": -0.1,
                                    "top_logprobs": [{"token": "D", "logprob": -0.1}],
                                }
                            ]
                        },
                    }
                ]
            }
            raw = json.dumps(body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args: object) -> None:
            pass

    upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _CaptureHandler)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    proxy = LogprobCaptureProxy(
        f"http://127.0.0.1:{upstream.server_address[1]}",
        20,
        upstream_api_key="UPSTREAM",
        request_overrides={"temperature": 0.0, "top_p": 0.95, "max_tokens": 131072},
    )
    proxy.start()
    try:
        payload = json.dumps({"messages": [{"role": "user", "content": "x"}]}).encode("utf-8")
        req = urllib.request.Request(
            f"{proxy.proxy_url}/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
            resp.read()
        assert seen["path"] == "/v1/chat/completions"
        assert seen["authorization"] == "Bearer UPSTREAM"
        assert seen["payload"]["logprobs"] is True
        assert seen["payload"]["top_logprobs"] == 20
        assert seen["payload"]["temperature"] == 0.0
        assert seen["payload"]["top_p"] == 0.95
        assert seen["payload"]["max_tokens"] == 131072
        assert proxy.captured_records()[0]["token"] == "D"
    finally:
        proxy.stop()
        upstream.shutdown()
        upstream.server_close()


def test_logprob_proxy_hoists_late_system_messages_for_strict_templates() -> None:
    seen: dict = {}

    class _CaptureHandler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            seen["payload"] = json.loads(self.rfile.read(length))
            body = {
                "choices": [
                    {
                        "message": {"content": "ok"},
                        "logprobs": {
                            "content": [
                                {
                                    "token": "ok",
                                    "logprob": -0.1,
                                    "top_logprobs": [{"token": "ok", "logprob": -0.1}],
                                }
                            ]
                        },
                    }
                ]
            }
            raw = json.dumps(body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args: object) -> None:
            pass

    upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _CaptureHandler)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    proxy = LogprobCaptureProxy(f"http://127.0.0.1:{upstream.server_address[1]}", 20)
    proxy.start()
    try:
        payload = json.dumps(
            {
                "messages": [
                    {"role": "system", "content": "base"},
                    {"role": "user", "content": "question"},
                    {"role": "assistant", "content": "tool plan"},
                    {"role": "system", "content": "late tool instruction"},
                    {"role": "user", "content": "continue"},
                ]
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{proxy.proxy_url}/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
            resp.read()

        assert seen["payload"]["messages"] == [
            {"role": "system", "content": "base\n\nlate tool instruction"},
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "tool plan"},
            {"role": "user", "content": "continue"},
        ]
        assert seen["payload"]["logprobs"] is True
        assert seen["payload"]["top_logprobs"] == 20
    finally:
        proxy.stop()
        upstream.shutdown()
        upstream.server_close()


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
        request_overrides: dict | None = None,
    ) -> None:
        self.upstream = normalize_openai_proxy_upstream(upstream)
        self.top_logprobs = top_logprobs
        self.bind_host = bind_host
        self.advertise_host = advertise_host
        self.client_timeout = client_timeout
        self.upstream_api_key = upstream_api_key
        self.proxy_api_key = proxy_api_key
        self.request_overrides = dict(request_overrides or {})
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
            "temperature": 0.0,
            "top_p": 0.95,
            "max_tokens": 65536,
            "enable_thinking": False,
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
    assert _FakeProxy.instances[0].request_overrides == {
        "chat_template_kwargs": {"enable_thinking": False},
        "temperature": 0.0,
        "top_p": 0.95,
        "max_tokens": 65536,
    }
    assert runtime.provider_api_base == "http://172.17.0.1:4567/v1"
    assert runtime.provider_api_key == "proxy-token"


def test_openclaw_runtime_logprob_proxy_init_is_concurrency_safe(monkeypatch) -> None:
    class _SlowProxy(_FakeProxy):
        def start(self) -> None:
            time.sleep(0.01)
            super().start()

    _FakeProxy.instances = []
    monkeypatch.setattr("alphadiana.agent.openclaw.LogprobCaptureProxy", _SlowProxy)
    monkeypatch.setattr("alphadiana.agent.openclaw.secrets.token_urlsafe", lambda _: "proxy-token")
    runtime = _FakeOpenClawRuntime()
    agent = OpenClawAgent()
    agent.setup(
        {
            "OPENAI_BASE_URL": "http://127.0.0.1:8011/v1",
            "OPENAI_API_KEY": "EMPTY",
            "OPENAI_MODEL_NAME": "Qwen/Qwen3.5-27B",
            "temperature": 0.0,
            "top_p": 0.95,
            "max_tokens": 65536,
            "capture_logprobs": True,
            "top_logprobs": 7,
        }
    )
    agent._runtime_manager = runtime

    with ThreadPoolExecutor(max_workers=8) as pool:
        proxies = list(pool.map(lambda _: agent._ensure_runtime_logprob_proxy(), range(16)))

    assert len(_FakeProxy.instances) == 1
    assert all(proxy is _FakeProxy.instances[0] for proxy in proxies)
    assert runtime.provider_api_base == "http://172.17.0.1:4567/v1"


def test_openclaw_setup_tracks_predeployed_gateway_logprob_proxy() -> None:
    proxy = _FakeProxy(
        "http://127.0.0.1:8011",
        7,
        request_overrides={"temperature": 0.0, "top_p": 0.95, "max_tokens": 65536},
    )
    proxy.start()
    agent = OpenClawAgent()
    agent.setup(
        {
            "api_base": "http://gateway-one/v1",
            "capture_logprobs": True,
            "_predeployed_gateway_api_base_by_sandbox_id": {
                "sandbox-one": "http://gateway-one/v1",
            },
            "_predeployed_logprob_proxies": {
                "sandbox-one": proxy,
            },
        }
    )

    assert agent._predeployed_gateway_api_base_by_sandbox_id == {
        "sandbox-one": "http://gateway-one/v1",
    }
    assert agent._predeployed_logprob_proxies == {"sandbox-one": proxy}
    assert proxy in agent._logprob_proxies

    agent.teardown()
    assert proxy.started is False


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
        if "zeroclaw " in command and " agent " in command and " -m " in command:
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
            "temperature": 0.0,
            "top_p": 0.95,
            "max_tokens": 65536,
            "capture_logprobs": True,
            "top_logprobs": 20,
        }
    )
    sandbox = _ZeroClawSandbox()
    task = BenchmarkTask(task_id="gpqa_0", problem="Pick D.", ground_truth="D", metadata={"execution_id": "z-proxy"})

    response = agent.solve(task, sandbox=sandbox)

    run_commands = [
        command
        for command in sandbox.commands
        if "zeroclaw " in command and " agent " in command and " -m " in command
    ]
    assert run_commands
    assert "OPENAI_BASE_URL=http://172.17.0.1:4567/v1" in run_commands[0]
    assert "OPENAI_API_KEY=proxy-token" in run_commands[0]
    assert "ZEROCLAW_PROVIDER=custom:http://172.17.0.1:4567/v1" in run_commands[0]
    assert "ZEROCLAW_CONFIG_DIR=/sandbox/work/.alphadiana_zeroclaw/z-proxy/home/.zeroclaw" in run_commands[0]
    assert _FakeProxy.instances[0].request_overrides == {
        "temperature": 0.0,
        "top_p": 0.95,
        "max_tokens": 65536,
    }
    config_text = next(
        content
        for path, content in sandbox.files.items()
        if path.endswith("/.zeroclaw/config.toml")
    )
    assert "provider_max_tokens = 65536" in config_text
    assert "top_p" not in config_text
    assert "logprobs" not in config_text
    assert response.metadata["logprobs_capture_status"] == "captured"
    assert response.metadata["logprob_probe_proxy_count"] == 1
    assert response.metadata["logprob_proxy_request_overrides"] == {
        "temperature": 0.0,
        "top_p": 0.95,
        "max_tokens": 65536,
    }
    assert response.token_entropy_stats["n_tokens"] == 1
    assert response.metadata["logprob_records"][0]["token"] == "D"
    config_path = next(path for path in sandbox.files if path.endswith("config.toml"))
    assert "[model_providers.custom.options]" not in sandbox.files[config_path]
