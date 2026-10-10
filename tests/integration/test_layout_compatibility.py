"""Canonical owners keep the supported imports and archived inputs usable."""

import hashlib
import importlib
import json
import runpy
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


def test_source_analysis_entry_points_find_the_same_paper_summary():
    from alphadiana.analysis.scripts import (
        analyze_behavior_insights,
        analyze_gpqa_trajectories,
    )

    root = Path(__file__).resolve().parents[2]
    path = root / "scripts/paper_analysis/data/measurement_summary.json"
    assert analyze_behavior_insights.DEFAULT_MEASUREMENT_SUMMARY == path
    assert analyze_gpqa_trajectories.DEFAULT_MEASUREMENT_SUMMARY == path
    assert analyze_behavior_insights._load_measurement_summary(path) == json.loads(
        path.read_text()
    )


def test_audit_frozen_code_paths_are_preserved():
    """The human audit hashes these paths relative to the workflow directory."""
    root = Path(__file__).resolve().parents[2]
    code = root / "scripts/action_audit"
    protocol = code / "../../alphadiana/analysis/actions/protocol.py"
    assert protocol.resolve() == root / "alphadiana/analysis/actions/protocol.py"
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


@pytest.mark.parametrize("inputs", ["missing", "empty"])
def test_chord_plot_rejects_unobserved_inputs(tmp_path, inputs):
    """A missing archive must not become fabricated padded transition plots."""
    pytest.importorskip("matplotlib")
    root = Path(__file__).resolve().parents[2]
    namespace = runpy.run_path(
        str(root / "scripts/paper_analysis/plot_action_chords.py")
    )
    load = namespace["load_transitions"]
    load.__globals__["DATA_DIR"] = tmp_path
    if inputs == "empty":
        for folder in ("six_action_statistics", "six_action_statistics_gemma"):
            path = tmp_path / folder / "action_transitions_by_outcome.csv"
            path.parent.mkdir()
            path.write_text(
                "benchmark,harness,outcome,from_action,to_action,transition_count\n"
            )
    with pytest.raises(FileNotFoundError if inputs == "missing" else ValueError):
        load()
    assert not list(tmp_path.rglob("*.pdf"))


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
    assert manager._count_jsonl(str(path)) == (1, 0, 1)
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
