"""Canonical owners keep the supported imports and runtime interfaces usable."""

import hashlib
import json
from pathlib import Path

import pytest


def test_public_analysis_entry_uses_canonical_pipeline(tmp_path):
    import alphadiana as ad
    from alphadiana.analysis.pipeline import Analyzer

    results = tmp_path / "public-api.jsonl"
    results.write_text("")
    assert isinstance(ad.analyzer(results, plugins=[]), Analyzer)


def test_results_and_measurement_imports_do_not_load_plotting_or_clustering():
    import os
    import subprocess
    import sys

    code = """
import importlib.abc
import sys
class RejectOptional(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'scipy', 'matplotlib'}:
            raise AssertionError('unexpected eager optional import: ' + fullname)
sys.meta_path.insert(0, RejectOptional())
from alphadiana.results.store import ResultStore
from alphadiana.results.statistics import RunStatistics
from alphadiana.analysis.measurements import RequestEntropy
assert RunStatistics.from_records([]).correct == 0
"""
    root = str(Path(__file__).resolve().parents[2])
    env = dict(os.environ, PYTHONPATH=root, PYTHONDONTWRITEBYTECODE="1")
    subprocess.run([sys.executable, "-c", code], env=env, check=True)


@pytest.mark.parametrize("module_name", ["analyze_behavior_insights", "analyze_gpqa_trajectories"])
def test_source_analysis_entry_points_accept_optional_measurement_summary(tmp_path, module_name):
    from importlib import import_module

    cli = import_module(f"alphadiana.analysis.scripts.{module_name}")
    assert cli._build_parser().parse_args([]).measurement_summary is None
    assert cli._load_measurement_summary(None) is None
    assert cli._load_measurement_summary(tmp_path / "missing.json") is None

    summary = {"paired_net_gain": [{"harness": "fixture", "paired_net_gain": 2}]}
    path = tmp_path / "measurement_summary.json"
    path.write_text(json.dumps(summary))
    args = cli._build_parser().parse_args(["--measurement-summary", str(path)])
    assert cli._load_measurement_summary(args.measurement_summary) == summary


def test_action_prompt_bytes_match_packaged_source():
    """Action annotation uses the prompt bytes shipped with the package."""
    root = Path(__file__).resolve().parents[2]
    from alphadiana.analysis.actions.protocol import prompt_bytes

    assert (
        hashlib.sha256(prompt_bytes()).digest()
        == hashlib.sha256(
            (root / "alphadiana/analysis/actions/action_prompt.md").read_bytes()
        ).digest()
    )


def test_relocated_dashboard_creates_reads_and_restores_jobs(tmp_path, monkeypatch):
    """Exercise the HTTP and persistence boundaries without starting a solver."""
    import threading

    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from alphadiana.dashboard.backend import api
    from alphadiana.dashboard.backend.job_manager import JobManager
    from alphadiana.dashboard.backend.main import PROJECT_ROOT, app

    assert PROJECT_ROOT == Path(__file__).resolve().parents[2]
    assert "/api/jobs" in app.openapi()["paths"]
    results = tmp_path / "results"
    manager = JobManager(str(results), str(tmp_path / "configs"))
    done = threading.Event()
    observed = []

    def finish_job(job_id, run_id, request):
        observed.append((run_id, request.agent_name, request.benchmark_name))
        manager._jobs[job_id].status = "completed"
        manager._persist_jobs()
        done.set()

    monkeypatch.setattr(manager, "_run_job", finish_job)
    monkeypatch.setattr(api, "job_manager", manager)
    test_app = FastAPI()
    test_app.include_router(api.router)
    with TestClient(test_app) as client:
        response = client.post(
            "/api/jobs",
            json={
                "run_id": "layout-smoke",
                "agent_name": "direct_llm",
                "benchmark_name": "aime",
            },
        )
        assert response.status_code == 201
        assert done.wait(5), "isolated job did not finish"
        job_id = response.json()["job_id"]
        detail = client.get(f"/api/jobs/{job_id}")
        assert detail.status_code == 200
        assert detail.json()["status"] == "completed"
        assert client.get("/api/jobs").json()[0]["job_id"] == job_id

    assert observed == [("layout-smoke", "direct_llm", "aime")]
    restored = JobManager(str(results), str(tmp_path / "configs"))
    assert restored.get_job(job_id).status == "completed"
    for log in manager._job_logs.values():
        log.close()


@pytest.mark.parametrize(
    "history",
    [
        [{"correct": True, "score": 1.0, "score_metadata": {"error": "judge failure"}}],
        [
            {"correct": True, "score": 1.0},
            {
                "correct": False,
                "score": 0.0,
                "error": {"error_type": "TimeoutError", "message": "deadline"},
            },
        ],
    ],
)
def test_jobs_and_runs_reject_invalid_latest_sample(tmp_path, history):
    from alphadiana.dashboard.backend.data_loader import DataLoader
    from alphadiana.dashboard.backend.job_manager import JobManager
    from alphadiana.dashboard.backend.models import JobStatus

    records = [
        dict(task_id="task", sample_index=0, run_id="run", **row) for row in history
    ]
    path = tmp_path / "run.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in records))
    manager = JobManager.__new__(JobManager)
    manager._results_dir = manager._logs_dir = str(tmp_path)
    job = JobStatus(job_id="job", run_id="run", status="interrupted", total_tasks=1)
    base = manager._load_statistics(str(path))
    assert len(base.results) == 1
    assert not base.valid
    assert base.metric_accuracy == 0.0
    manager._recover_progress(job)
    summary = DataLoader(str(tmp_path)).get_run("run").summary
    assert job.status == "interrupted"
    assert job.progress == summary.completed == 0
    assert job.accuracy == summary.accuracy == 0.0


def test_jobs_recover_valid_wrong_answer_as_completed(tmp_path):
    from alphadiana.dashboard.backend.job_manager import JobManager
    from alphadiana.dashboard.backend.models import JobStatus

    (tmp_path / "run.jsonl").write_text(
        json.dumps(dict(task_id="task", sample_index=0, score=0.0, correct=False))
        + "\n"
    )
    manager = JobManager.__new__(JobManager)
    manager._results_dir = manager._logs_dir = str(tmp_path)
    job = JobStatus(job_id="job", run_id="run", status="interrupted", total_tasks=1)
    manager._recover_progress(job)
    assert job.status == "completed"
    assert job.progress == 1
    assert job.accuracy == 0.0


def test_jobs_preserve_zero_contributing_empty_answers_and_refresh_accuracy(tmp_path):
    """Constructed fixture: measured zero counts for accuracy, not completion."""
    import threading

    from alphadiana.analysis.export import ReportGenerator
    from alphadiana.dashboard.backend.data_loader import DataLoader
    from alphadiana.dashboard.backend.job_manager import JobManager
    from alphadiana.dashboard.backend.models import JobStatus

    records = [
        dict(
            task_id="correct-task",
            sample_index=0,
            run_id="run",
            score=1.0,
            correct=True,
        ),
        dict(
            task_id="empty-task",
            sample_index=0,
            run_id="run",
            score=0.0,
            correct=False,
            metadata={"metric_contribution": 0},
            score_status="agent_empty_output",
        ),
    ]
    path = tmp_path / "run.jsonl"

    def write_records():
        path.write_text("".join(json.dumps(row) + "\n" for row in records))

    write_records()
    manager = JobManager.__new__(JobManager)
    manager._results_dir = manager._logs_dir = str(tmp_path)
    job = JobStatus(job_id="job", run_id="run", status="interrupted", total_tasks=2)
    manager._recover_progress(job)
    summary = DataLoader(str(tmp_path)).get_run("run").summary
    assert job.progress == summary.completed == 1
    assert (
        job.accuracy
        == summary.accuracy
        == ReportGenerator().summarize(records).accuracy
        == 0.5
    )
    assert job.status == "interrupted"

    job.status = "running"
    job.progress = 0
    job.accuracy = 0.0
    manager._lock = threading.Lock()
    manager._jobs = {"job": job}
    manager._job_logs = {}
    manager._job_progress_offsets = {}
    persisted = []
    manager._persist_jobs = lambda: persisted.append((job.progress, job.accuracy))
    manager.refresh_progress("job")
    assert persisted == [(1, 0.5)]

    records.append(
        dict(
            task_id="correct-task",
            sample_index=0,
            run_id="run",
            score=0.0,
            correct=False,
        )
    )
    write_records()
    manager.refresh_progress("job")
    assert persisted[-1] == (1, 0.0)
    assert len(persisted) == 2

    records[-1].update(score=1.0, correct=True)
    write_records()
    manager.refresh_progress("job")
    assert persisted[-1] == (1, 0.5)
    records[-1]["score_status"] = "provider_error"
    write_records()
    manager.refresh_progress("job")
    assert persisted[-1] == (0, 0.0)
