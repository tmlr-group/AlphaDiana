"""Rescoring refreshes score validity without erasing execution failures."""

import pytest

import alphadiana as ad
from alphadiana.analysis.io.result_store import ResultStore
from alphadiana.artifacts import Artifacts
from alphadiana.benchmarks.base import BenchmarkTask
from alphadiana.harness.base import AgentResponse
from alphadiana.scorer.numeric import NumericScorer


@pytest.mark.parametrize("answer, correct", [("2", True), ("3", False)])
def test_rescore_replaces_scorer_error_and_analyzer_counts_judgment(
    tmp_path, answer, correct
):
    store = ResultStore(str(tmp_path), "original")
    error = {"error_type": "ScorerError", "error": "constructed scoring failure"}
    store.append_error(
        BenchmarkTask("task", "Return 2", "2"),
        response=AgentResponse(answer),
        error=error,
    )
    source = Artifacts(store.path)
    original = source.path.read_bytes()
    assert source.records()[0]["score_status"] == "scorer_error"

    rescored = source.rescore(NumericScorer())
    record = rescored.records()[0]
    assert record["correct"] is correct
    assert record["score"] == float(correct)
    assert record["score_status"] == "valid_scored"
    assert record.get("error") is None
    assert record["rescoring"]["original_error"] == error
    assert record["rescoring"]["original_score_status"] == "scorer_error"
    performance = ad.analyzer(rescored).compute()["analyses"]["execution"]["tables"][
        "performance"
    ][0]
    assert performance["scored"] == 1
    assert performance["accuracy"] == float(correct)
    assert source.path.read_bytes() == original
    assert (
        rescored.rescore(NumericScorer()).records()[0]["score_status"] == "valid_scored"
    )


@pytest.mark.parametrize(
    "error_type, metadata, expected",
    [
        ("AgentError", {}, "agent_error"),
        ("ProviderError", {}, "provider_error"),
        ("RuntimeError", {}, "runtime_error"),
        ("ScorerError", {"failure_reason": "provider_error"}, "provider_error"),
        ("ScorerError", {"failure_reason": "no_answer"}, "no_answer"),
    ],
)
def test_successful_rescore_retains_solver_failure(
    tmp_path, error_type, metadata, expected
):
    store = ResultStore(str(tmp_path), "failed-solver")
    error = {"error_type": error_type, "error": "constructed failure"}
    store.append_error(
        BenchmarkTask("task", "Return 2", "2"),
        response=AgentResponse("2", metadata=metadata),
        error=error,
    )
    rescored = Artifacts(store.path).rescore(NumericScorer())
    record = rescored.records()[0]
    assert record["correct"] is True
    assert record["score_status"] == expected
    assert record["metadata"] == metadata
    assert record["rescoring"]["original_error"] == error
    if error_type != "ScorerError":
        assert record["error"] == error
    performance = ad.analyzer(rescored).compute()["analyses"]["execution"]["tables"][
        "performance"
    ][0]
    assert performance["scored"] == 0


def test_failed_rescore_leaves_source_and_destination_untouched(tmp_path):
    class FailingScorer(NumericScorer):
        def score(self, task, response):
            raise RuntimeError("new scoring failure")

    store = ResultStore(str(tmp_path), "original")
    store.append_error(
        BenchmarkTask("task", "Return 2", "2"),
        response=AgentResponse("2"),
        error={"error_type": "ScorerError"},
    )
    source = Artifacts(store.path)
    original = source.path.read_bytes()
    destination = tmp_path / "derived.jsonl"
    with pytest.raises(RuntimeError, match="new scoring failure"):
        source.rescore(FailingScorer(), output=destination)
    assert source.path.read_bytes() == original
    assert not destination.exists()
