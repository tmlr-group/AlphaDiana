"""Record feature extraction and clustering of those feature views."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any


from alphadiana.results.logprobs import entropy_stats_from_int16_records
from alphadiana.results.reader import (
    RunBundle,
    load_jsonl_records,
    resolve_run_relative_path,
)
from alphadiana.results.status import infer_score_status


if TYPE_CHECKING:
    import numpy as np


def load_int16_records(results_dir: Path, rel_path: str) -> list[dict[str, Any]]:
    """Load compact Int16 logprob sidecar records from a run-relative path."""
    if not rel_path:
        return []
    return load_jsonl_records(resolve_run_relative_path(results_dir, rel_path))


def summarize_int16_sidecar(results_dir: Path, rel_path: str) -> dict[str, float | int]:
    """Summarize Int16 sidecar entropy with the ResultStore helper semantics."""
    return entropy_stats_from_int16_records(load_int16_records(results_dir, rel_path))


def _top1_mean_prob(records: list[dict[str, Any]], scale: int | float) -> float:
    if not records or not scale:
        return 0.0
    top1_probs: list[float] = []
    for record in records:
        top20 = record.get("top20")
        if not isinstance(top20, list) or not top20:
            continue
        best = max(
            (
                int(entry.get("prob_i16", 0) or 0)
                for entry in top20
                if isinstance(entry, dict)
            ),
            default=0,
        )
        top1_probs.append(best / float(scale))
    return sum(top1_probs) / len(top1_probs) if top1_probs else 0.0


def _logprobs_capture_status(record: dict[str, Any]) -> str:
    metadata = record.get("metadata")
    if isinstance(metadata, dict) and metadata.get("logprobs_capture_status"):
        return str(metadata["logprobs_capture_status"])
    if record.get("logprobs_int16_path"):
        return "captured"
    return "missing"


def _error_type(record: dict[str, Any]) -> str:
    error = record.get("error")
    if isinstance(error, dict) and error.get("error_type"):
        return str(error["error_type"])
    return ""


def _completion_tokens(record: dict[str, Any]) -> int:
    token_usage = record.get("token_usage")
    if isinstance(token_usage, dict):
        return int(token_usage.get("completion_tokens") or 0)
    return 0


def build_record_feature_rows(bundle: RunBundle) -> list[dict[str, Any]]:
    """Build one action feature row per persisted result record."""
    rows: list[dict[str, Any]] = []
    for record in bundle.records:
        int16_path = str(record.get("logprobs_int16_path") or "")
        int16_records = load_int16_records(bundle.results_dir, int16_path)
        if int16_records:
            entropy = entropy_stats_from_int16_records(int16_records)
        else:
            stats = record.get("token_entropy_stats")
            entropy = stats if isinstance(stats, dict) else {}
        predicted = record.get("predicted")
        scale = record.get("int16_probability_scale") or 0
        rows.append(
            {
                "data_view": "all_attempts",
                "run_id": str(record.get("run_id") or bundle.run_id),
                "task_id": str(record.get("task_id") or ""),
                "sample_index": int(record.get("sample_index") or 0),
                "score_status": infer_score_status(record),
                "correct": record.get("correct"),
                "predicted": predicted,
                "ground_truth": record.get("ground_truth"),
                "answer_length": len(str(predicted)) if predicted is not None else 0,
                "logprobs_capture_status": _logprobs_capture_status(record),
                "has_logprobs": bool(int16_records),
                "n_tokens": int(entropy.get("n_tokens") or 0),
                "entropy_mean": float(entropy.get("mean") or 0.0),
                "entropy_p90": float(entropy.get("p90") or 0.0),
                "entropy_max": float(entropy.get("max") or 0.0),
                "top1_mean_prob": _top1_mean_prob(int16_records, scale),
                "wall_time_sec": float(record.get("wall_time_sec") or 0.0),
                "completion_tokens": _completion_tokens(record),
                "error_type": _error_type(record),
            }
        )
    return rows


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


def numeric_matrix(
    rows: list[dict[str, Any]], feature_columns: Sequence[str]
) -> np.ndarray:
    """Convert feature rows into a numeric matrix, coercing missing values to zero."""
    import numpy as np

    return np.array(
        [
            [_numeric_value(row.get(column)) for column in feature_columns]
            for row in rows
        ],
        dtype=float,
    )


def _counter(values: Sequence[Any]) -> dict[str, int]:
    return {
        str(key): count
        for key, count in sorted(Counter(values).items(), key=lambda item: str(item[0]))
    }


def _summarize_clusters(
    rows: list[dict[str, Any]], labels: list[int]
) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row, label in zip(rows, labels):
        grouped[int(label)].append(row)

    clusters: list[dict[str, Any]] = []
    for cluster_id in sorted(grouped):
        cluster_rows = grouped[cluster_id]
        entropy_values = [
            _numeric_value(row.get("entropy_mean")) for row in cluster_rows
        ]
        clusters.append(
            {
                "cluster_id": cluster_id,
                "n": len(cluster_rows),
                "score_status_counts": _counter(
                    [row.get("score_status", "") for row in cluster_rows]
                ),
                "correct_counts": _counter(
                    [row.get("correct") for row in cluster_rows]
                ),
                "entropy_mean_avg": (
                    sum(entropy_values) / len(entropy_values) if entropy_values else 0.0
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
    from scipy.cluster.hierarchy import fcluster, linkage

    columns = list(feature_columns)
    if len(rows) < 2:
        labels = list(range(1, len(rows) + 1))
    else:
        matrix = numeric_matrix(rows, columns)
        cluster_count = max(1, min(max_clusters, len(rows)))
        # scipy.linkage.average is represented by method="average" here.
        linkage_matrix = linkage(matrix, method="average", metric="euclidean")
        labels = [
            int(label)
            for label in fcluster(linkage_matrix, cluster_count, criterion="maxclust")
        ]

    return {
        "method": _METHOD,
        "distance": _DISTANCE,
        "feature_columns": columns,
        "n_rows": len(rows),
        "labels": labels,
        "clusters": _summarize_clusters(rows, labels),
    }


def cluster_analysis_views(
    rows: list[dict[str, Any]], *, max_clusters: int = 4
) -> dict[str, Any]:
    """Keep historical attempts in both cluster views, including superseded ones."""
    valid_rows = [row for row in rows if row.get("score_status") == "valid_scored"]
    return {
        name: {
            **cluster_rows(selected, max_clusters=max_clusters),
            "data_view": "all_attempts",
            "denominator": len(selected),
            "denominator_unit": "persisted_attempts",
        }
        for name, selected in (
            ("valid_scored_only", valid_rows),
            ("all_records_status", rows),
        )
    }
