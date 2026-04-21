from __future__ import annotations

import io
import json
import logging
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from alphadiana.agent.base import AgentResponse
from alphadiana.agent.openclaw import OpenClawAgent
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.dashboard.backend.data_loader import DataLoader, _load_jsonl
from alphadiana.dashboard.backend.job_manager import JobManager
from alphadiana.dashboard.backend.models import CreateJobRequest, JobStatus
from alphadiana.results.result_store import ResultStore
from alphadiana.sandbox.pool import SandboxPool


def _job_request(**overrides) -> CreateJobRequest:
    data = {
        "agent_name": "openclaw",
        "agent_version": "test",
        "agent_config": {"model": "demo-model", "temperature": 0.1},
        "benchmark_name": "aime",
        "benchmark_config": {"split": "train"},
        "scorer_name": "exact_match",
        "scorer_config": {},
        "sandbox_name": None,
        "sandbox_config": {},
        "max_concurrent": 1,
        "redo_all": False,
        "num_samples": 1,
        "metadata": {},
    }
    data.update(overrides)
    return CreateJobRequest(**data)


def _install_runner_stubs(monkeypatch, summary=None):
    import alphadiana.config.experiment_config as experiment_config_module
    import alphadiana.runner.runner as runner_module

    summary = summary or SimpleNamespace(completed=0, total_tasks=0, failed=0, accuracy=0.0)

    class FakeExperimentConfig:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class FakeRunner:
        def __init__(self, config, cancel_event=None):
            self.config = config
            self.cancel_event = cancel_event

        def setup(self):
            return None

        def run(self):
            return summary

        def teardown(self):
            return None

    monkeypatch.setattr(experiment_config_module, "ExperimentConfig", FakeExperimentConfig)
    monkeypatch.setattr(runner_module, "Runner", FakeRunner)


def test_save_per_task_json_concurrent(tmp_path: Path):
    store = ResultStore(output_dir=str(tmp_path), run_id="r1")
    errors: list[Exception] = []

    def worker(i: int) -> None:
        try:
            store._save_per_task_json(
                task_id="t1",
                record={"task_id": "t1", "sample_index": i, "result": f"x{i}"},
            )
        except Exception as exc:  # pragma: no cover - diagnostic path
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    task_path = tmp_path / "r1" / "tasks" / "t1.json"
    data = json.loads(task_path.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert sorted(record["sample_index"] for record in data) == list(range(10))


def test_get_task_accepts_list_format(tmp_path: Path):
    run_dir = tmp_path / "runs"
    task_dir = run_dir / "r1" / "tasks"
    task_dir.mkdir(parents=True)
    task_dir.joinpath("t1.json").write_text(
        json.dumps(
            [
                {"task_id": "t1", "sample_index": 0, "score": 0.1},
                {"task_id": "t1", "sample_index": 1, "score": 0.9},
            ]
        ),
        encoding="utf-8",
    )

    result = DataLoader(results_dir=str(run_dir)).get_task("r1", "t1")
    assert result is not None
    assert result.sample_index == 1
    assert result.score == 0.9


def test_get_task_accepts_dict_format_backcompat(tmp_path: Path):
    run_dir = tmp_path / "runs"
    task_dir = run_dir / "r1" / "tasks"
    task_dir.mkdir(parents=True)
    task_dir.joinpath("t1.json").write_text(
        json.dumps({"task_id": "t1", "sample_index": 0, "score": 0.7}),
        encoding="utf-8",
    )

    result = DataLoader(results_dir=str(run_dir)).get_task("r1", "t1")
    assert result is not None
    assert result.sample_index == 0
    assert result.score == 0.7


def test_deterministic_run_id_differs_by_scorer():
    req_a = _job_request(scorer_name="exact_match")
    req_b = _job_request(scorer_name="swebench_pro")

    assert JobManager._deterministic_run_id(req_a) != JobManager._deterministic_run_id(req_b)


def test_deterministic_run_id_differs_by_sampling():
    req_a = _job_request(
        num_samples=1,
        agent_config={"model": "demo-model", "temperature": 0.1},
    )
    req_b = _job_request(
        num_samples=3,
        agent_config={"model": "demo-model", "temperature": 0.7},
    )

    assert JobManager._deterministic_run_id(req_a) != JobManager._deterministic_run_id(req_b)


def test_concurrent_duplicate_active_run_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    manager = JobManager(results_dir=str(tmp_path / "results"), configs_dir=str(tmp_path / "configs"))
    monkeypatch.setattr(threading.Thread, "start", lambda self: None)
    req = _job_request()

    manager.create_job(req)
    with pytest.raises(ValueError):
        manager.create_job(req)


def test_redo_all_resets_offset_to_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    run_id = "redo-run"
    (results_dir / f"{run_id}.jsonl").write_text(
        json.dumps({"task_id": "t1", "sample_index": 0, "score": 1.0, "correct": True}) + "\n",
        encoding="utf-8",
    )
    manager = JobManager(results_dir=str(results_dir), configs_dir=str(tmp_path / "configs"))
    manager._jobs["job1"] = JobStatus(job_id="job1", run_id=run_id, status="pending", original_request={})
    manager._job_logs["job1"] = io.StringIO()
    _install_runner_stubs(monkeypatch)

    manager._run_job("job1", run_id, _job_request(redo_all=True))

    assert manager._job_progress_offsets["job1"] == 0


def test_redo_all_does_not_reset_non_redo_resume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    run_id = "resume-run"
    (results_dir / f"{run_id}.jsonl").write_text(
        json.dumps({"task_id": "t1", "sample_index": 0, "score": 1.0, "correct": True}) + "\n",
        encoding="utf-8",
    )
    manager = JobManager(results_dir=str(results_dir), configs_dir=str(tmp_path / "configs"))
    manager._jobs["job1"] = JobStatus(job_id="job1", run_id=run_id, status="pending", original_request={})
    manager._job_logs["job1"] = io.StringIO()
    _install_runner_stubs(monkeypatch)

    manager._run_job("job1", run_id, _job_request(redo_all=False))

    assert manager._job_progress_offsets["job1"] == 1


def test_load_jsonl_skips_malformed_line(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    path = tmp_path / "runs.jsonl"
    path.write_text('{"task_id":"t1","score":1}\nBROKEN LINE\n{"task_id":"t2","score":2}\n', encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        rows = _load_jsonl(path)

    assert [row["task_id"] for row in rows] == ["t1", "t2"]
    assert any("Skipping malformed" in record.message for record in caplog.records)


def test_openclaw_retry_reissues_request(monkeypatch: pytest.MonkeyPatch):
    stream_calls: list[int] = []

    class FakeResponse:
        def __init__(self, attempt: int):
            self.attempt = attempt
            self.status_code = 200
            self.headers = {"content-type": "text/event-stream"}
            self.text = ""

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def iter_lines(self):
            if self.attempt == 1:
                raise httpx.ReadTimeout("transient")
            yield 'data: {"choices":[{"delta":{"content":"42"}}]}'
            yield "data: [DONE]"

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, headers=None, json=None):
            stream_calls.append(1)
            return FakeResponse(len(stream_calls))

    monkeypatch.setattr(httpx, "Client", FakeClient)
    monkeypatch.setattr(OpenClawAgent, "_health_check", lambda self, url, headers: None)
    monkeypatch.setattr(OpenClawAgent, "_retrieve_trajectory_sync", lambda *args, **kwargs: [])
    monkeypatch.setattr("alphadiana.agent.openclaw.time.sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("alphadiana.agent.openclaw.random.uniform", lambda *_args, **_kwargs: 0.0)

    agent = OpenClawAgent()
    agent.setup({"api_base": "http://example.test/v1", "max_attempts": 2, "request_timeout": 5})
    task = BenchmarkTask(task_id="t1", problem="What is 6*7?", ground_truth="42")

    result = agent.solve(task)

    assert result.answer == "42"
    assert len(stream_calls) == 2


def test_pool_semaphore_not_set_on_replacement_failure():
    class BrokenSession:
        def reset(self):
            raise RuntimeError("boom")

        def close(self):
            return None

    class FakeSandbox:
        def __init__(self):
            self.calls = 0

        def create_session(self):
            self.calls += 1
            if self.calls == 1:
                return BrokenSession()
            raise RuntimeError("replacement failed")

    pool = SandboxPool(FakeSandbox(), size=1)
    session = pool.acquire()
    assert pool._event.is_set() is False

    pool.release(session)

    assert pool._event.is_set() is False
