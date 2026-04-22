from __future__ import annotations

import json

from alphadiana.agent.base import AgentResponse
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.results.normalized_trace import TRACE_SCHEMA_VERSION
from alphadiana.results.result_store import ResultStore
from alphadiana.scorer.base import ScoreResult


def _score() -> ScoreResult:
    return ScoreResult(correct=True, score=1.0, expected="42", predicted="42")


def test_result_store_normalizes_persisted_trajectory_and_reasoning(tmp_path) -> None:
    store = ResultStore(output_dir=str(tmp_path), run_id="normalized-record")
    task = BenchmarkTask(task_id="t1", problem="P", ground_truth="42")
    response = AgentResponse(
        answer="42",
        trajectory=[
            {"role": "system", "content": "You are a solver."},
            {"role": "user", "content": "P"},
            {"role": "tool_use", "tool": "python", "input": {"code": "print(42)"}},
            {"role": "assistant", "content": "Solved", "thinking": "checked with python"},
        ],
        reasoning_trajectory=[
            {"role": "tool_use", "type": "tool_use", "content": "python"},
            {"role": "assistant", "reasoning_content": "checked with python"},
        ],
    )

    store.append(task, response, _score())
    record = store.load()[0]

    assert record["trajectory"][0]["type"] == "system"
    assert record["trajectory"][2]["role"] == "assistant"
    assert record["trajectory"][2]["type"] == "tool_use"
    assert record["trajectory"][2]["tool_calls"][0]["tool"] == "python"
    assert record["reasoning_trajectory"] == [
        {
            "role": "assistant",
            "type": "reasoning",
            "content": "checked with python",
        }
    ]


def test_result_store_writes_normalized_trace_artifact(tmp_path) -> None:
    store = ResultStore(
        output_dir=str(tmp_path),
        run_id="normalized-trace",
        run_metadata={"agent_name": "opencode", "agent_version": "test"},
    )
    task = BenchmarkTask(task_id="t1", problem="P", ground_truth="42")
    response = AgentResponse(
        answer="42",
        trajectory=[
            {"role": "user", "content": "P"},
            {"role": "assistant", "content": "Solved", "thinking": "check"},
        ],
        reasoning_trajectory=[],
        request_messages=[{"role": "user", "content": "P"}],
        raw_output="Solved",
        wall_time_sec=1.5,
        token_usage={"prompt_tokens": 1, "completion_tokens": 2},
        metadata={"transport": "opencode_cli", "session_id": "session-123"},
        artifact_manifest={"files": {"response_stream": "trace.jsonl"}},
        workspace_file_contents={"trace.jsonl": '{"type":"assistant"}\n'},
    )

    store.append(task, response, _score())
    record = store.load()[0]
    trace_path = tmp_path / "normalized-trace" / "artifacts" / "t1" / "agent" / "normalized_trace.json"

    assert record["artifact_manifest"]["files"]["normalized_trace"].endswith(
        "t1/agent/normalized_trace.json"
    )
    assert trace_path.exists()

    payload = json.loads(trace_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == TRACE_SCHEMA_VERSION
    assert payload["session_id"] == "session-123"
    assert payload["agent"]["name"] == "opencode"
    assert payload["agent"]["transport"] == "opencode_cli"
    assert payload["steps"][0]["type"] == "message"
    assert payload["reasoning_steps"] == [
        {
            "role": "assistant",
            "type": "reasoning",
            "content": "check",
        }
    ]
    assert payload["refs"]["response_stream"].endswith("t1/workspace/trace.jsonl")
