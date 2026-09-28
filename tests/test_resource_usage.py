import json

import pytest

from alphadiana.analysis.resource_usage import MeasuredUsage, measured_usage
from alphadiana.artifacts import Artifacts


def session(tmp_path):
    record = {
        "task_id": "test",
        "problem": "solve task",
        "token_usage": {},
        "artifact_manifest": {"files": {"openclaw_session": "selected.jsonl"}},
    }
    artifacts_path = tmp_path / "run.jsonl"
    artifacts_path.write_text(json.dumps(record) + "\n")
    source = Artifacts(artifacts_path)
    root = source.artifact_root(record)
    root.mkdir(parents=True)
    rows = [
        {"type": "session", "version": 3},
        {"id": "user", "message": {"role": "user", "content": "Please solve task"}},
        {
            "id": "a",
            "message": {
                "role": "assistant",
                "usage": {
                    "input": 10,
                    "output": 4,
                    "cacheRead": 3,
                    "cacheWrite": 2,
                    "totalTokens": 19,
                },
            },
        },
        {
            "id": "b",
            "message": {
                "role": "assistant",
                "usage": {
                    "input": 20,
                    "output": 5,
                    "cacheRead": 0,
                    "cacheWrite": 0,
                    "totalTokens": 25,
                },
            },
        },
    ]
    path = root / "selected.jsonl"
    path.write_text("\n".join(map(json.dumps, rows)))
    return source, record, rows, path


def test_selected_session_excludes_unrelated_runtime_requests(tmp_path):
    source, record, _, path = session(tmp_path)
    (path.parent / "healthcheck.jsonl").write_text("not a task session")
    result = measured_usage(source, record)
    assert result.output_tokens == 9
    assert result.provenance["native_totals"]["totalTokens"] == 44
    assert result.provenance["assistant_messages"] == 2
    assert result.provenance["task_identity_verified"]
    assert len(result.provenance["sha256"]) == 64


@pytest.mark.parametrize(
    "fault,missing",
    [
        ("identity", "task_identity_unverified"),
        ("usage", "incomplete_request_usage"),
        ("schema", "unsupported_session_schema"),
    ],
)
def test_incomplete_or_unverified_session_does_not_become_partial_total(
    tmp_path, fault, missing
):
    source, record, rows, path = session(tmp_path)
    if fault == "identity":
        record["problem"] = "another task"
    elif fault == "usage":
        del rows[-1]["message"]["usage"]["output"]
    else:
        rows[0]["version"] = 999
    path.write_text("\n".join(map(json.dumps, rows)))
    result = measured_usage(source, record)
    assert result.output_tokens is None
    assert result.provenance["missing"] == missing


@pytest.mark.parametrize("fault", ["duplicate", "total"])
def test_inconsistent_session_fails(tmp_path, fault):
    source, record, rows, path = session(tmp_path)
    if fault == "duplicate":
        rows[-1]["id"] = rows[-2]["id"]
    else:
        rows[-1]["message"]["usage"]["totalTokens"] = 99
    path.write_text("\n".join(map(json.dumps, rows)))
    with pytest.raises(ValueError):
        measured_usage(source, record)


def test_persisted_usage_and_custom_extractors_remain_supported(tmp_path):
    source, record, _, _ = session(tmp_path)
    record["token_usage"] = {"completion_tokens": 0}
    assert measured_usage(source, record).output_tokens == 0
    record["token_usage"] = {}
    result = measured_usage(
        source, record, [lambda a, r: MeasuredUsage(12, {"source": "custom"})]
    )
    assert result.output_tokens == 12
    assert result.provenance["source"] == "custom"
    assert measured_usage(source, record, []).output_tokens is None
