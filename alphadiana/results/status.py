from __future__ import annotations

from typing import Any

VALID_SCORE_STATUS = "valid_scored"

INVALID_SCORE_STATUSES = {
    "unscored",
    "preserved_failure",
    "agent_error",
    "provider_error",
    "runtime_error",
    "verifier_error",
    "scorer_error",
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _classify_error_status(error: dict[str, Any]) -> str:
    error_type = str(error.get("error_type", "") or "").strip().lower()
    if "provider" in error_type or "http" in error_type or "api" in error_type:
        return "provider_error"
    if "agent" in error_type:
        return "agent_error"
    if "score" in error_type or "scorer" in error_type:
        return "scorer_error"
    if "verify" in error_type or "verifier" in error_type:
        return "verifier_error"
    return "runtime_error"


def infer_score_status(record: dict[str, Any]) -> str:
    explicit = str(record.get("score_status", "") or "").strip()
    if explicit:
        return explicit

    metadata = _as_dict(record.get("metadata"))
    benchmark_name = str(record.get("benchmark_name", "") or "").strip()
    finish_reason = str(record.get("finish_reason", "") or "").strip()
    error = _as_dict(record.get("error"))
    verifier_status = str(metadata.get("verifier_status", "") or "").strip()
    zeroclaw_classification = str(
        metadata.get("zeroclaw_selected_classification", "") or ""
    ).strip()

    if finish_reason == "preserved_failure" or metadata.get("zeroclaw_preserved_failure"):
        return "preserved_failure"
    if zeroclaw_classification in {"cli_error"}:
        return "agent_error"
    if zeroclaw_classification in {"provider_error"}:
        return "provider_error"
    if zeroclaw_classification in {"runtime_error"}:
        return "runtime_error"
    if benchmark_name == "terminal_bench2" and verifier_status == "skipped_duplicate":
        if (
            metadata.get("verifier_reward_observed") is True
            and record.get("score") is not None
            and record.get("correct") is not None
        ):
            return VALID_SCORE_STATUS
        return "verifier_error"
    if verifier_status and verifier_status != "ok":
        return "verifier_error"
    if benchmark_name == "terminal_bench2" and verifier_status != "ok":
        return "verifier_error"
    if error:
        return _classify_error_status(error)
    if record.get("score") is None or record.get("correct") is None:
        return "unscored"
    return VALID_SCORE_STATUS


def is_valid_completed_record(
    record: dict[str, Any],
    *,
    scorer_name: str | None = None,
) -> bool:
    if scorer_name and record.get("scorer_name") != scorer_name:
        return False
    return infer_score_status(record) == VALID_SCORE_STATUS
