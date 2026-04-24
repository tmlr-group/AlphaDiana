from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.phase11_validate_harness_logprob_smoke import validate_run_dir


RUN_ID = "phase11_openclaw_gpqa_diamond_qwen35_27b_logprobs_smoke"


def _write_complete_run(tmp_path: Path, *, harness: str = "openclaw") -> Path:
    run = tmp_path / RUN_ID
    (run / "tasks").mkdir(parents=True)
    (run / "logprobs").mkdir()
    (run / "logprobs_int16").mkdir()
    (run / "logprobs" / "gpqa_0.jsonl").write_text('{"token":"A"}\n', encoding="utf-8")
    (run / "logprobs_int16" / "gpqa_0.jsonl").write_text('{"token":"A"}\n', encoding="utf-8")
    record = {
        "task_id": "gpqa_0",
        "score": 1.0,
        "score_status": "valid_scored",
        "token_entropy_stats": {"n_tokens": 1},
        "metadata": {
            "logprobs_capture_status": "captured",
            "logprob_capture_harness": harness,
        },
        "logprobs_path": f"{RUN_ID}/logprobs/gpqa_0.jsonl",
        "logprobs_int16_path": f"{RUN_ID}/logprobs_int16/gpqa_0.jsonl",
        "top_logprobs": 20,
        "int16_probability_scale": 32767,
        "artifact_manifest": {
            "files": {
                "logprobs_float": "logprobs/gpqa_0.jsonl",
                "logprobs_int16": "logprobs_int16/gpqa_0.jsonl",
            }
        },
        "sandbox_metadata": {"command_history": [{"command": "echo safe"}]},
    }
    (run / "tasks" / "gpqa_0.json").write_text(json.dumps([record]), encoding="utf-8")
    return run


def test_validator_accepts_complete_harness_record(tmp_path: Path) -> None:
    run = _write_complete_run(tmp_path, harness="openclaw")
    summary = validate_run_dir(run, "openclaw")
    assert summary["task_id"] == "gpqa_0"
    assert summary["harness"] == "openclaw"
    assert summary["n_tokens"] == 1


def test_validator_rejects_wrong_harness(tmp_path: Path) -> None:
    run = _write_complete_run(tmp_path, harness="openclaw")
    with pytest.raises(SystemExit, match="logprob_capture_harness"):
        validate_run_dir(run, "opencode")


def test_validator_rejects_secret_leakage(tmp_path: Path) -> None:
    run = _write_complete_run(tmp_path, harness="openclaw")
    task_path = run / "tasks" / "gpqa_0.json"
    payload = json.loads(task_path.read_text(encoding="utf-8"))
    payload[0]["sandbox_metadata"] = {"command_history": [{"command": "OPENAI_API_KEY=sk-xxx cmd"}]}
    task_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SystemExit, match="secret markers"):
        validate_run_dir(run, "openclaw")


def test_validator_rejects_invalid_score_status(tmp_path: Path) -> None:
    run = _write_complete_run(tmp_path, harness="openclaw")
    task_path = run / "tasks" / "gpqa_0.json"
    payload = json.loads(task_path.read_text(encoding="utf-8"))
    payload[0]["score_status"] = "invalid"
    task_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SystemExit, match="score_status"):
        validate_run_dir(run, "openclaw")


def test_validator_rejects_missing_manifest_alias(tmp_path: Path) -> None:
    run = _write_complete_run(tmp_path, harness="openclaw")
    task_path = run / "tasks" / "gpqa_0.json"
    payload = json.loads(task_path.read_text(encoding="utf-8"))
    payload[0]["artifact_manifest"]["files"].pop("logprobs_int16")
    task_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SystemExit, match="artifact_manifest.files.logprobs_int16"):
        validate_run_dir(run, "openclaw")


def test_validator_rejects_missing_sidecar(tmp_path: Path) -> None:
    run = _write_complete_run(tmp_path, harness="openclaw")
    (run / "logprobs_int16" / "gpqa_0.jsonl").unlink()
    with pytest.raises(SystemExit, match="missing sidecar file"):
        validate_run_dir(run, "openclaw")
