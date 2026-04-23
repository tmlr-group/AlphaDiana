"""Phase 10 cross-harness artifact and logprob storage contract tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from alphadiana.agent.base import AgentResponse
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.results.logprob_artifacts import INT16_PROB_SCALE
from alphadiana.results.result_store import ResultStore
from alphadiana.scorer.base import ScoreResult


def _task(task_id: str) -> BenchmarkTask:
    return BenchmarkTask(task_id=task_id, problem="Choose A.", ground_truth="A")


def _score() -> ScoreResult:
    return ScoreResult(correct=True, score=1.0, expected="A", predicted="A", rationale="ok")


def _raw_logprob_record() -> dict:
    return {
        "token_index": 0,
        "token": "A",
        "logprob": -0.1,
        "top_logprobs": [
            {"token": "A", "logprob": -0.1},
            {"token": "B", "logprob": -2.0},
        ],
    }


def _append_response(tmp_path: Path, case_name: str, response: AgentResponse) -> dict:
    store = ResultStore(str(tmp_path), "run_contract")
    store.append(_task(case_name), response, _score())
    return store.load()[0]


def test_direct_llm_with_logprobs_writes_raw_and_int16_sidecars(tmp_path: Path) -> None:
    response = AgentResponse(
        answer="A",
        metadata={
            "logprobs_capture_status": "captured",
            "logprob_records": [_raw_logprob_record()],
        },
    )

    record = _append_response(tmp_path, "direct_llm_with_logprobs", response)

    assert record["logprobs_path"]
    assert record["logprobs_int16_path"]
    assert record["top_logprobs"] == 20
    assert record["int16_probability_scale"] == INT16_PROB_SCALE
    assert record["metadata"]["logprobs_capture_status"] == "captured"
    files = record["artifact_manifest"]["files"]
    assert files["logprobs_float"] == "logprobs/direct_llm_with_logprobs.jsonl"
    assert files["logprobs_int16"] == "logprobs_int16/direct_llm_with_logprobs.jsonl"


@pytest.mark.parametrize(
    ("case_name", "artifact_alias"),
    [
        ("openclaw_without_logprobs", "openclaw_session"),
        ("opencode_without_logprobs", "opencode_session"),
        ("zeroclaw_without_logprobs", "zeroclaw_session"),
    ],
)
def test_non_direct_harnesses_preserve_artifact_alias_without_logprob_sidecars(
    tmp_path: Path,
    case_name: str,
    artifact_alias: str,
) -> None:
    response = AgentResponse(
        answer="A",
        metadata={"logprobs_capture_status": "unsupported"},
        artifact_manifest={"files": {artifact_alias: f"{artifact_alias}.jsonl"}},
        workspace_file_contents={f"{artifact_alias}.jsonl": "{}\n"},
    )

    record = _append_response(tmp_path, case_name, response)

    files = record["artifact_manifest"]["files"]
    assert artifact_alias in files
    assert files[artifact_alias].endswith(f"{case_name}/workspace/{artifact_alias}.jsonl")
    assert record["logprobs_path"] == ""
    assert record["logprobs_int16_path"] == ""
    assert record["top_logprobs"] == 0
    assert record["int16_probability_scale"] == 0
    assert record["metadata"]["logprobs_capture_status"] == "unsupported"
