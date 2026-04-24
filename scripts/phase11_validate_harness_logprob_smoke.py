#!/usr/bin/env python3
"""Validate Phase 11 harness GPQA-Diamond logprob smoke artifacts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


EXPECTED_TOP_LOGPROBS = 20
EXPECTED_INT16_PROBABILITY_SCALE = 32767
ALLOWED_HARNESSES = {"openclaw", "opencode", "zeroclaw"}
SECRET_MARKERS = ("OPENAI_API_KEY=", "OPENROUTER_API_KEY=", "ZEROCLAW_API_KEY=")


def _fail(message: str) -> None:
    raise SystemExit(message)


def _load_first_task_record(run_dir: Path) -> dict[str, Any]:
    task_paths = sorted((run_dir / "tasks").glob("*.json"))
    if not task_paths:
        _fail(f"no task JSON found under {run_dir / 'tasks'}")
    try:
        payload = json.loads(task_paths[0].read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _fail(f"failed to parse task JSON {task_paths[0]}: {exc}")
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        return payload[0]
    if isinstance(payload, dict):
        return payload
    _fail(f"task JSON {task_paths[0]} does not contain a task record")


def _require_non_empty_string(record: dict[str, Any], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value:
        _fail(f"{field} must be a non-empty string")
    return value


def _require_sidecar(run_dir: Path, rel_path: str) -> None:
    path = run_dir.parent / rel_path
    if not path.exists():
        _fail(f"missing sidecar file: {path}")
    if not path.is_file():
        _fail(f"sidecar path is not a file: {path}")


def _scan_secret_markers(obj: Any) -> bool:
    if isinstance(obj, dict):
        return any(_scan_secret_markers(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_scan_secret_markers(v) for v in obj)
    if isinstance(obj, str):
        return any(marker in obj for marker in SECRET_MARKERS)
    return False


def validate_run_dir(run_dir: str | Path, harness: str) -> dict[str, Any]:
    run_path = Path(run_dir)
    harness_name = str(harness).strip().lower()
    if harness_name not in ALLOWED_HARNESSES:
        _fail(f"--harness must be one of {sorted(ALLOWED_HARNESSES)}")

    record = _load_first_task_record(run_path)
    if record.get("score_status") != "valid_scored":
        _fail("score_status must be valid_scored")

    token_entropy_stats = record.get("token_entropy_stats")
    if not isinstance(token_entropy_stats, dict):
        _fail("token_entropy_stats must be present")
    n_tokens = token_entropy_stats.get("n_tokens")
    if not isinstance(n_tokens, (int, float)) or n_tokens <= 0:
        _fail("token_entropy_stats.n_tokens must be > 0")

    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        _fail("metadata must be present")
    if metadata.get("logprobs_capture_status") != "captured":
        _fail("metadata.logprobs_capture_status must be captured")
    if metadata.get("logprob_capture_harness") != harness_name:
        _fail(f"metadata.logprob_capture_harness must be {harness_name}")

    logprobs_path = _require_non_empty_string(record, "logprobs_path")
    logprobs_int16_path = _require_non_empty_string(record, "logprobs_int16_path")
    if record.get("top_logprobs") != EXPECTED_TOP_LOGPROBS:
        _fail(f"top_logprobs must be {EXPECTED_TOP_LOGPROBS}")
    if record.get("int16_probability_scale") != EXPECTED_INT16_PROBABILITY_SCALE:
        _fail(f"int16_probability_scale must be {EXPECTED_INT16_PROBABILITY_SCALE}")

    files = record.get("artifact_manifest", {}).get("files", {})
    if not isinstance(files, dict):
        _fail("artifact_manifest.files must be present")
    if not files.get("logprobs_float"):
        _fail("artifact_manifest.files.logprobs_float must be present")
    if not files.get("logprobs_int16"):
        _fail("artifact_manifest.files.logprobs_int16 must be present")

    _require_sidecar(run_path, logprobs_path)
    _require_sidecar(run_path, logprobs_int16_path)

    sandbox_metadata = record.get("sandbox_metadata", {})
    if _scan_secret_markers(sandbox_metadata):
        _fail("sandbox_metadata contains secret markers")

    return {
        "task_id": record.get("task_id", ""),
        "harness": harness_name,
        "n_tokens": n_tokens,
        "logprobs_path": logprobs_path,
        "logprobs_int16_path": logprobs_int16_path,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--harness", required=True)
    args = parser.parse_args(argv)
    summary = validate_run_dir(args.run_dir, args.harness)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
