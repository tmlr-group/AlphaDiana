from __future__ import annotations

import json

from alphadiana.agent.base import AgentResponse
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.results.result_store import ResultStore
from alphadiana.scorer.base import ScoreResult


def _task(task_id: str = "t1") -> BenchmarkTask:
    return BenchmarkTask(task_id=task_id, problem="P", ground_truth="42")


def _score(correct: bool = True) -> ScoreResult:
    return ScoreResult(
        correct=correct,
        score=1.0 if correct else 0.0,
        expected="42",
        predicted="42" if correct else "0",
    )


def test_score_none_error_none_is_not_completed(tmp_path):
    store = ResultStore(output_dir=str(tmp_path), run_id="status-none")
    with open(store.path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"task_id": "t1", "score": None, "correct": None}) + "\n")

    assert store.completed_task_ids() == set()


def test_preserved_failure_score_zero_is_not_completed(tmp_path):
    store = ResultStore(output_dir=str(tmp_path), run_id="status-preserved")
    with open(store.path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "task_id": "t1",
            "score": 0.0,
            "correct": False,
            "finish_reason": "preserved_failure",
            "metadata": {"zeroclaw_preserved_failure": True},
        }) + "\n")

    assert store.completed_task_ids() == set()


def test_scorer_mismatch_is_not_completed(tmp_path):
    store = ResultStore(
        output_dir=str(tmp_path),
        run_id="status-mismatch",
        run_metadata={"scorer_name": "math_verify"},
    )
    store.append(_task("t1"), AgentResponse(answer="42"), _score())

    assert store.completed_task_ids(scorer_name="imo_verify") == set()
    assert store.completed_task_ids(scorer_name="math_verify") == {"t1"}


def test_legacy_scored_record_same_scorer_is_completed(tmp_path):
    store = ResultStore(output_dir=str(tmp_path), run_id="status-legacy")
    with open(store.path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "task_id": "t1",
            "scorer_name": "imo_verify",
            "score": 1.0,
            "correct": True,
        }) + "\n")

    assert store.completed_task_ids(scorer_name="imo_verify") == {"t1"}


def test_append_error_not_completed(tmp_path):
    store = ResultStore(output_dir=str(tmp_path), run_id="status-error")
    store.append_error(
        _task("t1"),
        error={"error": "boom", "error_type": "RuntimeError"},
        response=AgentResponse(answer=None),
    )

    records = store.load()
    assert records[0]["score_status"] == "runtime_error"
    assert store.completed_task_ids() == set()


def test_timeout_scored_zero_is_completed(tmp_path):
    store = ResultStore(output_dir=str(tmp_path), run_id="status-timeout-zero")
    response = AgentResponse(
        answer=None,
        finish_reason="timeout",
        metadata={
            "opencode_error_name": "OpenCodeTimeout",
            "opencode_error_message": "The operation timed out.",
            "opencode_timeout_scored_zero": True,
        },
    )
    score = ScoreResult(
        correct=False,
        score=0.0,
        expected="42",
        predicted=None,
        rationale="No answer produced.",
    )

    store.append(_task("t1"), response, score)

    records = store.load()
    assert records[0]["score_status"] == "valid_scored"
    assert records[0]["score"] == 0.0
    assert records[0]["finish_reason"] == "timeout"
    assert records[0]["metadata"]["opencode_error_message"] == "The operation timed out."
    assert store.completed_task_ids() == {"t1"}


def test_completed_sample_ids_uses_valid_status(tmp_path):
    store = ResultStore(
        output_dir=str(tmp_path),
        run_id="status-samples",
        run_metadata={"scorer_name": "imo_verify"},
    )
    store.append(_task("t1"), AgentResponse(answer="42"), _score(), sample_index=0)
    with open(store.path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "task_id": "t1",
            "sample_index": 1,
            "scorer_name": "imo_verify",
            "score": 0.0,
            "correct": False,
            "finish_reason": "preserved_failure",
            "metadata": {"zeroclaw_preserved_failure": True},
        }) + "\n")

    assert store.completed_sample_ids(scorer_name="imo_verify") == {("t1", 0)}


def test_legacy_timeout_error_is_completed_and_normalized(tmp_path):
    store = ResultStore(
        output_dir=str(tmp_path),
        run_id="status-legacy-timeout",
        run_metadata={"scorer_name": "imo_verify"},
    )
    with open(store.path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "task_id": "t1",
            "sample_index": 0,
            "scorer_name": "imo_verify",
            "predicted": None,
            "correct": None,
            "score": None,
            "rationale": "The operation timed out.",
            "finish_reason": "",
            "metadata": {
                "returncode": -1,
                "opencode_error_name": "OpenCodeNonZeroExit",
                "opencode_error_message": "The operation timed out.",
            },
            "response_json": {"returncode": -1},
            "error": {"error": "The operation timed out.", "error_type": "agent_error"},
            "score_status": "agent_error",
        }) + "\n")

    records = store.load()

    assert records[0]["score_status"] == "valid_scored"
    assert records[0]["score"] == 0.0
    assert records[0]["correct"] is False
    assert records[0]["predicted"] is None
    assert records[0]["finish_reason"] == "timeout"
    assert records[0]["error"] is None
    assert records[0]["metadata"]["legacy_timeout_scored_zero"] is True
    assert store.completed_sample_ids(scorer_name="imo_verify") == {("t1", 0)}


def test_legacy_timeout_runtime_only_zeroclaw_record_is_completed(tmp_path):
    store = ResultStore(output_dir=str(tmp_path), run_id="status-legacy-zeroclaw-timeout")
    with open(store.path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "task_id": "t1",
            "sample_index": 0,
            "predicted": None,
            "correct": None,
            "score": None,
            "rationale": "ZeroClaw produced no assistant output; stdout contained only runtime/provider logs.",
            "raw_output": json.dumps({"event_type": "llm_request", "model": "qwen"}),
            "wall_time_sec": 9301.0,
            "metadata": {"failure_reason": "empty_response"},
            "error": {
                "error": "ZeroClaw produced no assistant output; stdout contained only runtime/provider logs.",
                "error_type": "empty_response",
            },
            "score_status": "runtime_error",
        }) + "\n")

    records = store.load()

    assert records[0]["score_status"] == "valid_scored"
    assert records[0]["score"] == 0.0
    assert records[0]["finish_reason"] == "timeout"
    assert records[0]["metadata"]["failure_reason"] == "timeout"
    assert store.completed_task_ids() == {"t1"}


def test_non_timeout_nonzero_agent_error_is_not_legacy_timeout(tmp_path):
    store = ResultStore(output_dir=str(tmp_path), run_id="status-non-timeout-nonzero")
    with open(store.path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "task_id": "t1",
            "predicted": None,
            "correct": None,
            "score": None,
            "rationale": "OpenCode exited with return code 137.",
            "metadata": {
                "returncode": 137,
                "opencode_error_name": "OpenCodeNonZeroExit",
                "opencode_error_message": "OpenCode exited with return code 137.",
            },
            "error": {
                "error": "OpenCode exited with return code 137.",
                "error_type": "agent_error",
            },
            "score_status": "agent_error",
        }) + "\n")

    records = store.load()

    assert records[0]["score_status"] == "agent_error"
    assert records[0]["score"] is None
    assert store.completed_task_ids() == set()
