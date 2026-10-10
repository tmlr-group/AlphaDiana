"""Reliability projections of the canonical latest-sample statistics."""

from __future__ import annotations
from typing import Any
from alphadiana.analysis.run_summary import ERROR_STATUSES, RunStatistics


def compute_reliability_summary(
    records: list[dict[str, Any]], manifest: dict[str, Any]
) -> dict[str, Any]:
    """Keep observed-valid and planned-sample accuracy as distinct views."""
    base = RunStatistics.from_records(records, manifest)
    n, expected, k = len(base.results), base.expected_sample_count, base.num_samples
    power_available = (
        k > 1
        and base.missing_samples == 0
        and all(len(base.by_task.get(t, [])) >= k for t in base.task_ids)
    )
    return {
        "data_view": "latest_samples",
        "selection": "last_persisted_attempt_per_task_sample",
        "denominators": {
            "observed_valid_accuracy": len(base.valid),
            "expected_sample_accuracy": expected,
            "coverage": expected,
            "pass_at_k": base.expected_task_count,
            "avg_at_k": base.expected_task_count,
            "pass_power_k": base.expected_task_count,
        },
        "num_samples": k,
        "expected_sample_count": expected,
        "written_records": n,
        "valid_scored": len(base.valid),
        "observed_valid_accuracy": (
            base.correct / len(base.valid) if base.valid else 0.0
        ),
        "expected_sample_accuracy": base.correct / expected if expected > 0 else 0.0,
        "coverage": n / expected if expected > 0 else 0.0,
        "missing_samples": base.missing_samples,
        "error_records": base.errors,
        "pass_at_k": base.pass_at_k,
        "avg_at_k": base.avg_at_k,
        "pass_power_k_available": power_available,
        "pass_power_k": (
            sum(rate**k for rate in base.task_rates) / base.expected_task_count
            if power_available and base.expected_task_count > 0
            else None
        ),
    }
