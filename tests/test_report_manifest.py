from __future__ import annotations

from alphadiana.agent.base import AgentResponse
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.config.experiment_config import ExperimentConfig
from alphadiana.results.report import ReportGenerator
from alphadiana.results.result_store import ResultStore
from alphadiana.scorer.base import ScoreResult


def _cfg(run_id: str, *, num_samples: int = 1) -> ExperimentConfig:
    return ExperimentConfig(
        run_id=run_id,
        agent_name="test",
        agent_version="1",
        benchmark_name="test",
        scorer_name="numeric",
        num_samples=num_samples,
    )


def _task(task_id: str, *, category: str = "default") -> BenchmarkTask:
    return BenchmarkTask(
        task_id=task_id,
        problem="P",
        ground_truth="42",
        metadata={"category": category},
    )


def _score(correct: bool) -> ScoreResult:
    return ScoreResult(
        correct=correct,
        score=1.0 if correct else 0.0,
        expected="42",
        predicted="42" if correct else "0",
    )


def test_report_uses_expected_denominator_from_manifest(tmp_path):
    store = ResultStore(output_dir=str(tmp_path), run_id="manifest-denom")
    store.save_manifest({
        "run_id": "manifest-denom",
        "num_samples": 1,
        "expected_task_count": 2,
        "expected_sample_count": 2,
        "expected_task_ids": ["t1", "t2"],
        "task_metadata_by_id": {
            "t1": {"category": "algebra"},
            "t2": {"category": "geometry"},
        },
    })
    store.append(_task("t1", category="algebra"), AgentResponse(answer="42"), _score(True))

    summary = ReportGenerator().generate(store, _cfg("manifest-denom"))

    assert summary.total_tasks == 2
    assert summary.expected_task_count == 2
    assert summary.expected_sample_count == 2
    assert summary.written_records == 1
    assert summary.valid_scored == 1
    assert summary.missing_samples == 1
    assert summary.missing_tasks == 1


def test_missing_records_count_as_failed_in_accuracy_total(tmp_path):
    store = ResultStore(output_dir=str(tmp_path), run_id="manifest-acc-total")
    store.save_manifest({
        "run_id": "manifest-acc-total",
        "num_samples": 1,
        "expected_task_count": 2,
        "expected_sample_count": 2,
        "expected_task_ids": ["t1", "t2"],
    })
    store.append(_task("t1"), AgentResponse(answer="42"), _score(True))

    summary = ReportGenerator().generate(store, _cfg("manifest-acc-total"))

    assert summary.accuracy == 1.0
    assert summary.accuracy_total == 0.5
    assert summary.failed == 1


def test_pass_at_k_includes_missing_tasks(tmp_path):
    store = ResultStore(output_dir=str(tmp_path), run_id="manifest-passk")
    store.save_manifest({
        "run_id": "manifest-passk",
        "num_samples": 2,
        "expected_task_count": 2,
        "expected_sample_count": 4,
        "expected_task_ids": ["t1", "t2"],
    })
    store.append(_task("t1"), AgentResponse(answer="42"), _score(True), sample_index=0)
    store.append(_task("t1"), AgentResponse(answer="0"), _score(False), sample_index=1)

    summary = ReportGenerator().generate(store, _cfg("manifest-passk", num_samples=2))

    assert summary.pass_at_k == 0.5
    assert summary.avg_at_k == 0.25
    assert summary.missing_tasks == 1
    assert summary.missing_samples == 2


def test_score_none_error_none_counts_invalid_not_completed(tmp_path):
    store = ResultStore(output_dir=str(tmp_path), run_id="manifest-invalid")
    store.save_manifest({
        "run_id": "manifest-invalid",
        "num_samples": 1,
        "expected_task_count": 1,
        "expected_sample_count": 1,
        "expected_task_ids": ["t1"],
    })
    store.append_error(
        _task("t1"),
        error={"error": "", "error_type": ""},
        response=AgentResponse(answer=None),
    )
    records = store.load()
    records[0]["error"] = None
    records[0]["score_status"] = "unscored"
    with open(store.path, "w", encoding="utf-8") as handle:
        import json
        handle.write(json.dumps(records[0]) + "\n")

    summary = ReportGenerator().generate(store, _cfg("manifest-invalid"))

    assert summary.valid_scored == 0
    assert summary.invalid_scored == 1
    assert summary.error_records == 0
    assert summary.missing_samples == 0
    assert summary.completed == 0


def test_legacy_timeout_error_counts_as_valid_zero_in_report(tmp_path):
    store = ResultStore(output_dir=str(tmp_path), run_id="manifest-legacy-timeout")
    store.save_manifest({
        "run_id": "manifest-legacy-timeout",
        "num_samples": 1,
        "expected_task_count": 2,
        "expected_sample_count": 2,
        "expected_task_ids": ["t1", "t2"],
    })
    store.append(_task("t1"), AgentResponse(answer="42"), _score(True))
    with open(store.path, "a", encoding="utf-8") as handle:
        import json
        handle.write(json.dumps({
            "task_id": "t2",
            "sample_index": 0,
            "predicted": None,
            "correct": None,
            "score": None,
            "rationale": "OpenClaw stream total timeout after 60.0s",
            "finish_reason": "",
            "metadata": {"failure_reason": "timeout"},
            "error": {
                "error": "OpenClaw stream total timeout after 60.0s",
                "error_type": "timeout",
            },
            "score_status": "runtime_error",
        }) + "\n")

    summary = ReportGenerator().generate(store, _cfg("manifest-legacy-timeout"))

    assert summary.valid_scored == 2
    assert summary.error_records == 0
    assert summary.completed == 2
    assert summary.failed == 0
    assert summary.accuracy == 0.5
    assert summary.mean_score == 0.5


def test_preserved_failure_counts_invalid_not_valid_scored(tmp_path):
    store = ResultStore(output_dir=str(tmp_path), run_id="manifest-preserved")
    store.save_manifest({
        "run_id": "manifest-preserved",
        "num_samples": 1,
        "expected_task_count": 1,
        "expected_sample_count": 1,
        "expected_task_ids": ["t1"],
    })
    store.append(_task("t1"), AgentResponse(
        answer="0",
        finish_reason="preserved_failure",
        metadata={"zeroclaw_preserved_failure": True},
    ), _score(False))

    summary = ReportGenerator().generate(store, _cfg("manifest-preserved"))

    assert summary.valid_scored == 0
    assert summary.invalid_scored == 1
    assert summary.error_records == 0
    assert summary.completed == 0
