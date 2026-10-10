"""Canonical owners keep the supported imports and archived inputs usable."""

import hashlib
import importlib
import json
import runpy
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "old,new,name",
    [
        ("analysis.action_annotations", "analysis.annotations.actions", "ActionAnnotations"),
        ("analysis.action_segmentation", "analysis.annotations.segmentation", "annotate_trajectory"),
        ("analysis.failure_annotations", "analysis.annotations.failures", "FailureAnnotations"),
        ("analysis.judge", "analysis.annotations.judge", "CachedJudge"),
        ("analysis.action_events", "analysis.actions.events", "ActionEvent"),
        ("analysis.action_features", "analysis.actions.features", "build_record_feature_rows"),
        ("analysis.clustering", "analysis.actions.clustering", "cluster_rows"),
        ("analysis.case_studies", "analysis.actions.cases", "select_case_studies"),
        ("analysis.trajectory_metrics", "analysis.actions.metrics", "trajectory_key"),
        ("analysis.action_report", "analysis.actions.report", "render_markdown_report"),
        ("analysis.trajectory_reports", "analysis.actions.trajectory_reports", "write_phase14_outputs"),
        ("analysis.insight_corpus", "analysis.insights.corpus", "CorpusSpec"),
        ("analysis.insight_measurements", "analysis.insights.measurements", "InsightClaim"),
        ("analysis.insight_cases", "analysis.insights.cases", "select_insight_case_anchors"),
        ("analysis.insight_reports", "analysis.insights.reports", "write_phase15_outputs"),
        ("analysis.result_reader", "analysis.io.result_reader", "RunBundle"),
        ("analysis.reliability", "analysis.run_summary", "compute_reliability_summary"),
        ("analysis.entropy_analysis", "analysis.entropy", "EntropyAnalysis"),
        ("analysis.process_entropy", "analysis.entropy", "RequestEntropy"),
        ("utils.rollout_campaign", "engine.rollout_campaign", "ConcreteRun"),
        ("analysis.dashboard.backend.data_loader", "dashboard.backend.data_loader", "DataLoader"),
        ("analysis.dashboard.backend.job_manager", "dashboard.backend.job_manager", "JobManager"),
    ],
)
def test_compatibility_names_are_the_canonical_objects(old, new, name):
    if "dashboard" in old:
        pytest.importorskip("fastapi")
    assert getattr(importlib.import_module("alphadiana." + old), name) is getattr(
        importlib.import_module("alphadiana." + new), name
    )


def test_source_analysis_entry_points_find_the_same_paper_summary():
    from alphadiana.analysis.scripts import analyze_behavior_insights, analyze_gpqa_trajectories

    root = Path(__file__).resolve().parents[2]
    path = root / "scripts/paper_analysis/data/measurement_summary.json"
    assert analyze_behavior_insights.DEFAULT_MEASUREMENT_SUMMARY == path
    assert analyze_gpqa_trajectories.DEFAULT_MEASUREMENT_SUMMARY == path
    assert analyze_behavior_insights._load_measurement_summary(path) == json.loads(path.read_text())


def test_audit_frozen_code_paths_are_preserved():
    """The human audit hashes these paths relative to the workflow directory."""
    root = Path(__file__).resolve().parents[2]
    code = root / "scripts/action_audit"
    protocol = code / "../../alphadiana/analysis/action_protocol.py"
    assert protocol.resolve() == root / "alphadiana/analysis/action_protocol.py"
    from alphadiana.analysis.action_protocol import prompt_bytes

    assert hashlib.sha256(prompt_bytes()).digest() == hashlib.sha256(
        (root / "alphadiana/analysis/action_prompt.md").read_bytes()
    ).digest()


def test_relocated_dashboard_creates_reads_and_restores_jobs(tmp_path, monkeypatch):
    """Exercise the HTTP and persistence boundaries without starting a solver."""
    import threading

    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from alphadiana.dashboard.backend import api
    from alphadiana.dashboard.backend.job_manager import JobManager
    from alphadiana.dashboard.backend.main import PROJECT_ROOT, app
    from alphadiana.analysis.dashboard.backend.main import app as old_app

    assert app is old_app
    assert PROJECT_ROOT == Path(__file__).resolve().parents[2]
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
        response = client.post("/api/jobs", json={
            "run_id": "layout-smoke",
            "agent_name": "direct_llm",
            "benchmark_name": "aime",
        })
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
    namespace = runpy.run_path(str(root / "scripts/paper_analysis/plot_action_chords.py"))
    load = namespace["load_transitions"]
    load.__globals__["DATA_DIR"] = tmp_path
    if inputs == "empty":
        for folder in ("six_action_statistics", "six_action_statistics_gemma"):
            path = tmp_path / folder / "action_transitions_by_outcome.csv"
            path.parent.mkdir()
            path.write_text("benchmark,harness,outcome,from_action,to_action,transition_count\n")
    with pytest.raises(FileNotFoundError if inputs == "missing" else ValueError):
        load()
    assert not list(tmp_path.rglob("*.pdf"))
