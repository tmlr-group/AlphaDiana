"""Phase 10 GPQA logprob smoke validator tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.phase10_validate_gpqa_logprob_smoke import validate_run_dir


RUN_ID = "phase10_directllm_gpqa_diamond_qwen35_27b_int16_logprobs_smoke"


def _write_complete_smoke_artifacts(tmp_path: Path) -> Path:
    run = tmp_path / RUN_ID
    (run / "tasks").mkdir(parents=True)
    (run / "logprobs").mkdir()
    (run / "logprobs_int16").mkdir()
    (run / "logprobs" / "gpqa_0.jsonl").write_text('{"token":"A"}\n', encoding="utf-8")
    (run / "logprobs_int16" / "gpqa_0.jsonl").write_text('{"token":"A"}\n', encoding="utf-8")
    record = {
        "task_id": "gpqa_0",
        "token_entropy_stats": {"n_tokens": 1},
        "logprobs_path": f"{RUN_ID}/logprobs/gpqa_0.jsonl",
        "logprobs_int16_path": f"{RUN_ID}/logprobs_int16/gpqa_0.jsonl",
        "top_logprobs": 20,
        "int16_probability_scale": 32767,
        "artifact_manifest": {
            "files": {
                "logprobs_float": "logprobs/gpqa_0.jsonl",
                "logprobs_int16": "logprobs_int16/gpqa_0.jsonl",
            },
        },
    }
    (run / "tasks" / "gpqa_0.json").write_text(
        json.dumps([record]),
        encoding="utf-8",
    )
    return run


def test_validator_accepts_complete_smoke_artifacts(tmp_path: Path) -> None:
    run = _write_complete_smoke_artifacts(tmp_path)

    summary = validate_run_dir(run)

    assert summary["task_id"] == "gpqa_0"
    assert summary["n_tokens"] == 1
    assert summary["top_logprobs"] == 20
    assert summary["int16_probability_scale"] == 32767


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("token_entropy_stats", {"n_tokens": 0}, "token_entropy_stats.n_tokens"),
        ("logprobs_path", "", "logprobs_path"),
        ("logprobs_int16_path", "", "logprobs_int16_path"),
        ("top_logprobs", 5, "top_logprobs"),
        ("int16_probability_scale", 1, "int16_probability_scale"),
    ],
)
def test_validator_rejects_invalid_task_fields(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    run = _write_complete_smoke_artifacts(tmp_path)
    task_path = run / "tasks" / "gpqa_0.json"
    records = json.loads(task_path.read_text(encoding="utf-8"))
    records[0][field] = value
    task_path.write_text(json.dumps(records), encoding="utf-8")

    with pytest.raises(SystemExit, match=message):
        validate_run_dir(run)


def test_validator_rejects_missing_manifest_alias(tmp_path: Path) -> None:
    run = _write_complete_smoke_artifacts(tmp_path)
    task_path = run / "tasks" / "gpqa_0.json"
    records = json.loads(task_path.read_text(encoding="utf-8"))
    records[0]["artifact_manifest"]["files"].pop("logprobs_int16")
    task_path.write_text(json.dumps(records), encoding="utf-8")

    with pytest.raises(SystemExit, match="artifact_manifest.files.logprobs_int16"):
        validate_run_dir(run)


def test_validator_rejects_missing_sidecar_file(tmp_path: Path) -> None:
    run = _write_complete_smoke_artifacts(tmp_path)
    (run / "logprobs_int16" / "gpqa_0.jsonl").unlink()

    with pytest.raises(SystemExit, match="missing sidecar"):
        validate_run_dir(run)


def test_validator_rejects_run_without_task_json(tmp_path: Path) -> None:
    run = tmp_path / RUN_ID
    (run / "tasks").mkdir(parents=True)

    with pytest.raises(SystemExit, match="no task JSON"):
        validate_run_dir(run)
