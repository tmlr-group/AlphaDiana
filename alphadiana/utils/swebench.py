"""Helpers for normalizing SWE-bench task metadata."""

from __future__ import annotations

import json
from typing import Any

from alphadiana.benchmark.base import BenchmarkTask


def json_field_to_string(value: Any, *, default: str = "[]") -> str:
    """Return a stable JSON-string representation expected by swebench."""
    if value is None:
        return default
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or default
    return json.dumps(value)


def infer_instance_id(task: BenchmarkTask) -> str:
    """Infer the SWE-bench instance id from task metadata."""
    metadata = getattr(task, "metadata", None) or {}
    raw = metadata.get("instance_id")
    if raw:
        return str(raw)
    if task.task_id.startswith("swe_"):
        return task.task_id[len("swe_") :]
    return task.task_id


def build_swebench_instance(task: BenchmarkTask) -> dict[str, Any]:
    """Reconstruct a SWE-bench instance dict from a benchmark task."""
    metadata = getattr(task, "metadata", None) or {}
    return {
        "instance_id": infer_instance_id(task),
        "repo": str(metadata.get("repo", "")),
        "base_commit": str(metadata.get("base_commit", "")),
        "patch": str(task.ground_truth or ""),
        "test_patch": str(metadata.get("test_patch", "")),
        "problem_statement": task.problem,
        "hints_text": str(metadata.get("hints_text", "")),
        "created_at": str(metadata.get("created_at", "")),
        "version": str(metadata.get("version", "")),
        "FAIL_TO_PASS": json_field_to_string(metadata.get("FAIL_TO_PASS")),
        "PASS_TO_PASS": json_field_to_string(metadata.get("PASS_TO_PASS")),
        "environment_setup_commit": str(metadata.get("environment_setup_commit", "")),
    }
