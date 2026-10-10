"""Run-level isolation, failure ordering, and cleanup across OpenClaw tasks."""

import time
from types import SimpleNamespace

import pytest

from alphadiana.analysis.io.result_store import ResultStore
from alphadiana.analysis.run_summary import ReportGenerator
from alphadiana.benchmarks.base import BenchmarkTask
from alphadiana.engine.config.experiment_config import ExperimentConfig
from alphadiana.engine.runner import Runner
from alphadiana.harness.base import AgentResponse
from alphadiana.registry import SandboxRegistry
from alphadiana.scorer.numeric import NumericScorer


def configure_run(
    tmp_path, monkeypatch, *, reuse=False, tainted=False, startup_failure=False
):
    from alphadiana.harness.openclaw import gateway_pool as owner

    monkeypatch.setattr(
        owner, "_OPENCLAW_PROFILE_CACHE_PATH", tmp_path / "profile.json"
    )
    monkeypatch.setattr(
        owner,
        "time",
        SimpleNamespace(
            time=time.time,
            monotonic=time.monotonic,
            strftime=time.strftime,
            gmtime=time.gmtime,
            sleep=lambda _: None,
        ),
    )
    sessions, solved = [], []

    class Session:
        def __init__(self):
            self.sandbox_id = f"session-{len(sessions)}"
            self.closed = self.resets = 0

        def metadata(self):
            return {"sandbox_id": self.sandbox_id, "memory": "2g", "cpus": 0.5}

        def close(self):
            self.closed += 1

        def reset(self):
            self.resets += 1

    class Rock:
        def setup(self, config):
            pass

        def create_session(self):
            session = Session()
            sessions.append(session)
            return session

    class Runtime:
        def __init__(self, config):
            pass

        def ensure_ready(self, session):
            if startup_failure and len(sessions) > 1:
                raise RuntimeError("captured startup failure")
            return {"api_base": f"http://fixture/{session.sandbox_id}"}

    class Agent:
        def setup(self, config):
            self._gateway_pool = list(config.get("gateway_pool", []))

        def solve(self, task, sandbox=None):
            assert sandbox.closed == 0
            solved.append(sandbox.sandbox_id)
            return AgentResponse(
                answer="1",
                raw_output="HEARTBEAT_OK" if tainted and task.task_id == "t0" else "1",
                sandbox_id=sandbox.sandbox_id,
                trajectory=[{"role": "assistant", "content": "1"}],
            )

    import alphadiana.harness.openclaw.runtime as runtime

    monkeypatch.setattr(runtime, "OpenClawRuntimeManager", Runtime)
    monkeypatch.setattr(SandboxRegistry, "get", classmethod(lambda cls, name: Rock))
    config = ExperimentConfig(
        agent_name="openclaw",
        agent_version="fixture",
        benchmark_name="fixture",
        scorer_name="numeric",
        run_id="pool-fixture",
        output_dir=str(tmp_path),
        max_concurrent=1,
        task_retries=0,
        strict_isolation=startup_failure,
        agent_config={
            "rock_agent_config_path": "fixture",
            "openclaw_config_path": "fixture",
            "num_sandboxes": 2 if reuse or startup_failure else 1,
            "reuse_predeployed_sandboxes": reuse,
            "predeploy_stagger_seconds": 0,
            "predeployed_lease_probe": False,
        },
    )
    runner = Runner(config)
    runner.benchmark = SimpleNamespace(
        load_tasks=lambda _: [BenchmarkTask(f"t{i}", "Return 1", "1") for i in range(3)]
    )
    runner.agent = Agent()
    runner.scorer = NumericScorer()
    runner.scorer.setup({})
    runner.result_store = ResultStore(str(tmp_path), config.run_id)
    runner.report_generator = ReportGenerator()
    return runner, sessions, solved


@pytest.mark.parametrize("reuse", [False, True])
def test_task_isolation_reuse_and_final_cleanup(tmp_path, monkeypatch, reuse):
    runner, sessions, solved = configure_run(tmp_path, monkeypatch, reuse=reuse)
    runner.run()
    assert len(solved) == 3
    if reuse:
        assert len(set(solved)) == 2
        assert sum(s.resets for s in sessions) == 3
    else:
        assert len(set(solved)) == 3
        assert not any(s.resets for s in sessions)
    assert all(s.closed == 1 for s in sessions)
    records = runner.result_store.load()
    assert len(records) == 3 and all(
        r["correct"] and r["score_status"] == "valid_scored" for r in records
    )


def test_tainted_response_is_saved_as_failure_before_scoring(tmp_path, monkeypatch):
    runner, sessions, solved = configure_run(tmp_path, monkeypatch, tainted=True)
    runner.run()
    records = {r["task_id"]: r for r in runner.result_store.load()}
    assert records["t0"]["score_status"] != "valid_scored"
    assert (
        records["t0"]["metadata"]["openclaw_integrity_guard_reason"]
        == "heartbeat_trace"
    )
    assert records["t1"]["correct"] and records["t2"]["correct"]
    assert len(set(solved)) == 3
    assert all(s.closed == 1 for s in sessions)


def test_strict_partial_startup_closes_every_created_session(tmp_path, monkeypatch):
    runner, sessions, solved = configure_run(
        tmp_path, monkeypatch, startup_failure=True
    )
    with pytest.raises(RuntimeError, match="strict_isolation=true"):
        runner.run()
    assert not solved
    assert len(sessions) == 4 and all(s.closed == 1 for s in sessions)
