"""Record-level action feature extraction from persisted run artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from alphadiana.analysis.result_reader import RunBundle, load_jsonl_records, resolve_run_relative_path
from alphadiana.results.logprob_artifacts import entropy_stats_from_int16_records
from alphadiana.results.status import infer_score_status


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
            (int(entry.get("prob_i16", 0) or 0) for entry in top20 if isinstance(entry, dict)),
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
