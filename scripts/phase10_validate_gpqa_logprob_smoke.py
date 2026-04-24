#!/usr/bin/env python3
"""Validate Phase 10 GPQA-Diamond logprob smoke artifacts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


EXPECTED_TOP_LOGPROBS = 20
EXPECTED_INT16_PROBABILITY_SCALE = 32767


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


def validate_run_dir(run_dir: str | Path) -> dict[str, Any]:
    """Validate the first task record and return a compact inspection summary."""
    run_path = Path(run_dir)
    record = _load_first_task_record(run_path)

    token_entropy_stats = record.get("token_entropy_stats")
    if not isinstance(token_entropy_stats, dict):
        _fail("token_entropy_stats must be present")
    n_tokens = token_entropy_stats.get("n_tokens")
    if not isinstance(n_tokens, int | float) or n_tokens <= 0:
        _fail("token_entropy_stats.n_tokens must be > 0")

    logprobs_path = _require_non_empty_string(record, "logprobs_path")
    logprobs_int16_path = _require_non_empty_string(record, "logprobs_int16_path")

    if record.get("top_logprobs") != EXPECTED_TOP_LOGPROBS:
        _fail(f"top_logprobs must be {EXPECTED_TOP_LOGPROBS}")
    if record.get("int16_probability_scale") != EXPECTED_INT16_PROBABILITY_SCALE:
        _fail(
            "int16_probability_scale must be "
            f"{EXPECTED_INT16_PROBABILITY_SCALE}"
        )

    files = record.get("artifact_manifest", {}).get("files", {})
    if not isinstance(files, dict):
        _fail("artifact_manifest.files must be present")
    if not files.get("logprobs_float"):
        _fail("artifact_manifest.files.logprobs_float must be present")
    if not files.get("logprobs_int16"):
        _fail("artifact_manifest.files.logprobs_int16 must be present")

    _require_sidecar(run_path, logprobs_path)
    _require_sidecar(run_path, logprobs_int16_path)

    return {
        "task_id": record.get("task_id", ""),
        "n_tokens": n_tokens,
        "logprobs_path": logprobs_path,
        "logprobs_int16_path": logprobs_int16_path,
        "top_logprobs": record.get("top_logprobs"),
        "int16_probability_scale": record.get("int16_probability_scale"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args(argv)

    summary = validate_run_dir(args.run_dir)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
