"""Independent custom components exercise the existing Runner lifecycle."""

import pytest

import alphadiana as ad
from alphadiana.benchmarks.base import Benchmark, BenchmarkTask
from alphadiana.benchmarks.registry import BenchmarkRegistry
from alphadiana.engine.sandbox.local import LocalSandbox, LocalSession
from alphadiana.engine.sandbox.registry import SandboxRegistry
from alphadiana.harness.base import Agent, AgentResponse
from alphadiana.harness.registry import AgentRegistry
from alphadiana.scorer.base import Scorer, ScoreResult
from alphadiana.scorer.registry import ScorerRegistry


@pytest.mark.parametrize("fail", [False, True])
@pytest.mark.parametrize("shared", [False, True])
def test_custom_environment_lifecycle(tmp_path, monkeypatch, fail, shared):
    opened = []
    closed = []
    teardown = []

    class Data(Benchmark):
        def load_tasks(self, config):
            assert config["custom_option"] == "kept"
            return [
                BenchmarkTask(str(i), "Read the supplied file", "7") for i in range(3)
            ]

    class Session(LocalSession):
        def close(self):
            closed.append(self.session_id)
            super().close()

    class Environment(LocalSandbox):
        def supports_shared_session(self):
            return shared

        def create_session(self):
            session = Session()
            session.upload("input.txt", b"7")
            opened.append(session.session_id)
            return session

        def teardown(self):
            teardown.append("environment")

    class Solver(Agent):
        def setup(self, config):
            assert config["custom_option"] == "kept"

        def solve(self, task, sandbox=None):
            assert sandbox is not None
            if fail:
                raise RuntimeError("audit-owned failure")
            return AgentResponse(sandbox.download("input.txt").decode())

        def teardown(self):
            teardown.append("agent")

    class Metric(Scorer):
        name = "audit_metric"

        def score(self, task, response):
            return ScoreResult(True, 1, "7", response.answer)

    for registry, name, implementation in [
        (BenchmarkRegistry, "audit_data", Data),
        (AgentRegistry, "audit_solver", Solver),
        (SandboxRegistry, "audit_environment", Environment),
        (ScorerRegistry, "audit_metric", Metric),
    ]:
        monkeypatch.setitem(registry._registry, name, implementation)
    runner = ad.runner(
        env=ad.environment("audit_environment"),
        output_dir=str(tmp_path),
        run_id="audit",
        strict_isolation=True,
    )
    kwargs = {
        "agent": ad.agent("audit_solver", version="custom-1", custom_option="kept"),
        "data": ad.dataset("audit_data", custom_option="kept"),
        "scorer": ad.scorer.create("audit_metric"),
    }
    if fail:
        with pytest.raises(RuntimeError, match="no valid scored"):
            runner.compute(**kwargs)
    else:
        result = runner.compute(**kwargs)
        assert len(result.records()) == 3
    assert len(opened) == (1 if shared else 3) and len(set(opened)) == len(opened)
    assert sorted(closed) == sorted(opened)
    assert set(teardown) == {"agent", "environment"}
