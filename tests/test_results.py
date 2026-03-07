"""Tests for result storage and report generation."""

import pytest

from alphadiana.agent.base import AgentResponse
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.config.experiment_config import ExperimentConfig
from alphadiana.results.report import ReportGenerator
from alphadiana.results.result_store import ResultStore
from alphadiana.scorer.base import ScoreResult


def test_result_store_append_and_load(tmp_path):
    """Append a result to the store and load it back."""
    store = ResultStore(output_dir=str(tmp_path), run_id="test-run")

    task = BenchmarkTask(
        task_id="task-1",
        problem="What is 6 * 7?",
        ground_truth="42",
    )
    response = AgentResponse(
        answer="42",
        trajectory=[{"step": 1, "action": "compute"}],
        token_usage={"prompt_tokens": 10, "completion_tokens": 5},
        wall_time_sec=1.5,
    )
    score = ScoreResult(
        correct=True,
        score=1.0,
        expected="42",
        predicted="42",
        rationale="Exact match.",
    )

    store.append(task, response, score)

    results = store.load()
    assert len(results) == 1

    record = results[0]
    assert record["task_id"] == "task-1"
    assert record["problem"] == "What is 6 * 7?"
    assert record["ground_truth"] == "42"
    assert record["predicted"] == "42"
    assert record["correct"] is True
    assert record["score"] == 1.0
    assert record["rationale"] == "Exact match."
    assert record["wall_time_sec"] == 1.5
    assert record["token_usage"]["prompt_tokens"] == 10
    assert "timestamp" in record


def test_report_generator(tmp_path):
    """Generate a summary from stored results."""
    store = ResultStore(output_dir=str(tmp_path), run_id="report-test")

    # Append two results: one correct, one incorrect.
    for i, (answer, correct) in enumerate([("42", True), ("99", False)]):
        task = BenchmarkTask(
            task_id=f"task-{i}",
            problem=f"Problem {i}",
            ground_truth="42",
        )
        response = AgentResponse(
            answer=answer,
            token_usage={"prompt_tokens": 10, "completion_tokens": 5},
            wall_time_sec=2.0,
        )
        score = ScoreResult(
            correct=correct,
            score=1.0 if correct else 0.0,
            expected="42",
            predicted=answer,
        )
        store.append(task, response, score)

    config = ExperimentConfig(
        run_id="report-test",
        agent_name="test-agent",
        agent_version="v1.0",
        benchmark_name="test-bench",
        scorer_name="exact_match",
    )

    report_gen = ReportGenerator()
    summary = report_gen.generate(store, config)

    assert summary.run_id == "report-test"
    assert summary.agent == "test-agent"
    assert summary.agent_version == "v1.0"
    assert summary.benchmark == "test-bench"
    assert summary.total_tasks == 2
    assert summary.completed == 2
    assert summary.accuracy == 0.5
    assert summary.mean_score == 0.5
    assert summary.mean_wall_time_sec == 2.0
    assert summary.total_tokens["prompt_tokens"] == 20
    assert summary.total_tokens["completion_tokens"] == 10

    # Test markdown generation.
    markdown = report_gen.to_markdown(summary)
    assert "report-test" in markdown
    assert "test-agent" in markdown
    assert "0.5000" in markdown
