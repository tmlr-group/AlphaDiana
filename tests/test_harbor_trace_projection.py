"""Native roles and tool evidence survive persistence and both analysis paths."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from alphadiana.analysis.action_annotations import ActionAnnotations
from alphadiana.analysis.analyzer import ExecutionMetrics
from alphadiana.analysis.io.result_store import ResultStore
from alphadiana.artifacts import Artifacts
from alphadiana.benchmarks.base import BenchmarkTask
from alphadiana.benchmarks.terminal_bench2.harness.terminus import project_harbor_steps
from alphadiana.harness.base import AgentResponse
from alphadiana.scorer.base import ScoreResult


def test_native_projection_excludes_prompts_and_observations_from_actions(
    tmp_path, monkeypatch
):
    native = [
        {"source": "user", "message": "USER INSTRUCTION: verify and answer"},
        {"source": "system", "message": "SYSTEM HANDOFF"},
        {
            "source": "agent",
            "message": "Inspect the directory",
            "tool_calls": [
                {
                    "tool_call_id": "call_1",
                    "function_name": "bash_command",
                    "arguments": {"keystrokes": "ls\n", "duration": 0.1},
                }
            ],
            "observation": {
                "results": [
                    {"source_call_id": "call_1", "content": "TOOL OUTPUT: file.txt"}
                ]
            },
        },
        {"source": "agent", "message": "Final answer: done", "tool_calls": None},
    ]
    original = deepcopy(native)
    response = AgentResponse(
        "0",
        trajectory=project_harbor_steps(native),
        workspace_file_contents={"harbor_trajectory.json": json.dumps(native)},
    )
    assert native == original
    store = ResultStore(
        str(tmp_path), "native", run_metadata={"agent_name": "terminus2"}
    )
    store.append(
        BenchmarkTask("task", "fixture", "1"), response, ScoreResult(False, 0, "1", "0")
    )
    artifacts = Artifacts(Path(store.path))
    record = artifacts.records()[0]
    steps = json.loads(artifacts.file(record, "normalized_trace").read_text())["steps"]
    assert [step["role"] for step in steps] == [
        "user",
        "system",
        "assistant",
        "tool",
        "assistant",
    ]
    assert steps[2]["tool_calls"] == [
        {
            "id": "call_1",
            "tool": "bash_command",
            "input": {"keystrokes": "ls\n", "duration": 0.1},
        }
    ]
    assert steps[3]["tool_results"][0]["tool_use_id"] == "call_1"
    metrics = ExecutionMetrics().measure([artifacts])
    actions = metrics.tables["actions"]
    assert len(actions) == 2
    assert actions[0]["canonical_action"] == "tool_use"
    assert all(
        "USER INSTRUCTION" not in event["text_span"]
        and "TOOL OUTPUT" not in event["text_span"]
        for event in actions
    )
    classifier = ActionAnnotations(
        model="fixture",
        model_version="fixture-1",
        cache_dir=tmp_path / "cache",
        base_url="http://localhost:1/v1",
    )
    targets = []

    def classify(evidence):
        targets.append(evidence["target"])
        return "Planning", "fixture-digest"

    monkeypatch.setattr(classifier, "_classify", classify)
    try:
        classifier.measure([artifacts])
    finally:
        classifier.client.close()
    assert targets == ["Inspect the directory", "Final answer: done"]


def test_harbor_unknown_role_is_not_silently_an_assistant():
    with pytest.raises(ValueError, match="Unsupported Harbor"):
        project_harbor_steps([{"source": "new_native_role", "message": "unknown"}])


def test_harbor_handoff_reference_is_retained_only_in_native_evidence():
    steps = project_harbor_steps(
        [
            {
                "source": "system",
                "message": "Handoff",
                "observation": {"results": [{"subagent_trajectory_ref": []}]},
            }
        ]
    )
    assert steps == [{"role": "system", "content": "Handoff"}]
