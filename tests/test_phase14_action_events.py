from __future__ import annotations

import pytest

from alphadiana.analysis.action_events import (
    ACTION_LABEL_CONFIDENCES,
    ACTION_PRIORITY,
    BACKUP_EXCLUDE_PREFIX,
    CANONICAL_ACTIONS,
    DIRECTLLM_CANDIDATES,
    EVENT_SOURCES,
    PRIMARY_CORPORA,
    OBSERVATION_STATUSES,
    TOOL_SUBTYPES,
    ActionEvent,
    classify_action_intent,
    classify_tool_subtype,
    extract_action_event_rows,
    extract_action_events,
    inventory_corpus,
    normalized_records,
    observation_status_from_text,
    select_directllm_baseline,
)
from alphadiana.analysis.result_reader import load_run_bundle


def test_action_event_constants_are_locked():
    assert CANONICAL_ACTIONS == ("plan", "reason", "tool_use", "verify", "recover", "answer")
    assert ACTION_PRIORITY == ("answer", "recover", "verify", "tool_use", "plan", "reason")
    assert ACTION_LABEL_CONFIDENCES == ("strict", "heuristic")
    assert EVENT_SOURCES == ("trajectory", "normalized_trace", "artifact_stream", "raw_text")
    assert TOOL_SUBTYPES == (
        "search",
        "read",
        "write",
        "memory_read",
        "memory_write",
        "shell",
        "python",
        "subagent",
        "browser",
        "other",
    )
    assert OBSERVATION_STATUSES == ("none", "info", "success", "fail", "error", "timeout", "truncated")


def test_action_event_to_row_shape():
    event = ActionEvent(
        run_id="run-a",
        harness="openclaw",
        task_id="gpqa_1",
        sample_index=0,
        step_id="2",
        canonical_action="tool_use",
        action_label_confidence="strict",
        source="trajectory",
        tool_subtype="shell",
        observation_status="none",
        recovery_context=False,
        text_span="python solve.py",
        score_status="valid_scored",
        correct=True,
    )

    assert event.to_row() == {
        "run_id": "run-a",
        "harness": "openclaw",
        "task_id": "gpqa_1",
        "sample_index": 0,
        "step_id": "2",
        "canonical_action": "tool_use",
        "action_label_confidence": "strict",
        "source": "trajectory",
        "tool_subtype": "shell",
        "observation_status": "none",
        "recovery_context": False,
        "text_span": "python solve.py",
        "score_status": "valid_scored",
        "correct": True,
    }


def test_classify_action_intent_priority_order():
    assert classify_action_intent({"content": "Final answer: C", "type": "tool_use"}) == (
        "answer",
        "strict",
        "other",
    )
    assert classify_action_intent(
        {"content": "I should verify this by running a check", "tool_name": "python"},
        previous_observation_status="error",
    ) == ("recover", "heuristic", "python")
    assert classify_action_intent({"content": "verify the answer with a quick check", "type": "tool_use"}) == (
        "verify",
        "heuristic",
        "other",
    )
    assert classify_action_intent({"content": "search web for supporting facts", "tool_name": "search"}) == (
        "tool_use",
        "strict",
        "search",
    )
    assert classify_action_intent({"content": "Plan: first compare the options"}) == ("plan", "heuristic", "other")
    assert classify_action_intent({"content": "The molecule likely forms an intermediate"}) == (
        "reason",
        "heuristic",
        "other",
    )


@pytest.mark.parametrize(
    ("name", "text", "expected"),
    [
        ("web_search", "", "search"),
        ("read_file", "", "read"),
        ("edit", "write the answer file", "write"),
        ("memory", "recall stored note", "memory_read"),
        ("memory", "save this memory", "memory_write"),
        ("bash", "", "shell"),
        ("python", "", "python"),
        ("agent", "delegate to subagent", "subagent"),
        ("browser_open", "", "browser"),
        ("custom", "", "other"),
    ],
)
def test_classify_tool_subtype_mapping(name, text, expected):
    assert classify_tool_subtype(name, text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", "none"),
        ("command timed out", "timeout"),
        ("Traceback: exception raised", "error"),
        ("assertion failed", "fail"),
        ("all tests passed", "success"),
        ("neutral observation text", "info"),
    ],
)
def test_observation_status_from_text_mapping(text, expected):
    assert observation_status_from_text(text) == expected


def _write_task_record(results_dir, run_id, task_id, record):
    tasks_dir = results_dir / run_id / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / f"{task_id}.json").write_text(f"[{record}]", encoding="utf-8")


def _dump_json(value):
    import json

    return json.dumps(value)


def _write_run(results_dir, run_id, *, records=None, manifest=None, task_records=None):
    import json

    run_dir = results_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    records = records or []
    manifest = manifest or {"run_id": run_id}
    (results_dir / f"{run_id}.jsonl").write_text(
        "\n".join(json.dumps(record) for record in records),
        encoding="utf-8",
    )
    (run_dir / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if task_records:
        tasks_dir = run_dir / "tasks"
        tasks_dir.mkdir(exist_ok=True)
        for task_id, samples in task_records.items():
            (tasks_dir / f"{task_id}.json").write_text(json.dumps(samples), encoding="utf-8")


def test_normalized_records_prefer_task_records_and_deduplicate_valid_scored(tmp_path):
    results_dir = tmp_path / "results"
    _write_run(
        results_dir,
        "run-a",
        records=[{"task_id": "jsonl_only", "sample_index": 0, "score_status": "valid_scored", "correct": True}],
        task_records={
            "gpqa_1_attempt_1": [
                {"task_id": "gpqa_1", "sample_index": 0, "score_status": "runtime_error", "correct": None}
            ],
            "gpqa_1_attempt_2": [
                {"task_id": "gpqa_1", "sample_index": 0, "score_status": "valid_scored", "correct": False}
            ],
            "gpqa_2_attempt_1": [
                {"task_id": "gpqa_2", "sample_index": 0, "score_status": "runtime_error", "correct": None}
            ],
            "gpqa_2_attempt_2": [
                {"task_id": "gpqa_2", "sample_index": 0, "score_status": "provider_error", "correct": None}
            ],
        },
    )

    records = normalized_records(load_run_bundle(results_dir, "run-a"))

    assert [(record["task_id"], record["score_status"]) for record in records] == [
        ("gpqa_1", "valid_scored"),
        ("gpqa_2", "provider_error"),
    ]


def test_inventory_corpus_reports_counts_and_excludes_backup_prefix(tmp_path):
    results_dir = tmp_path / "results"
    _write_run(
        results_dir,
        "run-a",
        records=[
            {"task_id": "gpqa_1", "sample_index": 0, "score_status": "runtime_error", "correct": None},
            {"task_id": "gpqa_1", "sample_index": 0, "score_status": "valid_scored", "correct": True},
        ],
        manifest={"run_id": "run-a", "expected_sample_count": 2},
        task_records={
            "gpqa_1_attempt_1": [
                {"task_id": "gpqa_1", "sample_index": 0, "score_status": "runtime_error", "correct": None}
            ],
            "gpqa_1_attempt_2": [
                {"task_id": "gpqa_1", "sample_index": 0, "score_status": "valid_scored", "correct": True}
            ],
        },
    )

    inventory = inventory_corpus(results_dir, "run-a", "opencode")

    assert BACKUP_EXCLUDE_PREFIX == "bkp_gpqa_20260425"
    assert inventory["run_id"] == "run-a"
    assert inventory["harness"] == "opencode"
    assert inventory["jsonl_records"] == 2
    assert inventory["task_record_files"] == 2
    assert inventory["task_records"] == 2
    assert inventory["expected_sample_count"] == 2
    assert inventory["status_counts"] == {"valid_scored": 1}
    assert inventory["duplicate_sample_keys"] == [("gpqa_1", 0)]
    assert "bkp_gpqa_20260425" not in PRIMARY_CORPORA.values()


def test_extract_action_events_reads_normalized_trace_artifact(tmp_path):
    import json

    results_dir = tmp_path / "results"
    run_id = "run-trace"
    trace_path = results_dir / run_id / "artifacts" / "gpqa_1" / "normalized_trace.json"
    trace_path.parent.mkdir(parents=True)
    trace_path.write_text(
        json.dumps(
            {
                "steps": [
                    {"type": "reasoning", "content": "Plan: solve the problem."},
                    {"type": "tool_use", "tool_calls": [{"name": "python"}], "content": "run python check"},
                    {"type": "tool_result", "content": "Traceback exception"},
                    {"type": "message", "content": "Retry with a simpler calculation"},
                    {"type": "message", "content": "Final answer: B"},
                ]
            }
        ),
        encoding="utf-8",
    )
    _write_run(
        results_dir,
        run_id,
        task_records={
            "gpqa_1": [
                {
                    "run_id": run_id,
                    "task_id": "gpqa_1",
                    "sample_index": 0,
                    "score_status": "valid_scored",
                    "correct": True,
                    "artifact_manifest": {
                        "files": {"normalized_trace": f"{run_id}/artifacts/gpqa_1/normalized_trace.json"}
                    },
                }
            ]
        },
    )

    events = extract_action_events(load_run_bundle(results_dir, run_id), harness="openclaw")

    assert [(event.canonical_action, event.source, event.tool_subtype) for event in events] == [
        ("plan", "normalized_trace", "other"),
        ("verify", "normalized_trace", "python"),
        ("recover", "normalized_trace", "other"),
        ("answer", "normalized_trace", "other"),
    ]
    assert events[2].recovery_context is True
    assert events[2].observation_status == "none"


def test_extract_action_events_uses_raw_text_fallback_for_directllm(tmp_path):
    results_dir = tmp_path / "results"
    run_id = "direct-run"
    _write_run(
        results_dir,
        run_id,
        task_records={
            "gpqa_1": [
                {
                    "run_id": run_id,
                    "task_id": "gpqa_1",
                    "sample_index": 0,
                    "score_status": "valid_scored",
                    "correct": False,
                    "raw_output": "We compare the options carefully.",
                    "predicted": "D",
                }
            ]
        },
    )

    rows = extract_action_event_rows(load_run_bundle(results_dir, run_id), harness="directllm")

    assert [row["canonical_action"] for row in rows] == ["reason", "answer"]
    assert {row["tool_subtype"] for row in rows} == {"other"}
    assert all(row["canonical_action"] not in {"tool_use", "verify", "recover"} for row in rows)


def test_extract_action_events_filters_opencode_lifecycle_metadata(tmp_path):
    results_dir = tmp_path / "results"
    run_id = "opencode-run"
    _write_run(
        results_dir,
        run_id,
        task_records={
            "gpqa_1": [
                {
                    "run_id": run_id,
                    "task_id": "gpqa_1",
                    "sample_index": 0,
                    "score_status": "valid_scored",
                    "correct": True,
                    "trajectory": [
                        {"type": "metadata", "content": "step-start tool call"},
                        {"type": "tool_use", "tool_name": "read_file", "content": "read reference file"},
                        {"type": "metadata", "content": "step-finish tool call"},
                        {"type": "message", "content": "Final answer: A"},
                    ],
                }
            ]
        },
    )

    events = extract_action_events(load_run_bundle(results_dir, run_id), harness="opencode")

    assert [event.text_span for event in events] == ["read reference file", "Final answer: A"]
    assert [event.canonical_action for event in events] == ["tool_use", "answer"]


def test_select_directllm_baseline_prefers_complete_valid_scored_candidate(tmp_path):
    results_dir = tmp_path / "results"
    absolute_candidate = tmp_path / "snapshot" / "pilot_20260418_qwen35_27b_gpqa_diamond_directllm_t3"
    _write_run(
        results_dir,
        "phase9_directllm_gpqa_diamond_qwen35_27b_logprobs",
        records=[{"task_id": "gpqa_1", "sample_index": 0, "score_status": "valid_scored", "correct": True}],
    )
    _write_run(
        results_dir,
        "hf-alphadiana-benchmark-results/pilot_run/pilot_20260418_qwen35_27b_gpqa_diamond_directllm_t3",
        records=[
            {"task_id": "gpqa_1", "sample_index": 0, "score_status": "runtime_error", "correct": None},
            {"task_id": "gpqa_2", "sample_index": 0, "score_status": "runtime_error", "correct": None},
        ],
    )
    _write_run(
        absolute_candidate.parent,
        absolute_candidate.name,
        records=[
            {"task_id": "gpqa_1", "sample_index": 0, "score_status": "valid_scored", "correct": True},
            {"task_id": "gpqa_2", "sample_index": 0, "score_status": "valid_scored", "correct": False},
        ],
    )

    selected = select_directllm_baseline(
        results_dir,
        candidates=(
            DIRECTLLM_CANDIDATES[0],
            DIRECTLLM_CANDIDATES[1],
            str(absolute_candidate),
        ),
    )

    assert DIRECTLLM_CANDIDATES[2].startswith("/data0/models/hub/")
    assert selected["run_id"] == absolute_candidate.name
    assert selected["candidate"] == str(absolute_candidate)
    assert selected["valid_scored"] == 2
