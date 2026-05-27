from __future__ import annotations

import json

from dataclasses import replace
from typing import Any

from alphadiana.agent.zeroclaw import ZeroClawAgent
from alphadiana.agent.zeroclaw_runtime import ZeroClawPodmanRuntimeManager, ZeroClawRuntimeManager
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.config.experiment_config import ExperimentConfig
from alphadiana.config.validator import ConfigValidator
from alphadiana.container_runtime import PodmanAgentRuntimeResult, PodmanAgentSpec
from alphadiana.results.result_store import ResultStore
from alphadiana.runner.runner import _make_gateway_runtime_manager, _needs_auto_rock_sandbox
from alphadiana.scorer.base import ScoreResult


class FakePodmanAgentRuntime:
    def __init__(self) -> None:
        self.started_spec: PodmanAgentSpec | None = None
        self.cleanup_called = False
        self.result = PodmanAgentRuntimeResult(
            container_id="zeroclaw-podman-cid",
            api_base="http://127.0.0.1:18081/v1",
            process_id="5678",
            logs="podman container logs\n",
            artifacts={
                "/tmp/zeroclaw-gateway.log": "bridge ready\n",
                "/tmp/zeroclaw-bridge-artifacts/last_request/task.txt": "Problem\n",
                "/tmp/zeroclaw-bridge-artifacts/last_request/zeroclaw_output.txt": "$$\\boxed{42}$$\n",
                "/tmp/zeroclaw-bridge-artifacts/last_request/runtime_trace.jsonl": '{"event_type":"llm_request"}\n',
                "/tmp/zeroclaw-bridge-artifacts/last_request/status.json": '{"ok":true}\n',
                "/tmp/zeroclaw-bridge-artifacts/last_request/provider_exchange_summary.json": '{"exchanges":[]}\n',
            },
            metadata={
                "container_engine": "podman",
                "adapter_name": "zeroclaw-podman",
                "image": "localhost/zeroclaw:test",
                "container_id": "zeroclaw-podman-cid",
                "published_api_base": "http://127.0.0.1:18081/v1",
            },
        )

    def start(self, spec: PodmanAgentSpec) -> PodmanAgentRuntimeResult:
        self.started_spec = spec
        return self.result

    def collect_artifacts(self) -> PodmanAgentRuntimeResult:
        return self.result

    def cleanup(self, *, check: bool = False) -> None:
        self.cleanup_called = True


class FakeLogprobProxy:
    instances: list["FakeLogprobProxy"] = []
    drain_payloads: list[list[dict[str, Any]]] = []

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
        request_overrides: dict[str, Any] | None = None,
    ) -> None:
        self.upstream = upstream.rstrip("/")
        self.top_logprobs = top_logprobs
        self.bind_host = bind_host
        self.advertise_host = advertise_host
        self.client_timeout = client_timeout
        self.upstream_api_key = upstream_api_key
        self.proxy_api_key = proxy_api_key
        self.request_overrides = dict(request_overrides or {})
        self.started = False
        FakeLogprobProxy.instances.append(self)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    @property
    def proxy_url(self) -> str:
        return f"http://{self.advertise_host or '127.0.0.1'}:4567"

    @property
    def local_url(self) -> str:
        return "http://127.0.0.1:4567"

    def drain_records(self) -> list[dict[str, Any]]:
        if FakeLogprobProxy.drain_payloads:
            return FakeLogprobProxy.drain_payloads.pop(0)
        return []


def _logprob_record() -> dict[str, Any]:
    return {
        "token_index": 0,
        "token": "42",
        "logprob": -0.1,
        "top_logprobs": [{"token": "42", "logprob": -0.1}],
    }


def _podman_config(fake_runtime: FakePodmanAgentRuntime) -> dict[str, Any]:
    return {
        "runtime_backend": "podman",
        "podman_image": "localhost/zeroclaw:test",
        "model": "qwen-test",
        "api_base": "http://provider.test/v1",
        "api_key": "sk-secret",
        "gateway_token": "ZEROCLAW_TEST",
        "request_timeout": 99,
        "podman_network": "slirp4netns:allow_host_loopback=true",
        "provider_max_tokens": 2048,
        "top_p": 0.9,
        "enable_thinking": False,
        "reasoning_enabled": True,
        "reasoning_effort": "high",
        "_podman_agent_runtime": fake_runtime,
    }


def test_zeroclaw_podman_runtime_builds_spec_and_artifacts(monkeypatch) -> None:
    fake_runtime = FakePodmanAgentRuntime()
    manager = ZeroClawPodmanRuntimeManager(_podman_config(fake_runtime))
    monkeypatch.setattr(manager, "_warmup_podman_gateway", lambda _api_base: None)

    info = manager.ensure_ready(None)

    spec = fake_runtime.started_spec
    assert spec is not None
    assert spec.adapter_name == "zeroclaw-podman"
    assert spec.image == "localhost/zeroclaw:test"
    assert spec.network == "slirp4netns:allow_host_loopback=true"
    assert spec.exposed_port == 8080
    assert spec.api_base_suffix == "/v1"
    assert spec.run_command == "python /tmp/zeroclaw_bridge.py"
    assert spec.files[0].container_path == "/tmp/zeroclaw_bridge.py"
    assert b"Minimal OpenAI-compatible bridge" in spec.files[0].content  # type: ignore[operator]
    assert spec.env["ZEROCLAW_GATEWAY_TOKEN"] == "ZEROCLAW_TEST"
    assert spec.env["ZEROCLAW_BRIDGE_HOST"] == "0.0.0.0"
    assert spec.env["ZEROCLAW_PROVIDER_TIMEOUT_SECS"] == "99"
    assert spec.env["ZEROCLAW_PROVIDER_MAX_TOKENS"] == "2048"
    assert spec.env["ZEROCLAW_REASONING_ENABLED"] == "true"
    assert spec.env["ZEROCLAW_REASONING_EFFORT"] == "high"
    assert spec.env["OPENAI_BASE_URL"] == "http://provider.test/v1"
    assert spec.env["OPENAI_API_KEY"] == "sk-secret"
    assert "/tmp/zeroclaw-gateway.log" in spec.artifact_paths
    assert "/tmp/zeroclaw-bridge-artifacts/last_request/status.json" in spec.artifact_paths
    assert "/tmp/zeroclaw-bridge-artifacts/last_request/provider_exchange_summary.json" in spec.artifact_paths
    assert getattr(spec.healthcheck, "token") == "ZEROCLAW_TEST"
    assert info["api_base"] == "http://127.0.0.1:18081/v1"
    assert info["gateway_url"] == "http://127.0.0.1:18081/v1/chat/completions"
    assert info["container_engine"] == "podman"
    assert info["metadata"]["logprob_support"] == "disabled"

    artifacts = manager.collect_artifacts(None)
    assert artifacts["gateway_log_excerpt"] == "bridge ready\n"
    assert artifacts["workspace_file_contents"]["zeroclaw_output.txt"] == "$$\\boxed{42}$$\n"
    assert artifacts["workspace_file_contents"]["status.json"] == '{"ok":true}\n'
    assert artifacts["workspace_file_contents"]["provider_exchange_summary.json"] == '{"exchanges":[]}\n'
    assert artifacts["sandbox_metadata"]["container_engine"] == "podman"

    manager.teardown()
    assert fake_runtime.cleanup_called is True


def test_zeroclaw_podman_runtime_routes_provider_through_logprob_proxy(monkeypatch) -> None:
    FakeLogprobProxy.instances = []
    FakeLogprobProxy.drain_payloads = []
    monkeypatch.setattr("alphadiana.agent.zeroclaw_runtime.LogprobCaptureProxy", FakeLogprobProxy)
    monkeypatch.setattr("alphadiana.agent.zeroclaw_runtime.secrets.token_urlsafe", lambda _: "proxy-token")
    fake_runtime = FakePodmanAgentRuntime()
    config = _podman_config(fake_runtime)
    config["_logprob_capture"] = {"enabled": True, "top_logprobs": 20}
    manager = ZeroClawPodmanRuntimeManager(config)
    monkeypatch.setattr(manager, "_warmup_podman_gateway", lambda _api_base: None)

    info = manager.ensure_ready(None)

    spec = fake_runtime.started_spec
    assert spec is not None
    assert info["metadata"]["logprob_support"] == "enabled"
    assert info["metadata"]["logprob_proxy_enabled"] is True
    assert spec.env["OPENAI_BASE_URL"] == "http://host.containers.internal:4567/v1"
    assert spec.env["OPENAI_API_KEY"] == "proxy-token"
    assert spec.env["OPENROUTER_API_KEY"] == "proxy-token"
    assert spec.env["ZEROCLAW_PROVIDER"] == "custom:http://host.containers.internal:4567/v1"
    assert FakeLogprobProxy.instances[0].upstream == "http://provider.test/v1"
    assert FakeLogprobProxy.instances[0].upstream_api_key == "sk-secret"
    assert FakeLogprobProxy.instances[0].proxy_api_key == "proxy-token"
    assert FakeLogprobProxy.instances[0].request_overrides == {
        "chat_template_kwargs": {"enable_thinking": False},
        "reasoning": {"enabled": True},
        "temperature": 0.0,
        "top_p": 0.9,
        "max_tokens": 2048,
    }

    manager.teardown()


def test_zeroclaw_agent_can_use_podman_bridge_without_sandbox(monkeypatch) -> None:
    fake_runtime = FakePodmanAgentRuntime()
    agent = ZeroClawAgent()
    agent.setup(_podman_config(fake_runtime))
    monkeypatch.setattr(agent._runtime_manager, "_warmup_podman_gateway", lambda _api_base: None)

    class _Response:
        status_code = 200
        text = ""

        def json(self) -> dict[str, Any]:
            return {
                "choices": [
                    {"message": {"content": "$$\\boxed{42}$$"}, "finish_reason": "stop"}
                ],
                "usage": {},
            }

    def fake_post(url: str, **kwargs: Any) -> _Response:
        assert url == "http://127.0.0.1:18081/v1/chat/completions"
        assert kwargs["headers"]["Authorization"] == "bearer ZEROCLAW_TEST"
        assert kwargs["json"]["model"] == "qwen-test"
        return _Response()

    monkeypatch.setattr("httpx.post", fake_post)
    task = BenchmarkTask(task_id="tiny", problem="What is 6*7?", ground_truth="42")

    response = agent.solve(task, sandbox=None)

    assert response.answer == "42"
    assert response.gateway_url == "http://127.0.0.1:18081/v1/chat/completions"
    assert response.metadata["transport"] == "zeroclaw_podman_bridge"
    assert response.metadata["container_engine"] == "podman"
    assert response.workspace_file_contents["zeroclaw_output.txt"] == "$$\\boxed{42}$$\n"


def test_zeroclaw_podman_logprob_proxy_records_write_sidecars(monkeypatch, tmp_path) -> None:
    FakeLogprobProxy.instances = []
    FakeLogprobProxy.drain_payloads = [[], [], [_logprob_record()]]
    monkeypatch.setattr("alphadiana.agent.zeroclaw_runtime.LogprobCaptureProxy", FakeLogprobProxy)
    monkeypatch.setattr("alphadiana.agent.zeroclaw_runtime.secrets.token_urlsafe", lambda _: "proxy-token")
    fake_runtime = FakePodmanAgentRuntime()
    config = _podman_config(fake_runtime)
    config["capture_logprobs"] = True
    config["top_logprobs"] = 20
    agent = ZeroClawAgent()
    agent.setup(config)
    monkeypatch.setattr(agent._runtime_manager, "_warmup_podman_gateway", lambda _api_base: None)

    class _Response:
        status_code = 200
        text = ""

        def json(self) -> dict[str, Any]:
            return {
                "choices": [
                    {"message": {"content": "$$\\boxed{42}$$"}, "finish_reason": "stop"}
                ],
                "usage": {},
            }

    monkeypatch.setattr("httpx.post", lambda *_args, **_kwargs: _Response())
    task = BenchmarkTask(task_id="tiny", problem="What is 6*7?", ground_truth="42")

    response = agent.solve(task, sandbox=None)

    assert response.metadata["logprobs_capture_status"] == "captured"
    assert response.metadata["logprob_probe_proxy_count"] == 1
    assert response.metadata["logprob_source"] == "provider_proxy"
    assert response.metadata["logprob_records"] == [_logprob_record()]
    assert response.token_entropy_stats["n_tokens"] == 1

    store = ResultStore(str(tmp_path), "run_zc_podman")
    store.append(
        task,
        response,
        ScoreResult(correct=True, score=1.0, expected="42", predicted="42"),
    )
    record = store.load()[0]
    assert record["logprobs_path"] == "run_zc_podman/logprobs/tiny.jsonl"
    assert record["logprobs_int16_path"] == "run_zc_podman/logprobs_int16/tiny.jsonl"
    assert record["metadata"]["logprob_records"] == 1
    assert (tmp_path / "run_zc_podman" / "logprobs" / "tiny.jsonl").exists()


def test_zeroclaw_podman_bridge_error_records_runtime_provenance(monkeypatch) -> None:
    fake_runtime = FakePodmanAgentRuntime()
    agent = ZeroClawAgent()
    agent.setup(_podman_config(fake_runtime))
    monkeypatch.setattr(agent._runtime_manager, "_warmup_podman_gateway", lambda _api_base: None)

    class _Response:
        status_code = 500
        text = ""

        def json(self) -> dict[str, Any]:
            return {
                "error": {
                    "message": "OpenRouter transport error while reading response body",
                    "type": "RuntimeError",
                }
            }

    monkeypatch.setattr("httpx.post", lambda *_args, **_kwargs: _Response())
    task = BenchmarkTask(task_id="imo_0", problem="Solve.", ground_truth="42")

    try:
        agent.solve(task, sandbox=None)
    except RuntimeError as exc:
        partial = getattr(exc, "partial_response", None)
        error_type = getattr(exc, "error_type", "")
    else:
        raise AssertionError("Expected ZeroClaw bridge error")

    assert partial is not None
    assert error_type == "RuntimeError"
    assert partial.metadata["failure_reason"] == "RuntimeError"
    assert partial.metadata["container_engine"] == "podman"
    assert partial.metadata["adapter_name"] == "zeroclaw-podman"
    assert partial.sandbox_metadata["container_engine"] == "podman"


def test_zeroclaw_podman_bridge_length_empty_output_scores_zero(monkeypatch) -> None:
    fake_runtime = FakePodmanAgentRuntime()
    fake_runtime.result = replace(fake_runtime.result, artifacts={
        "/tmp/zeroclaw-gateway.log": "bridge ready\n",
        "/tmp/zeroclaw-bridge-artifacts/last_request/task.txt": "Problem\n",
        "/tmp/zeroclaw-bridge-artifacts/last_request/zeroclaw_output.txt": (
            "2026-05-26T20:07:19Z INFO zeroclaw::agent::loop_: Memory initialized\n"
        ),
        "/tmp/zeroclaw-bridge-artifacts/last_request/status.json": (
            '{"returncode":0,"timed_out":false,"provider_exchange_count":1}\n'
        ),
        "/tmp/zeroclaw-bridge-artifacts/last_request/provider_exchange_summary.json": (
            json.dumps({
                "exchanges": [
                    {
                        "response_status": 200,
                        "response": {
                            "choices": [
                                {
                                    "finish_reason": "length",
                                    "content_length": 0,
                                    "tool_call_count": 0,
                                }
                            ],
                            "usage": {
                                "prompt_tokens": 13054,
                                "completion_tokens": 8192,
                                "total_tokens": 21246,
                            },
                        },
                    }
                ]
            })
            + "\n"
        ),
    })
    agent = ZeroClawAgent()
    agent.setup(_podman_config(fake_runtime))
    monkeypatch.setattr(agent._runtime_manager, "_warmup_podman_gateway", lambda _api_base: None)

    class _Response:
        status_code = 500
        text = ""

        def json(self) -> dict[str, Any]:
            return {
                "error": {
                    "message": "ZeroClaw produced no assistant output; stdout contained only runtime/provider logs.",
                    "type": "RuntimeError",
                }
            }

    monkeypatch.setattr("httpx.post", lambda *_args, **_kwargs: _Response())
    task = BenchmarkTask(task_id="gpqa_6", problem="Solve.", ground_truth="C")

    response = agent.solve(task, sandbox=None)

    assert response.answer is None
    assert response.finish_reason == "length"
    assert response.metadata["failure_reason"] == "token_budget_exhausted"
    assert response.metadata["zeroclaw_empty_assistant_scored_zero"] is True
    assert response.metadata["zeroclaw_provider_completion_tokens"] == 8192
    assert response.token_usage["completion_tokens"] == 8192
    assert "provider_exchange_summary.json" in response.workspace_file_contents


def test_zeroclaw_podman_capture_logprobs_config_validates() -> None:
    config = ExperimentConfig(
        agent_name="zeroclaw",
        agent_version="0.6.9",
        agent_config={
            "runtime_backend": "podman",
            "podman_image": "localhost/zeroclaw:test",
            "api_base": "http://provider.test/v1",
            "api_key": "sk-secret",
            "model": "qwen-test",
            "capture_logprobs": True,
            "top_logprobs": 20,
        },
        benchmark_name="aime",
        scorer_name="numeric",
    )

    assert ConfigValidator().validate(config) == []


def test_zeroclaw_podman_runtime_does_not_trigger_rock_autosandbox() -> None:
    config = ExperimentConfig(
        agent_name="zeroclaw",
        agent_version="0.6.9",
        agent_config={
            "runtime_backend": "podman",
            "podman_image": "localhost/zeroclaw:test",
            "rock_image": "zeroclaw-reasoning:0.6.9",
        },
        benchmark_name="aime",
        scorer_name="numeric",
    )

    assert _needs_auto_rock_sandbox(config) is False


def test_zeroclaw_runtime_manager_selection_preserves_legacy_rock() -> None:
    podman_config = ExperimentConfig(
        agent_name="zeroclaw",
        agent_version="0.6.9",
        agent_config={
            "runtime_backend": "podman",
            "podman_image": "localhost/zeroclaw:test",
            "api_base": "http://provider.test/v1",
            "api_key": "sk-secret",
            "model": "qwen-test",
        },
        benchmark_name="aime",
        scorer_name="numeric",
    )
    rock_config = ExperimentConfig(
        agent_name="zeroclaw",
        agent_version="0.6.9",
        agent_config={
            "rock_image": "zeroclaw-reasoning:0.6.9",
            "api_base": "http://provider.test/v1",
            "api_key": "sk-secret",
            "model": "qwen-test",
        },
        benchmark_name="aime",
        scorer_name="numeric",
    )

    assert isinstance(_make_gateway_runtime_manager(podman_config), ZeroClawPodmanRuntimeManager)
    assert isinstance(_make_gateway_runtime_manager(rock_config), ZeroClawRuntimeManager)
    assert not isinstance(_make_gateway_runtime_manager(rock_config), ZeroClawPodmanRuntimeManager)
