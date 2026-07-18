"""Shared QJL task contract and Level 3 score-vector helpers."""

from __future__ import annotations

import math
from typing import Any, Iterable

QJL_REPOSITORY = "https://github.com/amirzandieh/QJL.git"
QJL_COMMIT = "648b364"
QJL_DISTORTION_THRESHOLD = 0.52
QJL_RETENTION_THRESHOLD = 0.90
QJL_EDITABLE_GLOB = "qjl_kernel/csrc/*.cu"
QJL_EDITABLE_PREFIX = "qjl_kernel/csrc/"
QJL_HIDDEN_SEED_COUNT = 3


def finite_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def valid_gain(result: dict[str, Any], baseline_runtime_ms: float) -> float:
    """Return effective gain, gated by verifier correctness."""
    metrics = result.get("metrics", {})
    runtime_ms = finite_float(metrics.get("runtime_ms"), 0.0)
    if not bool(result.get("correct")) or runtime_ms <= 0 or baseline_runtime_ms <= 0:
        return 0.0
    return baseline_runtime_ms / runtime_ms


def build_score_vector(
    *,
    official_result: dict[str, Any],
    attempt_results: Iterable[dict[str, Any]],
    baseline_runtime_ms: float,
    retention_threshold: float = QJL_RETENTION_THRESHOLD,
    cost: dict[str, Any] | None = None,
    baseline_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the auditable C/G/S/M/K score vector for one episode."""
    final_gain = valid_gain(official_result, baseline_runtime_ms)
    comparison_result = official_result.get("comparison_result")
    if isinstance(comparison_result, dict):
        attempt_gains = [valid_gain(comparison_result, baseline_runtime_ms)]
    else:
        attempt_gains = [
            valid_gain(result, baseline_runtime_ms)
            for result in attempt_results
        ]
    baseline_gain = (
        valid_gain(baseline_result, baseline_runtime_ms)
        if baseline_result is not None
        else (1.0 if baseline_runtime_ms > 0 else 0.0)
    )
    best_gain = max([baseline_gain, final_gain, *attempt_gains], default=0.0)
    stability = final_gain / best_gain if best_gain > 0 else 0.0
    stability_pass = bool(final_gain > 0 and stability >= retention_threshold)
    official_correct = bool(official_result.get("correct"))
    episode_pass = official_correct and stability_pass

    return {
        "schema_version": "moltbench.score.v3",
        "C": {
            "value": official_correct,
            "gate": "build succeeds and hidden-input distortion is below threshold",
        },
        "G": {
            "value": final_gain if official_correct else 0.0,
            "raw_final_gain": final_gain,
            "best_valid_gain": best_gain,
            "formula": "baseline_runtime_ms / candidate_runtime_ms",
        },
        "S": {
            "value": stability,
            "retention_threshold": retention_threshold,
            "pass": stability_pass,
            "formula": "final_valid_gain / episode_best_valid_gain",
        },
        "M": "N/A",
        "K": dict(cost or {}),
        "score": final_gain if episode_pass else 0.0,
        "correct": episode_pass,
        "baseline_result": baseline_result,
        "official_result": official_result,
    }
