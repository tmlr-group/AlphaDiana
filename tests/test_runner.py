"""Tests for TaskDispatcher and Runner."""

import json
from types import SimpleNamespace

import httpx
import pytest

from alphadiana.agent.base import AgentResponse
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.config.experiment_config import ExperimentConfig
from alphadiana.runner.runner import (
    Runner,
    _is_gateway_autodeploy_agent,
    _merge_artifact_manifests,
    _needs_auto_rock_sandbox,
    _openclaw_integrity_guard_reason,
    _predeployed_session_failure_reason,
    _sanitize_sandbox_metadata,
    _sanitize_success_sandbox_metadata,
)
from alphadiana.runner.task_dispatcher import TaskDispatcher
from alphadiana.sandbox.pool import SandboxPool


class _FakeTask:
    """Minimal task-like object for testing."""

    def __init__(self, task_id: str):
        self.task_id = task_id


class TestTaskDispatcherSequential:
    """Verify TaskDispatcher sequential execution."""

    def test_dispatch_all_success(self):
        """All tasks execute successfully."""
        tasks = [_FakeTask(f"t{i}") for i in range(5)]
        results_collected = []

        def solve_fn(task):
            result = {"task_id": task.task_id, "answer": 42}
            results_collected.append(result)
            return result

        dispatcher = TaskDispatcher(max_concurrent=1)
        outcomes = dispatcher.dispatch(tasks, solve_fn)

        assert len(outcomes) == 5
        assert all(o["success"] for o in outcomes)
        assert len(results_collected) == 5

        task_ids = {o["task_id"] for o in outcomes}
        assert task_ids == {"t0", "t1", "t2", "t3", "t4"}

    def test_dispatch_preserves_order(self):
        """Task order preserved during sequential execution."""
        tasks = [_FakeTask(f"t{i}") for i in range(3)]
        order = []

        def solve_fn(task):
            order.append(task.task_id)
            return {"task_id": task.task_id}

        dispatcher = TaskDispatcher(max_concurrent=1)
        dispatcher.dispatch(tasks, solve_fn)

        assert order == ["t0", "t1", "t2"]


class TestTaskDispatcherConcurrent:
    """Verify TaskDispatcher concurrent execution."""

    def test_dispatch_concurrent(self):
        """All tasks complete under concurrent execution."""
        tasks = [_FakeTask(f"t{i}") for i in range(6)]

        def solve_fn(task):
            return {"task_id": task.task_id, "answer": int(task.task_id[1:])}

        dispatcher = TaskDispatcher(max_concurrent=2)
        outcomes = dispatcher.dispatch(tasks, solve_fn)

        assert len(outcomes) == 6
        assert all(o["success"] for o in outcomes)

        task_ids = {o["task_id"] for o in outcomes}
        assert task_ids == {"t0", "t1", "t2", "t3", "t4", "t5"}


class TestTaskDispatcherErrorIsolation:
    """Verify error isolation: one task failure does not affect others."""

    def test_error_isolation(self):
        """Failed task does not affect successful tasks."""
        tasks = [_FakeTask("ok1"), _FakeTask("fail"), _FakeTask("ok2")]

        def solve_fn(task):
            if task.task_id == "fail":
                raise ValueError("intentional failure")
            return {"task_id": task.task_id}

        dispatcher = TaskDispatcher(max_concurrent=1)
        outcomes = dispatcher.dispatch(tasks, solve_fn)

        assert len(outcomes) == 3
        succeeded = [o for o in outcomes if o["success"]]
        failed = [o for o in outcomes if not o["success"]]
        assert len(succeeded) == 2
        assert len(failed) == 1
        assert failed[0]["task_id"] == "fail"
        assert "intentional failure" in failed[0]["error"]

    def test_error_isolation_concurrent(self):
        """Error isolation in concurrent mode."""
        tasks = [_FakeTask("ok1"), _FakeTask("fail"), _FakeTask("ok2")]

        def solve_fn(task):
            if task.task_id == "fail":
                raise RuntimeError("boom")
            return {"task_id": task.task_id}

        dispatcher = TaskDispatcher(max_concurrent=3)
        outcomes = dispatcher.dispatch(tasks, solve_fn)

        assert len(outcomes) == 3
        succeeded = [o for o in outcomes if o["success"]]
        failed = [o for o in outcomes if not o["success"]]
        assert len(succeeded) == 2
        assert len(failed) == 1

    def test_empty_tasks(self):
        """Empty task list returns empty results."""
        dispatcher = TaskDispatcher(max_concurrent=1)
        outcomes = dispatcher.dispatch([], lambda t: {})
        assert outcomes == []

    def test_max_concurrent_clamped(self):
        """max_concurrent < 1 is clamped to 1."""
        dispatcher = TaskDispatcher(max_concurrent=0)
        assert dispatcher.max_concurrent == 1
        dispatcher2 = TaskDispatcher(max_concurrent=-1)
        assert dispatcher2.max_concurrent == 1

    def test_retry_filter_blocks_nonrecoverable_error(self, monkeypatch):
        """Retry filters can keep preserved task errors from being rerun."""
        monkeypatch.setattr("alphadiana.runner.task_dispatcher.time.sleep", lambda _: None)
        calls = 0

        def solve_fn(task):
            nonlocal calls
            calls += 1
            exc = RuntimeError("output cap reached")
            exc.retryable_task_failure = False
            raise exc

        dispatcher = TaskDispatcher(
            max_concurrent=1,
            task_retries=2,
            retry_if=lambda exc: bool(getattr(exc, "retryable_task_failure", False)),
        )
        outcomes = dispatcher.dispatch([_FakeTask("t0")], solve_fn)

        assert calls == 1
        assert outcomes[0]["success"] is False
        assert "output cap reached" in outcomes[0]["error"]

    def test_retry_filter_allows_recoverable_error(self, monkeypatch):
        """Recoverable infrastructure failures are retried."""
        monkeypatch.setattr("alphadiana.runner.task_dispatcher.time.sleep", lambda _: None)
        calls = 0

        def solve_fn(task):
            nonlocal calls
            calls += 1
            if calls == 1:
                exc = RuntimeError("gateway connection refused")
                exc.retryable_task_failure = True
                raise exc
            return {"task_id": task.task_id, "answer": "42"}

        dispatcher = TaskDispatcher(
            max_concurrent=1,
            task_retries=2,
            retry_if=lambda exc: bool(getattr(exc, "retryable_task_failure", False)),
        )
        outcomes = dispatcher.dispatch([_FakeTask("t0")], solve_fn)

        assert calls == 2
        assert outcomes[0]["success"] is True


def test_openclaw_direct_api_base_disables_gateway_autodeploy() -> None:
    config = ExperimentConfig(
        agent_name="openclaw",
        agent_version="2026.3.20",
        agent_config={
            "api_base": "http://127.0.0.1:18083/v1",
            "rock_agent_config_path": "openclaw_deploy/rock_agent_config.prebuilt.yaml",
            "openclaw_config_path": "openclaw_deploy/openclaw.json",
        },
        benchmark_name="imo_answerbench",
        scorer_name="math_verify",
    )

    assert _is_gateway_autodeploy_agent(config) is False


def test_merge_artifact_manifests_preserves_existing_file_aliases() -> None:
    existing = {
        "files": {
            "response_stream": "zeroclaw_output.txt",
            "stdout_log": "zeroclaw_output.txt",
        },
        "attachments": [],
    }
    incoming = {
        "files": {
            "bridge_log_source": "/tmp/zeroclaw-gateway.log",
        },
        "local_artifact_root": "task_0",
    }

    merged = _merge_artifact_manifests(existing, incoming)

    assert merged["files"]["response_stream"] == "zeroclaw_output.txt"
    assert merged["files"]["stdout_log"] == "zeroclaw_output.txt"
    assert merged["files"]["bridge_log_source"] == "/tmp/zeroclaw-gateway.log"
    assert merged["local_artifact_root"] == "task_0"


def test_openclaw_gateway_pool_disables_gateway_autodeploy() -> None:
    config = ExperimentConfig(
        agent_name="openclaw",
        agent_version="2026.3.20",
        agent_config={
            "gateway_pool": ["http://127.0.0.1:18083/v1", "http://127.0.0.1:18084/v1"],
            "rock_agent_config_path": "openclaw_deploy/rock_agent_config.prebuilt.yaml",
            "openclaw_config_path": "openclaw_deploy/openclaw.json",
        },
        benchmark_name="imo_answerbench",
        scorer_name="math_verify",
    )

    assert _is_gateway_autodeploy_agent(config) is False


def test_zeroclaw_rock_image_requires_auto_rock_sandbox_without_gateway_autodeploy() -> None:
    config = ExperimentConfig(
        agent_name="zeroclaw",
        agent_version="0.6.9",
        agent_config={
            "model": "demo-model",
            "api_base": "https://example.test/v1",
            "api_key": "sk-test",
            "rock_image": "zeroclaw-reasoning:0.6.9",
        },
        benchmark_name="aime",
        scorer_name="numeric",
    )

    assert _is_gateway_autodeploy_agent(config) is False
    assert _needs_auto_rock_sandbox(config) is True


def test_sanitize_success_sandbox_metadata_drops_command_history() -> None:
    metadata = {
        "sandbox_id": "sbx-1",
        "command_history": [
            {"command": "OPENAI_API_KEY=sk-live abc"},
        ],
        "artifact_collection_history": [],
    }

    sanitized = _sanitize_success_sandbox_metadata(metadata)

    assert sanitized == {"sandbox_id": "sbx-1"}


def test_sanitize_error_sandbox_metadata_redacts_secret_env_assignments() -> None:
    metadata = {
        "sandbox_id": "sbx-2",
        "command_history": [
            {
                "command": (
                    "OPENAI_API_KEY=sk-live "
                    "OPENROUTER_API_KEY='sk-router' "
                    "ZEROCLAW_API_KEY=\"sk-zero\" zeroclaw agent"
                ),
                "stdout": "OPENAI_API_KEY=sk-live",
                "stderr": "ZEROCLAW_API_KEY=sk-zero",
            }
        ],
        "artifact_collection_history": [
            {"command": "OPENAI_API_KEY=sk-live cat file"}
        ],
    }

    sanitized = _sanitize_sandbox_metadata(metadata, keep_command_history=True)

    history = sanitized["command_history"][0]
    assert "sk-live" not in history["command"]
    assert "sk-router" not in history["command"]
    assert "sk-zero" not in history["command"]
    assert "OPENAI_API_KEY=<redacted>" in history["command"]
    assert "OPENROUTER_API_KEY=<redacted>" in history["command"]
    assert "ZEROCLAW_API_KEY=<redacted>" in history["command"]
    assert history["stdout"] == "OPENAI_API_KEY=<redacted>"
    assert history["stderr"] == "ZEROCLAW_API_KEY=<redacted>"
    assert (
        sanitized["artifact_collection_history"][0]["command"]
        == "OPENAI_API_KEY=<redacted> cat file"
    )


def test_runner_uses_live_predeployed_sessions_for_gateway_pool(monkeypatch, tmp_path):
    class _FakeBenchmark:
        def load_tasks(self, config):
            return [
                BenchmarkTask(task_id="aime_0", problem="Q0", ground_truth="42"),
                BenchmarkTask(task_id="aime_1", problem="Q1", ground_truth="42"),
            ]

    class _FakePredeployedSession:
        def __init__(self, sandbox_id: str):
            self.sandbox_id = sandbox_id
            self.reset_calls = 0
            self.close_calls = 0

        def proxy_v1_base(self) -> str:
            return f"http://proxy.test/apis/envs/sandbox/v1/sandboxes/{self.sandbox_id}/proxy/v1"

        def metadata(self) -> dict:
            return {
                "sandbox_id": self.sandbox_id,
                "proxy_base_url": self.proxy_v1_base(),
            }

        def reset(self) -> None:
            self.reset_calls += 1

        def close(self) -> None:
            self.close_calls += 1

    class _FakeRockSandboxBackend:
        def setup(self, config) -> None:
            self.config = config

        def create_session(self):
            return sessions.pop(0)

    class _FakeGatewayRuntimeManager:
        def ensure_ready(self, session):
            return {
                "sandbox_id": session.sandbox_id,
                "gateway_url": f"{session.proxy_v1_base()}/chat/completions",
                "api_base": session.proxy_v1_base(),
            }

    class _FakeGatewayAgent:
        def __init__(self):
            self.solve_sandbox_ids: list[str | None] = []

        def setup(self, config) -> None:
            self.config = dict(config)

        def solve(self, task, sandbox=None):
            sandbox_id = getattr(sandbox, "sandbox_id", None)
            self.solve_sandbox_ids.append(sandbox_id)
            return AgentResponse(
                answer="42",
                sandbox_id=sandbox_id or "",
                metadata={},
            )

        def teardown(self) -> None:
            pass

    created_sessions = [
        _FakePredeployedSession("sandbox-a"),
        _FakePredeployedSession("sandbox-b"),
    ]
    sessions = list(created_sessions)

    monkeypatch.setattr(
        "alphadiana.runner.runner.BenchmarkRegistry.get",
        lambda name: _FakeBenchmark,
    )
    monkeypatch.setattr(
        "alphadiana.runner.runner.AgentRegistry.get",
        lambda name: _FakeGatewayAgent,
    )
    monkeypatch.setattr(
        "alphadiana.runner.runner.SandboxRegistry.get",
        lambda name: _FakeRockSandboxBackend,
    )
    monkeypatch.setattr(
        "alphadiana.runner.runner._make_gateway_runtime_manager",
        lambda config: _FakeGatewayRuntimeManager(),
    )

    config = ExperimentConfig(
        run_id="runner-predeployed-gateway-pool",
        agent_name="openclaw",
        agent_version="2026.3.20",
        agent_config={
            "api_key": "sk-test",
            "rock_agent_config_path": "openclaw_deploy/rock_agent_config.prebuilt.yaml",
            "openclaw_config_path": "openclaw_deploy/openclaw.json",
            "admin_base_url": "http://127.0.0.1:9000",
            "proxy_base_url": "http://127.0.0.1:9005/apis/envs/sandbox/v1",
            "reuse_predeployed_sandboxes": True,
        },
        benchmark_name="aime",
        benchmark_config={},
        scorer_name="numeric",
        scorer_config={},
        max_concurrent=2,
        output_dir=str(tmp_path),
    )

    runner = Runner(config)
    runner.setup()
    try:
        summary = runner.run()
    finally:
        runner.teardown()

    assert summary.completed == 2
    assert summary.total_tasks == 2
    assert sorted(runner.agent.solve_sandbox_ids) == ["sandbox-a", "sandbox-b"]
    assert [session.reset_calls for session in created_sessions] == [1, 1]
    assert [session.close_calls for session in created_sessions] == [1, 1]


def test_runner_defaults_to_fresh_predeployed_sandboxes(monkeypatch, tmp_path):
    class _FakeBenchmark:
        def load_tasks(self, config):
            return [
                BenchmarkTask(task_id="aime_0", problem="Q0", ground_truth="42"),
                BenchmarkTask(task_id="aime_1", problem="Q1", ground_truth="42"),
            ]

    class _FakePredeployedSession:
        def __init__(self, sandbox_id: str):
            self.sandbox_id = sandbox_id
            self.reset_calls = 0
            self.close_calls = 0

        def proxy_v1_base(self) -> str:
            return f"http://proxy.test/apis/envs/sandbox/v1/sandboxes/{self.sandbox_id}/proxy/v1"

        def metadata(self) -> dict:
            return {
                "sandbox_id": self.sandbox_id,
                "proxy_base_url": self.proxy_v1_base(),
            }

        def reset(self) -> None:
            self.reset_calls += 1

        def close(self) -> None:
            self.close_calls += 1

    created_sessions: list[_FakePredeployedSession] = []

    class _FakeRockSandboxBackend:
        def setup(self, config) -> None:
            self.config = config

        def create_session(self):
            session = _FakePredeployedSession(f"sandbox-{len(created_sessions)}")
            created_sessions.append(session)
            return session

    class _FakeGatewayRuntimeManager:
        def ensure_ready(self, session):
            return {
                "sandbox_id": session.sandbox_id,
                "gateway_url": f"{session.proxy_v1_base()}/chat/completions",
                "api_base": session.proxy_v1_base(),
            }

    class _FakeGatewayAgent:
        def __init__(self):
            self.solve_sandbox_ids: list[str | None] = []

        def setup(self, config) -> None:
            self.config = dict(config)

        def solve(self, task, sandbox=None):
            sandbox_id = getattr(sandbox, "sandbox_id", None)
            self.solve_sandbox_ids.append(sandbox_id)
            return AgentResponse(answer="42", sandbox_id=sandbox_id or "", metadata={})

        def teardown(self) -> None:
            pass

    monkeypatch.setattr("alphadiana.runner.runner.BenchmarkRegistry.get", lambda name: _FakeBenchmark)
    monkeypatch.setattr("alphadiana.runner.runner.AgentRegistry.get", lambda name: _FakeGatewayAgent)
    monkeypatch.setattr("alphadiana.runner.runner.SandboxRegistry.get", lambda name: _FakeRockSandboxBackend)
    monkeypatch.setattr(
        "alphadiana.runner.runner._make_gateway_runtime_manager",
        lambda config: _FakeGatewayRuntimeManager(),
    )

    config = ExperimentConfig(
        run_id="runner-fresh-predeployed-default",
        agent_name="openclaw",
        agent_version="2026.3.20",
        agent_config={
            "api_key": "sk-test",
            "rock_agent_config_path": "openclaw_deploy/rock_agent_config.prebuilt.yaml",
            "openclaw_config_path": "openclaw_deploy/openclaw.json",
            "admin_base_url": "http://127.0.0.1:9000",
            "proxy_base_url": "http://127.0.0.1:9005/apis/envs/sandbox/v1",
            "predeploy_stagger_seconds": 0,
        },
        benchmark_name="aime",
        benchmark_config={},
        scorer_name="numeric",
        scorer_config={},
        max_concurrent=1,
        output_dir=str(tmp_path),
    )

    runner = Runner(config)
    runner.setup()
    try:
        summary = runner.run()
    finally:
        runner.teardown()

    assert summary.completed == 2
    assert runner.agent.solve_sandbox_ids[0] != runner.agent.solve_sandbox_ids[1]
    assert all(session.reset_calls == 0 for session in created_sessions)
    used_ids = set(runner.agent.solve_sandbox_ids)
    used_sessions = [s for s in created_sessions if s.sandbox_id in used_ids]
    assert len(used_sessions) == 2
    assert all(session.close_calls == 1 for session in used_sessions)
    records = runner.result_store.load()
    assert {record["isolation_mode"] for record in records} == {"fresh_predeployed_pool"}
    assert {record["reuse_predeployed_sandboxes"] for record in records} == {False}


def test_runner_fresh_predeployed_standby_sandboxes(monkeypatch, tmp_path):
    class _FakeBenchmark:
        def load_tasks(self, config):
            return [BenchmarkTask(task_id="aime_0", problem="Q0", ground_truth="42")]

    class _FakePredeployedSession:
        def __init__(self, sandbox_id: str):
            self.sandbox_id = sandbox_id
            self.reset_calls = 0
            self.close_calls = 0

        def proxy_v1_base(self) -> str:
            return f"http://proxy.test/apis/envs/sandbox/v1/sandboxes/{self.sandbox_id}/proxy/v1"

        def metadata(self) -> dict:
            return {
                "sandbox_id": self.sandbox_id,
                "proxy_base_url": self.proxy_v1_base(),
            }

        def reset(self) -> None:
            self.reset_calls += 1

        def close(self) -> None:
            self.close_calls += 1

    created_sessions: list[_FakePredeployedSession] = []

    class _FakeRockSandboxBackend:
        def setup(self, config) -> None:
            self.config = config

        def create_session(self):
            session = _FakePredeployedSession(f"sandbox-{len(created_sessions)}")
            created_sessions.append(session)
            return session

    class _FakeGatewayRuntimeManager:
        def ensure_ready(self, session):
            return {
                "sandbox_id": session.sandbox_id,
                "gateway_url": f"{session.proxy_v1_base()}/chat/completions",
                "api_base": session.proxy_v1_base(),
            }

    class _FakeGatewayAgent:
        def __init__(self):
            self.solve_sandbox_ids: list[str | None] = []

        def setup(self, config) -> None:
            self.config = dict(config)

        def solve(self, task, sandbox=None):
            sandbox_id = getattr(sandbox, "sandbox_id", None)
            self.solve_sandbox_ids.append(sandbox_id)
            return AgentResponse(answer="42", sandbox_id=sandbox_id or "", metadata={})

        def teardown(self) -> None:
            pass

    monkeypatch.setattr("alphadiana.runner.runner.BenchmarkRegistry.get", lambda name: _FakeBenchmark)
    monkeypatch.setattr("alphadiana.runner.runner.AgentRegistry.get", lambda name: _FakeGatewayAgent)
    monkeypatch.setattr("alphadiana.runner.runner.SandboxRegistry.get", lambda name: _FakeRockSandboxBackend)
    monkeypatch.setattr(
        "alphadiana.runner.runner._make_gateway_runtime_manager",
        lambda config: _FakeGatewayRuntimeManager(),
    )

    config = ExperimentConfig(
        run_id="runner-fresh-predeployed-standby",
        agent_name="openclaw",
        agent_version="2026.3.20",
        agent_config={
            "api_key": "sk-test",
            "rock_agent_config_path": "openclaw_deploy/rock_agent_config.prebuilt.yaml",
            "openclaw_config_path": "openclaw_deploy/openclaw.json",
            "admin_base_url": "http://127.0.0.1:9000",
            "proxy_base_url": "http://127.0.0.1:9005/apis/envs/sandbox/v1",
            "standby_sandboxes": 1,
            "predeploy_stagger_seconds": 0,
        },
        benchmark_name="aime",
        benchmark_config={},
        scorer_name="numeric",
        scorer_config={},
        max_concurrent=1,
        output_dir=str(tmp_path),
    )

    runner = Runner(config)
    runner.setup()
    try:
        summary = runner.run()
    finally:
        runner.teardown()

    assert summary.completed == 1
    assert len(created_sessions) == 2
    assert runner.agent.solve_sandbox_ids == ["sandbox-0"]
    assert all(session.reset_calls == 0 for session in created_sessions)
    assert all(session.close_calls == 1 for session in created_sessions)
    records = runner.result_store.load()
    assert {record["standby_sandboxes"] for record in records} == {1}


def test_runner_skips_unhealthy_fresh_predeployed_standby_on_acquire(monkeypatch, tmp_path):
    class _FakeBenchmark:
        def load_tasks(self, config):
            return [
                BenchmarkTask(task_id="aime_0", problem="Q0", ground_truth="42"),
                BenchmarkTask(task_id="aime_1", problem="Q1", ground_truth="42"),
            ]

    class _FakePredeployedSession:
        def __init__(self, sandbox_id: str):
            self.sandbox_id = sandbox_id
            self.close_calls = 0

        def proxy_v1_base(self) -> str:
            return f"http://proxy.test/apis/envs/sandbox/v1/sandboxes/{self.sandbox_id}/proxy/v1"

        def published_base(self, container_port: int, *, host: str = "127.0.0.1") -> str:
            return f"http://127.0.0.1:1/{self.sandbox_id}"

        def metadata(self) -> dict:
            return {
                "sandbox_id": self.sandbox_id,
                "proxy_base_url": self.proxy_v1_base(),
            }

        def close(self) -> None:
            self.close_calls += 1

    created_sessions: list[_FakePredeployedSession] = []

    class _FakeRockSandboxBackend:
        def setup(self, config) -> None:
            self.config = config

        def create_session(self):
            session = _FakePredeployedSession(f"sandbox-{len(created_sessions)}")
            created_sessions.append(session)
            return session

    class _FakeGatewayRuntimeManager:
        def ensure_ready(self, session):
            return {
                "sandbox_id": session.sandbox_id,
                "gateway_url": f"http://127.0.0.1:1/{session.sandbox_id}/v1/chat/completions",
                "api_base": f"http://127.0.0.1:1/{session.sandbox_id}/v1",
            }

    class _FakeGatewayAgent:
        def __init__(self):
            self.solve_sandbox_ids: list[str | None] = []

        def setup(self, config) -> None:
            self.config = dict(config)

        def solve(self, task, sandbox=None):
            sandbox_id = getattr(sandbox, "sandbox_id", None)
            self.solve_sandbox_ids.append(sandbox_id)
            return AgentResponse(answer="42", sandbox_id=sandbox_id or "", metadata={})

        def teardown(self) -> None:
            pass

    probe_urls: list[str] = []

    def _fake_probe(url, *, timeout, trust_env):
        probe_urls.append(url)
        assert trust_env is False
        if "/sandbox-1/" in url:
            raise httpx.ConnectError("Connection refused")
        return SimpleNamespace(status_code=404)

    monkeypatch.setattr("alphadiana.runner.runner.httpx.get", _fake_probe)
    monkeypatch.setattr("alphadiana.runner.runner.BenchmarkRegistry.get", lambda name: _FakeBenchmark)
    monkeypatch.setattr("alphadiana.runner.runner.AgentRegistry.get", lambda name: _FakeGatewayAgent)
    monkeypatch.setattr("alphadiana.runner.runner.SandboxRegistry.get", lambda name: _FakeRockSandboxBackend)
    monkeypatch.setattr(
        "alphadiana.runner.runner._make_gateway_runtime_manager",
        lambda config: _FakeGatewayRuntimeManager(),
    )

    config = ExperimentConfig(
        run_id="runner-fresh-predeployed-skip-dead-standby",
        agent_name="openclaw",
        agent_version="2026.3.20",
        agent_config={
            "api_key": "sk-test",
            "rock_agent_config_path": "openclaw_deploy/rock_agent_config.prebuilt.yaml",
            "openclaw_config_path": "openclaw_deploy/openclaw.json",
            "admin_base_url": "http://127.0.0.1:9000",
            "proxy_base_url": "http://127.0.0.1:9005/apis/envs/sandbox/v1",
            "standby_sandboxes": 1,
            "predeploy_stagger_seconds": 0,
        },
        benchmark_name="aime",
        benchmark_config={},
        scorer_name="numeric",
        scorer_config={},
        max_concurrent=1,
        output_dir=str(tmp_path),
    )

    runner = Runner(config)
    runner.setup()
    try:
        summary = runner.run()
    finally:
        runner.teardown()

    assert summary.completed == 2
    assert "sandbox-1" not in runner.agent.solve_sandbox_ids
    assert runner.agent.solve_sandbox_ids[0] == "sandbox-0"
    assert runner.agent.solve_sandbox_ids[1] != "sandbox-1"
    assert created_sessions[1].close_calls == 1
    assert any("/sandbox-1/v1/models" in url for url in probe_urls)


def test_runner_quarantines_predeployed_session_after_reset_failure(monkeypatch, tmp_path):
    class _FakeBenchmark:
        def load_tasks(self, config):
            return [
                BenchmarkTask(task_id="aime_0", problem="Q0", ground_truth="42"),
                BenchmarkTask(task_id="aime_1", problem="Q1", ground_truth="42"),
            ]

    class _FakePredeployedSession:
        def __init__(self, sandbox_id: str, *, fail_reset: bool = False):
            self.sandbox_id = sandbox_id
            self.fail_reset = fail_reset
            self.reset_calls = 0
            self.close_calls = 0

        def proxy_v1_base(self) -> str:
            return f"http://proxy.test/apis/envs/sandbox/v1/sandboxes/{self.sandbox_id}/proxy/v1"

        def metadata(self) -> dict:
            return {
                "sandbox_id": self.sandbox_id,
                "proxy_base_url": self.proxy_v1_base(),
            }

        def reset(self) -> None:
            self.reset_calls += 1
            if self.fail_reset:
                raise RuntimeError("reset failed")

        def close(self) -> None:
            self.close_calls += 1

    class _FakeRockSandboxBackend:
        def setup(self, config) -> None:
            self.config = config

        def create_session(self):
            return sessions.pop(0)

    class _FakeGatewayRuntimeManager:
        def ensure_ready(self, session):
            return {
                "sandbox_id": session.sandbox_id,
                "gateway_url": f"{session.proxy_v1_base()}/chat/completions",
                "api_base": session.proxy_v1_base(),
            }

    class _FakeGatewayAgent:
        def __init__(self):
            self.solve_sandbox_ids: list[str | None] = []

        def setup(self, config) -> None:
            self.config = dict(config)

        def solve(self, task, sandbox=None):
            sandbox_id = getattr(sandbox, "sandbox_id", None)
            self.solve_sandbox_ids.append(sandbox_id)
            return AgentResponse(
                answer="42",
                sandbox_id=sandbox_id or "",
                metadata={},
            )

        def teardown(self) -> None:
            pass

    created_sessions = [
        _FakePredeployedSession("sandbox-a", fail_reset=True),
        _FakePredeployedSession("sandbox-b"),
        _FakePredeployedSession("sandbox-c"),
    ]
    sessions = list(created_sessions)

    monkeypatch.setattr(
        "alphadiana.runner.runner.BenchmarkRegistry.get",
        lambda name: _FakeBenchmark,
    )
    monkeypatch.setattr(
        "alphadiana.runner.runner.AgentRegistry.get",
        lambda name: _FakeGatewayAgent,
    )
    monkeypatch.setattr(
        "alphadiana.runner.runner.SandboxRegistry.get",
        lambda name: _FakeRockSandboxBackend,
    )
    monkeypatch.setattr(
        "alphadiana.runner.runner._make_gateway_runtime_manager",
        lambda config: _FakeGatewayRuntimeManager(),
    )

    config = ExperimentConfig(
        run_id="runner-predeployed-reset-quarantine",
        agent_name="openclaw",
        agent_version="2026.3.20",
        agent_config={
            "api_key": "sk-test",
            "rock_agent_config_path": "openclaw_deploy/rock_agent_config.prebuilt.yaml",
            "openclaw_config_path": "openclaw_deploy/openclaw.json",
            "admin_base_url": "http://127.0.0.1:9000",
            "proxy_base_url": "http://127.0.0.1:9005/apis/envs/sandbox/v1",
            "num_sandboxes": 2,
            "reuse_predeployed_sandboxes": True,
        },
        benchmark_name="aime",
        benchmark_config={},
        scorer_name="numeric",
        scorer_config={},
        max_concurrent=1,
        output_dir=str(tmp_path),
    )

    runner = Runner(config)
    runner.setup()
    try:
        summary = runner.run()
    finally:
        runner.teardown()

    assert summary.completed == 2
    assert summary.error_records == 0
    assert runner.agent.solve_sandbox_ids == ["sandbox-a", "sandbox-b"]
    assert [session.reset_calls for session in created_sessions] == [1, 1, 0]
    assert [session.close_calls for session in created_sessions] == [1, 1, 1]
    assert not sessions


def test_predeployed_session_failure_reason_detects_dead_gateway() -> None:
    exc = RuntimeError("OpenClaw response did not contain assistant text")
    exc.retry_responses = [
        {
            "attempt": 1,
            "body": "[Errno 111] Connection refused",
            "error_type": "unknown",
        }
    ]

    assert _predeployed_session_failure_reason(exc) == "gateway_connection_refused"


def test_session_failure_reason_detects_rock_upstream_unreachable() -> None:
    exc = RuntimeError(
        "Failed to execute command: {'status': 'Failed', 'message': 'run in session failed', "
        "'error': 'Service unavailable: Upstream server is not reachable.', 'result': None}"
    )

    assert _predeployed_session_failure_reason(exc) == "sandbox_upstream_unreachable"


def test_sandbox_pool_discards_and_replaces_broken_session() -> None:
    class _FakeSession:
        def __init__(self, sandbox_id: str):
            self.sandbox_id = sandbox_id
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    class _FakeSandbox:
        def __init__(self):
            self.created_sessions: list[_FakeSession] = []

        def create_session(self):
            session = _FakeSession(f"sandbox-{len(self.created_sessions) + 1}")
            self.created_sessions.append(session)
            return session

    sandbox = _FakeSandbox()
    pool = SandboxPool(sandbox, 1)
    original = pool.acquire()

    assert pool.discard_and_replace(original, reason="sandbox_upstream_unreachable") is True
    replacement = pool.acquire(timeout=0.1)

    assert original.close_calls == 1
    assert replacement.sandbox_id == "sandbox-2"
    pool.release(replacement)
    pool.teardown()


def test_runner_quarantines_dead_predeployed_session(monkeypatch, tmp_path):
    class _FakeBenchmark:
        def load_tasks(self, config):
            return [
                BenchmarkTask(task_id="aime_0", problem="Q0", ground_truth="42"),
                BenchmarkTask(task_id="aime_1", problem="Q1", ground_truth="42"),
                BenchmarkTask(task_id="aime_2", problem="Q2", ground_truth="42"),
            ]

    class _FakePredeployedSession:
        def __init__(self, sandbox_id: str):
            self.sandbox_id = sandbox_id
            self.reset_calls = 0
            self.close_calls = 0

        def proxy_v1_base(self) -> str:
            return f"http://proxy.test/apis/envs/sandbox/v1/sandboxes/{self.sandbox_id}/proxy/v1"

        def metadata(self) -> dict:
            return {
                "sandbox_id": self.sandbox_id,
                "proxy_base_url": self.proxy_v1_base(),
            }

        def reset(self) -> None:
            self.reset_calls += 1

        def close(self) -> None:
            self.close_calls += 1

    class _FakeRockSandboxBackend:
        def setup(self, config) -> None:
            self.config = config

        def create_session(self):
            return sessions.pop(0)

    class _FakeGatewayRuntimeManager:
        def ensure_ready(self, session):
            return {
                "sandbox_id": session.sandbox_id,
                "gateway_url": f"{session.proxy_v1_base()}/chat/completions",
                "api_base": session.proxy_v1_base(),
            }

    class _FakeGatewayAgent:
        def __init__(self):
            self.solve_sandbox_ids: list[str | None] = []

        def setup(self, config) -> None:
            self.config = dict(config)

        def solve(self, task, sandbox=None):
            sandbox_id = getattr(sandbox, "sandbox_id", None)
            self.solve_sandbox_ids.append(sandbox_id)
            if task.task_id == "aime_0":
                retry_responses = [
                    {
                        "attempt": 1,
                        "body": "[Errno 111] Connection refused",
                        "error_type": "unknown",
                    }
                ]
                exc = RuntimeError("OpenClaw response did not contain assistant text")
                exc.error_type = "unknown"
                exc.retry_responses = retry_responses
                exc.partial_response = AgentResponse(
                    answer=None,
                    sandbox_id=sandbox_id or "",
                    metadata={"retry_responses": retry_responses},
                )
                raise exc
            return AgentResponse(
                answer="42",
                sandbox_id=sandbox_id or "",
                metadata={},
            )

        def teardown(self) -> None:
            pass

    created_sessions = [
        _FakePredeployedSession("sandbox-a"),
        _FakePredeployedSession("sandbox-b"),
        _FakePredeployedSession("sandbox-c"),
    ]
    sessions = list(created_sessions)

    monkeypatch.setattr(
        "alphadiana.runner.runner.BenchmarkRegistry.get",
        lambda name: _FakeBenchmark,
    )
    monkeypatch.setattr(
        "alphadiana.runner.runner.AgentRegistry.get",
        lambda name: _FakeGatewayAgent,
    )
    monkeypatch.setattr(
        "alphadiana.runner.runner.SandboxRegistry.get",
        lambda name: _FakeRockSandboxBackend,
    )
    monkeypatch.setattr(
        "alphadiana.runner.runner._make_gateway_runtime_manager",
        lambda config: _FakeGatewayRuntimeManager(),
    )

    config = ExperimentConfig(
        run_id="runner-predeployed-quarantine",
        agent_name="openclaw",
        agent_version="2026.3.20",
        agent_config={
            "api_key": "sk-test",
            "rock_agent_config_path": "openclaw_deploy/rock_agent_config.prebuilt.yaml",
            "openclaw_config_path": "openclaw_deploy/openclaw.json",
            "admin_base_url": "http://127.0.0.1:9000",
            "proxy_base_url": "http://127.0.0.1:9005/apis/envs/sandbox/v1",
            "num_sandboxes": 2,
            "reuse_predeployed_sandboxes": True,
        },
        benchmark_name="aime",
        benchmark_config={},
        scorer_name="numeric",
        scorer_config={},
        max_concurrent=1,
        task_retries=0,
        output_dir=str(tmp_path),
    )

    runner = Runner(config)
    runner.setup()
    try:
        summary = runner.run()
    finally:
        runner.teardown()

    assert summary.completed == 2
    assert summary.error_records == 1
    assert runner.agent.solve_sandbox_ids == ["sandbox-a", "sandbox-b", "sandbox-c"]
    assert [session.reset_calls for session in created_sessions] == [0, 1, 1]
    assert [session.close_calls for session in created_sessions] == [1, 1, 1]

    task0 = json.loads((tmp_path / "runner-predeployed-quarantine" / "tasks" / "aime_0.json").read_text())
    assert task0[0]["metadata"]["predeployed_session_quarantined"] is True
    assert task0[0]["metadata"]["predeployed_session_quarantine_reason"] == "gateway_connection_refused"


def test_runner_replaces_pooled_session_and_retries_after_rock_upstream_failure(
    monkeypatch,
    tmp_path,
):
    class _FakeBenchmark:
        def load_tasks(self, config):
            return [BenchmarkTask(task_id="aime_0", problem="Q0", ground_truth="42")]

    class _FakeSession:
        def __init__(self, sandbox_id: str):
            self.sandbox_id = sandbox_id
            self.reset_calls = 0
            self.close_calls = 0

        def metadata(self) -> dict:
            return {
                "sandbox_id": self.sandbox_id,
                "command_history": [
                    {"command": "OPENAI_API_KEY=sk-live ZEROCLAW_API_KEY=sk-zero zeroclaw"}
                ],
            }

        def reset(self) -> None:
            self.reset_calls += 1

        def close(self) -> None:
            self.close_calls += 1

    class _FakeSandbox:
        def __init__(self):
            self.created_sessions: list[_FakeSession] = []

        def setup(self, config) -> None:
            self.config = dict(config)

        def create_session(self):
            session = _FakeSession(f"sandbox-{len(self.created_sessions) + 1}")
            self.created_sessions.append(session)
            return session

        def teardown(self) -> None:
            pass

    class _FakeAgent:
        def setup(self, config) -> None:
            self.config = dict(config)
            self.solve_sandbox_ids: list[str | None] = []

        def solve(self, task, sandbox=None):
            sandbox_id = getattr(sandbox, "sandbox_id", None)
            self.solve_sandbox_ids.append(sandbox_id)
            if len(self.solve_sandbox_ids) == 1:
                raise RuntimeError(
                    "Failed to execute command: {'status': 'Failed', "
                    "'message': 'run in session failed', "
                    "'error': 'Service unavailable: Upstream server is not reachable.', "
                    "'result': None}"
                )
            return AgentResponse(answer="42", sandbox_id=sandbox_id or "", metadata={})

        def teardown(self) -> None:
            pass

    monkeypatch.setattr(
        "alphadiana.runner.runner.BenchmarkRegistry.get",
        lambda name: _FakeBenchmark,
    )
    monkeypatch.setattr(
        "alphadiana.runner.runner.AgentRegistry.get",
        lambda name: _FakeAgent,
    )
    monkeypatch.setattr(
        "alphadiana.runner.runner.SandboxRegistry.get",
        lambda name: _FakeSandbox,
    )

    config = ExperimentConfig(
        run_id="runner-replace-pooled-session",
        agent_name="zeroclaw",
        agent_version="0.6.9",
        agent_config={},
        benchmark_name="aime",
        benchmark_config={},
        sandbox_name="fake_sandbox",
        sandbox_config={},
        scorer_name="numeric",
        scorer_config={},
        max_concurrent=2,
        task_retries=1,
        output_dir=str(tmp_path),
    )

    runner = Runner(config)
    runner.setup()
    try:
        summary = runner.run()
    finally:
        runner.teardown()

    assert summary.completed == 1
    assert summary.error_records == 0
    assert runner.agent.solve_sandbox_ids == ["sandbox-1", "sandbox-2"]
    assert len(runner.sandbox.created_sessions) == 3
    assert runner.sandbox.created_sessions[0].close_calls == 1

    jsonl_records = [
        json.loads(line)
        for line in (tmp_path / "runner-replace-pooled-session.jsonl").read_text().splitlines()
    ]
    assert [record["score_status"] for record in jsonl_records] == ["runtime_error", "valid_scored"]
    error_record = jsonl_records[0]
    assert error_record["metadata"]["pooled_session_replaced"] is True
    assert (
        error_record["metadata"]["pooled_session_replacement_reason"]
        == "sandbox_upstream_unreachable"
    )
    history = error_record["sandbox_metadata"]["command_history"][0]["command"]
    assert "sk-live" not in history
    assert "sk-zero" not in history
    assert "OPENAI_API_KEY=<redacted>" in history
    assert "ZEROCLAW_API_KEY=<redacted>" in history


def test_runner_passes_kata_runtime_flag_to_auto_created_rock_sandbox(monkeypatch, tmp_path):
    class _FakeBenchmark:
        def load_tasks(self, config):
            return [BenchmarkTask(task_id="aime_0", problem="Q0", ground_truth="42")]

    class _FakeSession:
        def __init__(self):
            self.sandbox_id = "sandbox-kata"

        def metadata(self) -> dict:
            return {"sandbox_id": self.sandbox_id}

        def reset(self) -> None:
            pass

        def close(self) -> None:
            pass

    class _FakeRockSandboxBackend:
        seen_configs: list[dict] = []

        def setup(self, config) -> None:
            self.__class__.seen_configs.append(dict(config))

        def create_session(self):
            return _FakeSession()

        def teardown(self) -> None:
            pass

    class _FakeGatewayAgent:
        def setup(self, config) -> None:
            self.config = dict(config)

        def solve(self, task, sandbox=None):
            return AgentResponse(answer="42", metadata={})

        def teardown(self) -> None:
            pass

    monkeypatch.setattr(
        "alphadiana.runner.runner.BenchmarkRegistry.get",
        lambda name: _FakeBenchmark,
    )
    monkeypatch.setattr(
        "alphadiana.runner.runner.AgentRegistry.get",
        lambda name: _FakeGatewayAgent,
    )
    monkeypatch.setattr(
        "alphadiana.runner.runner.SandboxRegistry.get",
        lambda name: _FakeRockSandboxBackend,
    )

    config = ExperimentConfig(
        run_id="runner-kata-rock",
        agent_name="zeroclaw",
        agent_version="0.6.9",
        agent_config={
            "model": "demo-model",
            "api_base": "https://example.test/v1",
            "api_key": "sk-test",
            "rock_image": "zeroclaw-reasoning:0.6.9",
            "admin_base_url": "http://127.0.0.1:9000",
            "proxy_base_url": "http://127.0.0.1:9005/apis/envs/sandbox/v1",
            "rock_use_kata_runtime": True,
        },
        benchmark_name="aime",
        benchmark_config={},
        scorer_name="numeric",
        scorer_config={},
        max_concurrent=1,
        output_dir=str(tmp_path),
    )

    runner = Runner(config)
    runner.setup()
    try:
        summary = runner.run()
    finally:
        runner.teardown()

    assert summary.completed == 1
    assert _FakeRockSandboxBackend.seen_configs
    assert _FakeRockSandboxBackend.seen_configs[0]["use_kata_runtime"] is True


@pytest.mark.parametrize(
    ("agent_name", "expected_create_calls", "expected_sandbox_ids"),
    [
        ("openclaw", 2, ["sandbox-1", "sandbox-2"]),
        ("zeroclaw", 1, ["sandbox-1", "sandbox-1"]),
    ],
)
def test_runner_sequential_session_reuse_depends_on_agent_name(
    monkeypatch,
    tmp_path,
    agent_name,
    expected_create_calls,
    expected_sandbox_ids,
):
    class _FakeBenchmark:
        def load_tasks(self, config):
            return [
                BenchmarkTask(task_id="task-0", problem="Q0", ground_truth="42"),
                BenchmarkTask(task_id="task-1", problem="Q1", ground_truth="42"),
            ]

    class _FakeSession:
        def __init__(self, sandbox_id: str):
            self.sandbox_id = sandbox_id
            self.reset_calls = 0
            self.close_calls = 0

        def metadata(self) -> dict:
            return {"sandbox_id": self.sandbox_id}

        def reset(self) -> None:
            self.reset_calls += 1

        def close(self) -> None:
            self.close_calls += 1

    class _FakeSandbox:
        def __init__(self):
            self.created_sessions: list[_FakeSession] = []

        def setup(self, config) -> None:
            self.config = dict(config)

        def supports_shared_session(self) -> bool:
            return True

        def create_session(self):
            session = _FakeSession(f"sandbox-{len(self.created_sessions) + 1}")
            self.created_sessions.append(session)
            return session

        def teardown(self) -> None:
            pass

    class _FakeAgent:
        def setup(self, config) -> None:
            self.config = dict(config)
            self.solve_sandbox_ids: list[str | None] = []

        def solve(self, task, sandbox=None):
            sandbox_id = getattr(sandbox, "sandbox_id", None)
            self.solve_sandbox_ids.append(sandbox_id)
            return AgentResponse(
                answer="42",
                sandbox_id=sandbox_id or "",
                metadata={},
            )

        def teardown(self) -> None:
            pass

    monkeypatch.setattr(
        "alphadiana.runner.runner.BenchmarkRegistry.get",
        lambda name: _FakeBenchmark,
    )
    monkeypatch.setattr(
        "alphadiana.runner.runner.AgentRegistry.get",
        lambda name: _FakeAgent,
    )
    monkeypatch.setattr(
        "alphadiana.runner.runner.SandboxRegistry.get",
        lambda name: _FakeSandbox,
    )

    config = ExperimentConfig(
        run_id=f"runner-shared-session-{agent_name}",
        agent_name=agent_name,
        agent_version="test",
        agent_config={},
        benchmark_name="aime",
        benchmark_config={},
        sandbox_name="fake_sandbox",
        sandbox_config={},
        scorer_name="numeric",
        scorer_config={},
        max_concurrent=1,
        output_dir=str(tmp_path),
    )

    runner = Runner(config)
    runner.setup()
    try:
        summary = runner.run()
    finally:
        runner.teardown()

    assert summary.completed == 2
    assert runner.agent.solve_sandbox_ids == expected_sandbox_ids
    assert len(runner.sandbox.created_sessions) == expected_create_calls

    if agent_name == "openclaw":
        assert [session.reset_calls for session in runner.sandbox.created_sessions] == [0, 0]
        assert [session.close_calls for session in runner.sandbox.created_sessions] == [1, 1]
    else:
        assert [session.reset_calls for session in runner.sandbox.created_sessions] == [2]
        assert [session.close_calls for session in runner.sandbox.created_sessions] == [1]


@pytest.mark.parametrize(
    ("response_kwargs", "expected_reason", "expected_error_type"),
    [
        (
            {"metadata": {"received_done": False}, "raw_output": "$$\\boxed{42}$$"},
            "stream_incomplete",
            "incomplete_stream",
        ),
        (
            {
                "metadata": {"received_done": True},
                "trajectory": [
                    {"role": "user", "content": "Read HEARTBEAT.md if it exists."},
                    {"role": "assistant", "content": "HEARTBEAT_OK"},
                ],
            },
            "heartbeat_trace",
            "openclaw_heartbeat_taint",
        ),
        (
            {"metadata": {"received_done": True, "session_tainted": True}},
            "session_tainted",
            "openclaw_session_tainted",
        ),
    ],
)
def test_runner_rejects_tainted_openclaw_responses_before_scoring(
    monkeypatch,
    tmp_path,
    response_kwargs,
    expected_reason,
    expected_error_type,
):
    class _FakeBenchmark:
        def load_tasks(self, config):
            return [BenchmarkTask(task_id="task-0", problem="Q0", ground_truth="42")]

    class _FakeSession:
        sandbox_id = "sandbox-1"

        def metadata(self) -> dict:
            return {"sandbox_id": self.sandbox_id}

        def close(self) -> None:
            pass

    class _FakeSandbox:
        def setup(self, config) -> None:
            self.config = dict(config)

        def create_session(self):
            return _FakeSession()

        def teardown(self) -> None:
            pass

    class _FakeAgent:
        def setup(self, config) -> None:
            self.config = dict(config)

        def solve(self, task, sandbox=None):
            return AgentResponse(answer="42", sandbox_id="sandbox-1", **response_kwargs)

        def teardown(self) -> None:
            pass

    monkeypatch.setattr("alphadiana.runner.runner.BenchmarkRegistry.get", lambda name: _FakeBenchmark)
    monkeypatch.setattr("alphadiana.runner.runner.AgentRegistry.get", lambda name: _FakeAgent)
    monkeypatch.setattr("alphadiana.runner.runner.SandboxRegistry.get", lambda name: _FakeSandbox)

    config = ExperimentConfig(
        run_id=f"runner-openclaw-taint-{expected_reason}",
        agent_name="openclaw",
        agent_version="test",
        agent_config={},
        benchmark_name="aime",
        benchmark_config={},
        sandbox_name="fake_sandbox",
        sandbox_config={},
        scorer_name="numeric",
        scorer_config={},
        max_concurrent=1,
        output_dir=str(tmp_path),
    )

    runner = Runner(config)
    runner.setup()
    try:
        summary = runner.run()
    finally:
        runner.teardown()

    assert summary.completed == 0
    assert summary.error_records == 1
    records = runner.result_store.load()
    assert len(records) == 1
    record = records[0]
    assert record["score_status"] == "runtime_error"
    assert record["correct"] is None
    assert record["predicted"] == "42"
    assert record["error"]["error_type"] == expected_error_type
    assert record["metadata"]["openclaw_integrity_guard"] is True
    assert record["metadata"]["openclaw_integrity_guard_reason"] == expected_reason


def test_runner_allows_openclaw_timeout_scored_zero_response():
    config = ExperimentConfig(
        run_id="runner-openclaw-timeout-zero",
        agent_name="openclaw",
        agent_version="test",
        agent_config={},
        benchmark_name="aime",
        benchmark_config={},
        sandbox_name="none",
        sandbox_config={},
        scorer_name="numeric",
        scorer_config={},
    )
    response = AgentResponse(
        answer=None,
        finish_reason="timeout",
        metadata={
            "received_done": False,
            "failure_reason": "timeout",
            "openclaw_timeout_scored_zero": True,
        },
    )

    assert _openclaw_integrity_guard_reason(config, response) == ("", "")


def test_runner_still_rejects_heartbeat_tainted_openclaw_timeout_zero():
    config = ExperimentConfig(
        run_id="runner-openclaw-timeout-zero-heartbeat",
        agent_name="openclaw",
        agent_version="test",
        agent_config={},
        benchmark_name="aime",
        benchmark_config={},
        sandbox_name="none",
        sandbox_config={},
        scorer_name="numeric",
        scorer_config={},
    )
    response = AgentResponse(
        answer=None,
        finish_reason="timeout",
        raw_output="HEARTBEAT_OK",
        metadata={
            "received_done": False,
            "failure_reason": "timeout",
            "openclaw_timeout_scored_zero": True,
        },
    )

    assert _openclaw_integrity_guard_reason(config, response) == (
        "heartbeat_trace",
        "openclaw_heartbeat_taint",
    )


def test_runner_checkpoint_ignores_completed_records_from_other_scorer(monkeypatch, tmp_path):
    class _FakeBenchmark:
        def load_tasks(self, config):
            return [
                BenchmarkTask(task_id="t1", problem="Q1", ground_truth="42"),
                BenchmarkTask(task_id="t2", problem="Q2", ground_truth="42"),
            ]

    class _FakeAgent:
        def setup(self, config) -> None:
            self.config = dict(config)
            self.solved_task_ids: list[str] = []

        def solve(self, task, sandbox=None):
            self.solved_task_ids.append(task.task_id)
            return AgentResponse(answer="42", metadata={})

        def teardown(self) -> None:
            pass

    monkeypatch.setattr(
        "alphadiana.runner.runner.BenchmarkRegistry.get",
        lambda name: _FakeBenchmark,
    )
    monkeypatch.setattr(
        "alphadiana.runner.runner.AgentRegistry.get",
        lambda name: _FakeAgent,
    )

    config = ExperimentConfig(
        run_id="runner-scorer-mismatch",
        agent_name="direct_llm",
        agent_version="1.0",
        agent_config={
            "model": "demo-model",
            "api_base": "https://example.test/v1",
            "api_key": "sk-test",
        },
        benchmark_name="aime",
        benchmark_config={},
        scorer_name="numeric",
        scorer_config={},
        output_dir=str(tmp_path),
    )

    jsonl_path = tmp_path / "runner-scorer-mismatch.jsonl"
    jsonl_path.write_text(
        json.dumps({
            "task_id": "t1",
            "sample_index": 0,
            "scorer_name": "math_verify",
            "score": 1.0,
            "correct": True,
        }) + "\n",
        encoding="utf-8",
    )

    runner = Runner(config)
    runner.setup()
    try:
        summary = runner.run()
    finally:
        runner.teardown()

    assert summary.completed == 2
    assert runner.agent.solved_task_ids == ["t1", "t2"]


def test_strict_isolation_rejects_shared_gateway_fallback(monkeypatch, tmp_path):
    class _FakeBenchmark:
        def load_tasks(self, config):
            return [BenchmarkTask(task_id="t1", problem="Q1", ground_truth="42")]

    class _FakeAgent:
        def setup(self, config) -> None:
            self.config = dict(config)

        def solve(self, task, sandbox=None):
            return AgentResponse(answer="42", metadata={})

        def teardown(self) -> None:
            pass

    class _BrokenRockSandbox:
        def setup(self, config) -> None:
            raise RuntimeError("rock bootstrap failed")

    monkeypatch.setattr("alphadiana.runner.runner.BenchmarkRegistry.get", lambda name: _FakeBenchmark)
    monkeypatch.setattr("alphadiana.runner.runner.AgentRegistry.get", lambda name: _FakeAgent)
    monkeypatch.setattr("alphadiana.runner.runner.SandboxRegistry.get", lambda name: _BrokenRockSandbox)

    config = ExperimentConfig(
        run_id="runner-strict-shared-gateway",
        agent_name="zeroclaw",
        agent_version="0.6.9",
        agent_config={
            "model": "demo-model",
            "api_base": "https://example.test/v1",
            "api_key": "sk-test",
            "rock_image": "zeroclaw-reasoning:0.6.9",
        },
        benchmark_name="aime",
        benchmark_config={},
        scorer_name="numeric",
        strict_isolation=True,
        output_dir=str(tmp_path),
    )

    runner = Runner(config)
    runner.setup()
    try:
        with pytest.raises(RuntimeError, match="strict_isolation=true: failed to auto-create ROCK sandbox"):
            runner.run()
    finally:
        runner.teardown()


def test_strict_isolation_rejects_partial_predeploy(monkeypatch, tmp_path):
    class _FakeBenchmark:
        def load_tasks(self, config):
            return [BenchmarkTask(task_id="t1", problem="Q1", ground_truth="42")]

    class _FakeSession:
        def __init__(self, sandbox_id: str):
            self.sandbox_id = sandbox_id

        def metadata(self) -> dict:
            return {"sandbox_id": self.sandbox_id, "proxy_base_url": "http://proxy.test"}

        def close(self) -> None:
            pass

    class _FakeRockSandboxBackend:
        create_calls = 0

        def setup(self, config) -> None:
            self.config = dict(config)

        def create_session(self):
            self.__class__.create_calls += 1
            return _FakeSession(f"sandbox-{self.__class__.create_calls}")

    class _FakeRuntimeManager:
        ensure_calls = 0

        def ensure_ready(self, session):
            self.__class__.ensure_calls += 1
            if self.__class__.ensure_calls == 1:
                return {"api_base": "http://proxy.test/v1"}
            raise RuntimeError("second gateway failed")

    class _FakeAgent:
        def setup(self, config) -> None:
            self.config = dict(config)

        def solve(self, task, sandbox=None):
            return AgentResponse(answer="42", metadata={})

        def teardown(self) -> None:
            pass

    monkeypatch.setattr("alphadiana.runner.runner.BenchmarkRegistry.get", lambda name: _FakeBenchmark)
    monkeypatch.setattr("alphadiana.runner.runner.AgentRegistry.get", lambda name: _FakeAgent)
    monkeypatch.setattr("alphadiana.runner.runner.SandboxRegistry.get", lambda name: _FakeRockSandboxBackend)
    monkeypatch.setattr(
        "alphadiana.runner.runner._make_gateway_runtime_manager",
        lambda config: _FakeRuntimeManager(),
    )

    config = ExperimentConfig(
        run_id="runner-strict-predeploy",
        agent_name="openclaw",
        agent_version="2026.3.20",
        agent_config={
            "api_key": "sk-test",
            "rock_image": "alphadiana/openclaw:latest",
            "rock_agent_config_path": "openclaw_deploy/rock_agent_config.prebuilt.yaml",
            "openclaw_config_path": "openclaw_deploy/openclaw.json",
            "num_sandboxes": 2,
        },
        benchmark_name="aime",
        benchmark_config={},
        scorer_name="numeric",
        strict_isolation=True,
        max_concurrent=2,
        output_dir=str(tmp_path),
    )

    runner = Runner(config)
    runner.setup()
    try:
        with pytest.raises(RuntimeError, match="strict_isolation=true: failed to predeploy"):
            runner.run()
    finally:
        runner.teardown()


def test_non_strict_keeps_old_fallback_behavior(monkeypatch, tmp_path):
    class _FakeBenchmark:
        def load_tasks(self, config):
            return [BenchmarkTask(task_id="t1", problem="Q1", ground_truth="42")]

    class _FakeAgent:
        def setup(self, config) -> None:
            self.config = dict(config)

        def solve(self, task, sandbox=None):
            return AgentResponse(answer="42", metadata={})

        def teardown(self) -> None:
            pass

    class _BrokenRockSandbox:
        def setup(self, config) -> None:
            raise RuntimeError("rock bootstrap failed")

    monkeypatch.setattr("alphadiana.runner.runner.BenchmarkRegistry.get", lambda name: _FakeBenchmark)
    monkeypatch.setattr("alphadiana.runner.runner.AgentRegistry.get", lambda name: _FakeAgent)
    monkeypatch.setattr("alphadiana.runner.runner.SandboxRegistry.get", lambda name: _BrokenRockSandbox)

    config = ExperimentConfig(
        run_id="runner-nonstrict-shared-gateway",
        agent_name="openclaw",
        agent_version="2026.3.20",
        agent_config={
            "api_key": "sk-test",
            "rock_image": "alphadiana/openclaw:latest",
            "rock_agent_config_path": "openclaw_deploy/rock_agent_config.prebuilt.yaml",
            "openclaw_config_path": "openclaw_deploy/openclaw.json",
        },
        benchmark_name="aime",
        benchmark_config={},
        scorer_name="numeric",
        strict_isolation=False,
        output_dir=str(tmp_path),
    )

    runner = Runner(config)
    runner.setup()
    try:
        summary = runner.run()
    finally:
        runner.teardown()

    assert summary.completed == 1


def test_zeroclaw_non_strict_rejects_host_fallback_when_auto_sandbox_fails(monkeypatch, tmp_path):
    class _FakeBenchmark:
        def load_tasks(self, config):
            return [BenchmarkTask(task_id="t1", problem="Q1", ground_truth="42")]

    class _FakeAgent:
        def setup(self, config) -> None:
            self.config = dict(config)

        def solve(self, task, sandbox=None):
            return AgentResponse(answer="42", metadata={})

        def teardown(self) -> None:
            pass

    class _BrokenRockSandbox:
        def setup(self, config) -> None:
            raise RuntimeError("rock bootstrap failed")

    monkeypatch.setattr("alphadiana.runner.runner.BenchmarkRegistry.get", lambda name: _FakeBenchmark)
    monkeypatch.setattr("alphadiana.runner.runner.AgentRegistry.get", lambda name: _FakeAgent)
    monkeypatch.setattr("alphadiana.runner.runner.SandboxRegistry.get", lambda name: _BrokenRockSandbox)

    config = ExperimentConfig(
        run_id="runner-zeroclaw-no-host-fallback",
        agent_name="zeroclaw",
        agent_version="0.6.9",
        agent_config={
            "model": "demo-model",
            "api_base": "https://example.test/v1",
            "api_key": "sk-test",
            "rock_image": "zeroclaw-reasoning:0.6.9",
        },
        benchmark_name="aime",
        benchmark_config={},
        scorer_name="numeric",
        strict_isolation=False,
        output_dir=str(tmp_path),
    )

    runner = Runner(config)
    runner.setup()
    try:
        with pytest.raises(RuntimeError, match="ZeroClaw requires a ROCK sandbox"):
            runner.run()
    finally:
        runner.teardown()
