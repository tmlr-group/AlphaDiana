"""Tests for TaskDispatcher and Runner."""

from types import SimpleNamespace

import pytest

from alphadiana.agent.base import AgentResponse
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.config.experiment_config import ExperimentConfig
from alphadiana.runner.runner import Runner
from alphadiana.runner.task_dispatcher import TaskDispatcher


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
        agent_name="zeroclaw",
        agent_version="0.6.9",
        agent_config={
            "model": "demo-model",
            "api_base": "https://example.test/v1",
            "api_key": "sk-test",
            "rock_image": "zeroclaw-reasoning:0.6.9",
            "admin_base_url": "http://127.0.0.1:9000",
            "proxy_base_url": "http://127.0.0.1:9005/apis/envs/sandbox/v1",
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
