"""Analysis reads canonical sidecars and excludes prompts from agent events."""

import json

import pytest

from alphadiana.analysis.action_events import extract_action_event_rows
from alphadiana.analysis.result_reader import RunBundle, resolve_run_relative_path


def test_trace_survives_rescore_in_another_directory(tmp_path):
    original = tmp_path / "original/artifacts"
    trace = original / "task/agent/trace.json"
    trace.parent.mkdir(parents=True)
    trace.write_text(
        json.dumps(
            {
                "steps": [
                    {"role": "system", "content": "Plan carefully"},
                    {"role": "user", "content": "Verify this problem"},
                    {"role": "assistant", "type": "answer", "content": "2"},
                ]
            }
        )
    )
    record = {
        "task_id": "task",
        "sample_index": 0,
        "artifact_origin": str(original),
        "artifact_manifest": {"files": {"normalized_trace": "task/agent/trace.json"}},
    }
    target = tmp_path / "derived"
    bundle = RunBundle(
        target,
        "rescore",
        target / "rescore.jsonl",
        target / "rescore",
        {},
        [record],
        {},
    )
    rows = extract_action_event_rows(bundle, harness="custom")
    assert len(rows) == 1
    assert rows[0]["text_span"] == "2"
    assert rows[0]["source"] == "normalized_trace"


@pytest.mark.parametrize("reference", ["../outside", "/tmp/outside"])
def test_artifact_references_cannot_escape_root(tmp_path, reference):
    with pytest.raises(ValueError):
        resolve_run_relative_path(tmp_path, reference)
