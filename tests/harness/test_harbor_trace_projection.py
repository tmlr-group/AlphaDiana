"""Native roles and tool evidence survive persistence and both analysis paths."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from tests.analysis.action_target_fixtures import attach_targets, fixed_item
from alphadiana.analysis.actions.llm_annotations import ActionAnnotations
from alphadiana.analysis.measurements import ExecutionMetrics
from alphadiana.results.store import ResultStore
from alphadiana.results.artifacts import Artifacts
from alphadiana.benchmarks.base import BenchmarkTask
from alphadiana.harness.sweagent import project_sweagent_history
from alphadiana.harness.terminus2.agent import project_harbor_steps
from alphadiana.harness.base import AgentResponse
from alphadiana.scorer.base import ScoreResult
from tests.analysis.test_general_analysis import judge_options, response as judge_response


def llm_actions(artifacts, tmp_path):
    with ActionAnnotations(**judge_options(tmp_path)) as classifier:

        def create(**kw):
            blocks = json.loads(kw["messages"][1]["content"].split("\n", 1)[1])["sources"]
            return judge_response(
                {
                    "actions": [
                        {
                            "source_id": b["source_id"],
                            "start": 0,
                            "end": b["length"],
                            "label": (
                                "Tool Use" if b["kind"] == "tool_call" else "Reasoning"
                            ),
                        }
                        for b in blocks
                        if b["target"]
                    ]
                }
            )

        classifier.client.chat.completions.create = create
        return classifier.measure([artifacts]).tables["annotations"]


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
    assert "actions" not in ExecutionMetrics().measure([artifacts]).tables
    actions = llm_actions(artifacts, tmp_path)
    assert len(actions) == 3
    assert actions[1]["action"] == "Tool Use"
    assert all(
        "USER INSTRUCTION" not in event["text"] and "TOOL OUTPUT" not in event["text"]
        for event in actions
    )
    # Boundaries are explicitly supplied, not invented by the classifier.
    attach_targets(
        artifacts,
        [
            fixed_item("call", json.dumps(steps[2], sort_keys=True), 0),
            fixed_item("answer", "Final answer: done", 1),
        ],
    )
    classifier = ActionAnnotations(
        protocol="human-protocol-v2",
        model="fixture",
        model_version="fixture-1",
        cache_dir=tmp_path / "cache",
        base_url="http://localhost:1/v1",
        api_key="fixture-key",
    )
    targets = []

    def classify(evidence):
        targets.append(evidence["actions"][0]["text"])
        return "Planning", "fixture-digest"

    monkeypatch.setattr(classifier, "_classify", classify)
    try:
        classifier.measure([artifacts])
    finally:
        classifier.client.close()
    assert targets == [json.dumps(steps[2], sort_keys=True), "Final answer: done"]
    assert '"tool_calls"' in targets[0]


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


def test_sweagent_uses_history_not_queries_or_observations_as_actions(tmp_path):
    history = [
        {"role": "system", "content": "SYSTEM PROMPT"},
        {"role": "user", "content": [{"type": "text", "text": "USER INSTRUCTION"}]},
        {
            "role": "assistant",
            "content": "Inspect files",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "bash", "arguments": '{"command":"ls"}'},
                }
            ],
        },
        {"role": "tool", "content": "TOOL OUTPUT", "tool_call_ids": ["call_1"]},
        {"role": "assistant", "content": "Final answer: done"},
    ]
    original = deepcopy(history)
    store = ResultStore(str(tmp_path), "swe", run_metadata={"agent_name": "sweagent"})
    store.append(
        BenchmarkTask("task", "fixture", "1"),
        AgentResponse("patch", trajectory=project_sweagent_history(history)),
        ScoreResult(False, 0, "1", "patch"),
    )
    assert history == original
    artifacts = Artifacts(Path(store.path))
    record = artifacts.records()[0]
    steps = json.loads(artifacts.file(record, "normalized_trace").read_text())["steps"]
    assert [s["role"] for s in steps] == [
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert steps[2]["tool_calls"][0]["input"] == {"command": "ls"}
    assert steps[3]["tool_results"][0]["tool_use_id"] == "call_1"
    events = llm_actions(artifacts, tmp_path)
    assert len(events) == 3 and events[1]["action"] == "Tool Use"
    assert all(
        "USER INSTRUCTION" not in r["text"] and "TOOL OUTPUT" not in r["text"]
        for r in events
    )


def test_sweagent_malformed_native_call_is_preserved_and_unknown_roles_fail():
    steps = project_sweagent_history(
        [
            {
                "role": "assistant",
                "tool_calls": [{"function": {"name": "bash", "arguments": "not JSON"}}],
            }
        ]
    )
    assert steps[0]["tool_calls"][0]["input"] == {"unparsed_arguments": "not JSON"}
    with pytest.raises(ValueError, match="Unsupported SWE-Agent"):
        project_sweagent_history([{"role": "new_role"}])
