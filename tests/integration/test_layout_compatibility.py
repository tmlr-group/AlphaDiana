"""Canonical owners keep the supported imports and archived inputs usable."""

import hashlib
import importlib
import json
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
