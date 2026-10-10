"""Captured native events survive the canonical response/analysis boundary."""

import json
from pathlib import Path

import pytest

from alphadiana.analysis.actions.keyword_baseline import classify_action_intent
from alphadiana.harness.zeroclaw.agent import ZeroClawAgent
from alphadiana.harness.zeroclaw.trace import normalize_trace

FIXTURE = Path(__file__).parents[1] / "fixtures/zeroclaw/native-0.6.9-tool-trace.jsonl"


def records():
    return [json.loads(line) for line in FIXTURE.read_text().splitlines()]


def test_captured_tool_trace_has_real_actions_and_observed_usage():
    trajectory, reasoning, usage, coverage = normalize_trace(
        [{"role": "user", "content": "Calculate"}], records(), final_output="5017"
    )
    assert len(trajectory) == 4
    call, observation, answer = trajectory[1:]
    assert call["name"] == "shell"
    assert classify_action_intent(call)[0] == "tool_use"
    assert observation["role"] == "tool" and observation["content"] == "5017\n"
    assert observation["success"] is True
    assert answer["type"] == "answer" and answer["content"] == "5017"
    assert reasoning == []  # Native trace does not provide private reasoning.
    assert usage == {}
    assert coverage["observed_token_usage"] == {
        "prompt_tokens": 25175,
        "completion_tokens": 45,
        "total_tokens": 25220,
    }
    assert coverage["usage_complete"] is False
    assert coverage["usage_incomplete_reason"] == "whole_run_coverage_unverified"
    assert coverage["unmapped_event_counts"] == {}


@pytest.mark.parametrize("missing", [None, -1, True, "40"])
def test_partial_or_malformed_usage_is_not_a_total(missing):
    rows = records()
    rows[1]["payload"]["output_tokens"] = missing
    _, _, usage, coverage = normalize_trace([], rows, final_output="5017")
    assert usage == {}
    assert coverage["responses_with_usage"] == 1
    assert coverage["usage_complete"] is False


def test_truncated_trace_and_unknown_events_are_visible():
    rows = records()[1:] + [{"event_type": "future_event", "payload": {}}]
    _, _, usage, coverage = normalize_trace([], rows, final_output="")
    assert usage == {}
    assert coverage["unmapped_event_counts"] == {"future_event": 1}


def test_native_response_boundary_persists_usage_and_raw_trace():
    agent = ZeroClawAgent()
    agent._logprob_capture = {"enabled": False}
    response = agent._build_cli_response(
        prompt="Calculate",
        raw_output="5017",
        raw_stderr="",
        runtime_trace=FIXTURE.read_text(),
        attachment_items=[],
        metadata={},
        wall_time_sec=1,
        system_prompt="",
        request_messages=[],
    )
    assert response.token_usage == {}
    coverage = response.metadata["native_trace_coverage"]
    assert coverage["observed_token_usage"]["total_tokens"] == 25220
    assert coverage["usage_complete"] is False
    assert (
        response.workspace_file_contents["runtime_trace.jsonl"] == FIXTURE.read_text()
    )
    assert len(response.trajectory) == 3


def test_balanced_rolling_suffix_is_not_published_as_whole_run_usage(tmp_path):
    import alphadiana as ad
    from alphadiana.results.store import ResultStore
    from alphadiana.results.artifacts import Artifacts
    from alphadiana.benchmarks.base import BenchmarkTask
    from alphadiana.scorer.numeric import NumericScorer

    # Drop an entire request/response pair from the real capture. Counts still
    # balance, but the retained five output tokens omit forty earlier tokens.
    suffix = "\n".join(json.dumps(row) for row in records()[2:])
    agent = ZeroClawAgent()
    agent._logprob_capture = {"enabled": False}
    response = agent._build_cli_response(
        prompt="Calculate",
        raw_output="5017",
        raw_stderr="",
        runtime_trace=suffix,
        attachment_items=[],
        metadata={},
        wall_time_sec=1,
        system_prompt="",
        request_messages=[],
    )
    assert response.token_usage == {}
    coverage = response.metadata["native_trace_coverage"]
    assert (
        coverage["event_counts"]["llm_request"] == coverage["responses_with_usage"] == 1
    )
    assert coverage["observed_token_usage"]["completion_tokens"] == 5
    assert coverage["usage_complete"] is False

    task = BenchmarkTask("task", "Calculate", "5017")
    store = ResultStore(str(tmp_path), "suffix")
    store.append(task, response, NumericScorer().score(task, response))
    artifacts = Artifacts(store.path)
    row = artifacts.records()[0]
    assert row["metadata"]["native_trace_coverage"] == coverage
    assert artifacts.file(row, "response_stream").read_text() == suffix
    performance = ad.analyzer(artifacts).compute()["analyses"]["execution"]["tables"][
        "performance"
    ][0]
    assert performance["mean_output_tokens"] is None
    assert performance["tokens_per_correct"] is None


def test_legacy_message_trace_retains_shared_projection():
    trajectory, _, usage, coverage = normalize_trace(
        [], [{"type": "text", "content": "old format"}], final_output="old format"
    )
    assert trajectory == [
        {"role": "assistant", "type": "text", "content": "old format"}
    ]
    assert usage == {} and coverage == {"native_records": 0}


def test_native_final_prevents_reappending_cli_transcript():
    trajectory, _, _, _ = normalize_trace(
        [], records(), final_output="Earlier assistant text\n5017"
    )
    assert len(trajectory) == 3
    assert trajectory[-1]["content"] == "5017"


def test_persisted_tools_keep_call_arguments_and_result_role():
    from alphadiana.results.trace import normalize_persisted_trajectory

    trajectory, _, _, _ = normalize_trace([], records(), final_output="5017")
    persisted = normalize_persisted_trajectory(trajectory)
    assert persisted[0]["type"] == "tool_use"
    assert "173 * 29" in str(persisted[0]["tool_calls"][0]["input"])
    assert persisted[1]["type"] == "tool_result"
    assert persisted[1]["role"] == "tool"
    assert persisted[1]["tool_results"][0]["content"] == "5017"
