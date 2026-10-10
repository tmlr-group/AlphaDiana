"""Observed tool feedback, missingness and equal-call binning contracts."""

import json
import math

import pytest

import alphadiana as ad
from alphadiana.analysis.measurements import RequestEntropy
from alphadiana.results.artifacts import Artifacts


def test_entropy_uses_observed_request_boundaries_and_survives_rescoring(tmp_path):
    run = tmp_path / "run"
    (run / "logprobs").mkdir(parents=True)
    path = tmp_path / "run.jsonl"
    path.write_text(
        json.dumps(
            {
                "task_id": "t",
                "artifact_manifest": {"files": {"logprobs_float": "logprobs/t.jsonl"}},
            }
        )
        + "\n"
    )
    top = [{"token": "a", "logprob": -1}, {"token": "b", "logprob": -1}]
    tokens = [
        {
            "call_id": "first",
            "call_token_index": 0,
            "follows_tool_result": True,
            "top_logprobs": top,
        },
        {
            "call_id": "first",
            "call_token_index": 1,
            "follows_tool_result": True,
            "top_logprobs": top,
        },
        {
            "call_id": "second",
            "call_token_index": 0,
            "follows_tool_result": True,
            "top_logprobs": [{"token": "a", "logprob": 0}],
        },
        {"call_id": "missing", "call_token_index": 0, "follows_tool_result": False},
        {"top_logprobs": top},
    ]
    (run / "logprobs/t.jsonl").write_text("".join(json.dumps(t) + "\n" for t in tokens))
    source = Artifacts(path)
    report = ad.analyzer(source, plugins=[RequestEntropy(bin_size=2)]).compute()
    analysis = report["analyses"]["request_entropy"]
    (row,) = analysis["tables"]["entropy_by_request_position"]
    assert row["entropy_nats"] == pytest.approx(math.log(2) / 2)
    assert row["n_calls"] == 2
    assert row["n_tokens"] == 3
    assert analysis["coverage"]["run"]["missing_attribution"] == 1
    assert analysis["coverage"]["run"]["missing_top_k"] == 1
    original = source.records()[0]
    original["artifact_origin"] = str(run / "artifacts")
    destination = tmp_path / "other/rescored.jsonl"
    destination.parent.mkdir()
    destination.write_text(json.dumps(original) + "\n")
    derived = RequestEntropy(bin_size=2).measure([Artifacts(destination)])
    assert (
        derived.tables["entropy_by_request_position"][0]["entropy_nats"]
        == row["entropy_nats"]
    )
    assert (
        derived.coverage["rescored"]["sources"]
        == analysis["coverage"]["run"]["sources"]
    )


def test_missing_sidecar_is_missing_not_zero_entropy(tmp_path):
    path = tmp_path / "old.jsonl"
    path.write_text('{"task_id":"t"}\n')
    result = RequestEntropy().measure([Artifacts(path)])
    assert result.tables["entropy_by_request_position"] == []
    assert result.coverage["old"]["missing_sidecars"] == 1
