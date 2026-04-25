"""Reliability summaries for persisted ResultStore records."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from alphadiana.results.status import VALID_SCORE_STATUS, infer_score_status

ERROR_STATUSES = {"agent_error", "provider_error", "runtime_error", "scorer_error"}


def _num_samples(records: list[dict[str, Any]], manifest: dict[str, Any]) -> int:
    if manifest.get("num_samples"):
        return int(manifest["num_samples"])
    by_task: dict[str, int] = defaultdict(int)
    for record in records:
        task_id = str(record.get("task_id") or "")
        if task_id:
            by_task[task_id] += 1
    return max(by_task.values(), default=1)


def _expected_task_ids(records: list[dict[str, Any]], manifest: dict[str, Any]) -> list[str]:
    raw_task_ids = manifest.get("expected_task_ids")
    if isinstance(raw_task_ids, list) and raw_task_ids:
        task_ids = [str(task_id) for task_id in raw_task_ids]
    else:
        task_ids = list(dict.fromkeys(str(record.get("task_id")) for record in records if record.get("task_id")))

    expected_task_count = int(manifest.get("expected_task_count") or len(task_ids))
    if expected_task_count > len(task_ids):
        task_ids.extend(f"__missing_{idx}" for idx in range(expected_task_count - len(task_ids)))
    return task_ids


def _expected_sample_count(
    records: list[dict[str, Any]],
    manifest: dict[str, Any],
    task_ids: list[str],
    num_samples: int,
) -> int:
    if manifest.get("expected_sample_count") is not None:
        return int(manifest["expected_sample_count"])
    if task_ids and num_samples:
        return len(task_ids) * num_samples
    return len(records)


def compute_reliability_summary(records: list[dict[str, Any]], manifest: dict[str, Any]) -> dict[str, Any]:
    """Compute score-status-aware reliability metrics for persisted records."""
    num_samples = _num_samples(records, manifest)
    task_ids = _expected_task_ids(records, manifest)
    expected_sample_count = _expected_sample_count(records, manifest, task_ids, num_samples)
    status_by_record = [infer_score_status(record) for record in records]
    valid_records = [
        record
        for record, status in zip(records, status_by_record)
        if status == VALID_SCORE_STATUS
    ]
    correct_valid_records = [record for record in valid_records if record.get("correct") is True]
    error_records = sum(1 for status in status_by_record if status in ERROR_STATUSES)

    observed_sample_keys = {
        (str(record.get("task_id")), int(record.get("sample_index") or 0))
        for record in records
        if record.get("task_id")
    }
    expected_sample_keys = {
        (task_id, sample_index)
        for task_id in task_ids
        for sample_index in range(num_samples)
    }
    missing_samples = (
        len(expected_sample_keys - observed_sample_keys)
        if expected_sample_keys
        else max(expected_sample_count - len(records), 0)
    )

    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    status_by_key: dict[tuple[str, int], str] = {}
    for record, status in zip(records, status_by_record):
        task_id = str(record.get("task_id") or "")
        if not task_id:
            continue
        sample_index = int(record.get("sample_index") or 0)
        by_task[task_id].append(record)
        status_by_key[(task_id, sample_index)] = status

    task_ids_for_summary = task_ids or list(by_task)
    expected_task_count = len(task_ids_for_summary)
    tasks_passed = 0
    task_avg_scores: list[float] = []
    task_power_scores: list[float] = []
    for task_id in task_ids_for_summary:
        samples = by_task.get(task_id, [])
        correct_valid_samples = sum(
            1
            for sample in samples
            if status_by_key[(task_id, int(sample.get("sample_index") or 0))] == VALID_SCORE_STATUS
            and sample.get("correct") is True
        )
        if correct_valid_samples:
            tasks_passed += 1
        sample_rate = correct_valid_samples / num_samples if num_samples > 0 else 0.0
        task_avg_scores.append(sample_rate)
        task_power_scores.append(sample_rate**num_samples if num_samples > 0 else 0.0)

    pass_power_k_available = num_samples > 1 and all(
        len(by_task.get(task_id, [])) >= num_samples
        for task_id in task_ids_for_summary
    )
    pass_power_k = (
        sum(task_power_scores) / len(task_power_scores)
        if pass_power_k_available and task_power_scores
        else None
    )

    return {
        "observed_valid_accuracy": (
            len(correct_valid_records) / len(valid_records)
            if valid_records
            else 0.0
        ),
        "expected_sample_accuracy": (
            len(correct_valid_records) / expected_sample_count
            if expected_sample_count > 0
            else 0.0
        ),
        "coverage": len(records) / expected_sample_count if expected_sample_count > 0 else 0.0,
        "missing_samples": missing_samples,
        "error_records": error_records,
        "pass_at_k": tasks_passed / expected_task_count if expected_task_count > 0 else 0.0,
        "avg_at_k": sum(task_avg_scores) / len(task_avg_scores) if task_avg_scores else 0.0,
        "pass_power_k_available": pass_power_k_available,
        "pass_power_k": pass_power_k,
    }
