"""Clustering helpers for offline action-space analysis."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import Any

import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage

DEFAULT_CLUSTER_FEATURES = (
    "answer_length",
    "has_logprobs",
    "n_tokens",
    "entropy_mean",
    "entropy_p90",
    "entropy_max",
    "top1_mean_prob",
    "wall_time_sec",
    "completion_tokens",
)

_METHOD = "scipy.linkage.average"
_DISTANCE = "euclidean"


def _numeric_value(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def numeric_matrix(rows: list[dict[str, Any]], feature_columns: Sequence[str]) -> np.ndarray:
    """Convert feature rows into a numeric matrix, coercing missing values to zero."""
    return np.array(
        [
            [_numeric_value(row.get(column)) for column in feature_columns]
            for row in rows
        ],
        dtype=float,
    )


def _counter(values: Sequence[Any]) -> dict[str, int]:
    return {str(key): count for key, count in sorted(Counter(values).items(), key=lambda item: str(item[0]))}


def _summarize_clusters(rows: list[dict[str, Any]], labels: list[int]) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row, label in zip(rows, labels):
        grouped[int(label)].append(row)

    clusters: list[dict[str, Any]] = []
    for cluster_id in sorted(grouped):
        cluster_rows = grouped[cluster_id]
        entropy_values = [_numeric_value(row.get("entropy_mean")) for row in cluster_rows]
        clusters.append(
            {
                "cluster_id": cluster_id,
                "n": len(cluster_rows),
                "score_status_counts": _counter([row.get("score_status", "") for row in cluster_rows]),
                "correct_counts": _counter([row.get("correct") for row in cluster_rows]),
                "entropy_mean_avg": (
                    sum(entropy_values) / len(entropy_values)
                    if entropy_values
                    else 0.0
                ),
                "exemplar_task_ids": [
                    str(row.get("task_id") or "")
                    for row in cluster_rows[:3]
                    if row.get("task_id")
                ],
                "rows": cluster_rows,
            }
        )
    return clusters


def cluster_rows(
    rows: list[dict[str, Any]],
    *,
    feature_columns: Sequence[str] = DEFAULT_CLUSTER_FEATURES,
    max_clusters: int = 4,
) -> dict[str, Any]:
    """Cluster feature rows with SciPy average-linkage hierarchical clustering."""
    columns = list(feature_columns)
    if len(rows) < 2:
        labels = list(range(1, len(rows) + 1))
    else:
        matrix = numeric_matrix(rows, columns)
        cluster_count = max(1, min(max_clusters, len(rows)))
        # scipy.linkage.average is represented by method="average" here.
        linkage_matrix = linkage(matrix, method="average", metric="euclidean")
        labels = [int(label) for label in fcluster(linkage_matrix, cluster_count, criterion="maxclust")]

    return {
        "method": _METHOD,
        "distance": _DISTANCE,
        "feature_columns": columns,
        "n_rows": len(rows),
        "labels": labels,
        "clusters": _summarize_clusters(rows, labels),
    }


def cluster_analysis_views(rows: list[dict[str, Any]], *, max_clusters: int = 4) -> dict[str, Any]:
    """Build action-only and operational-status cluster views."""
    valid_rows = [row for row in rows if row.get("score_status") == "valid_scored"]
    return {
        "valid_scored_only": cluster_rows(valid_rows, max_clusters=max_clusters),
        "all_records_status": cluster_rows(rows, max_clusters=max_clusters),
    }
