from __future__ import annotations

import json
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


def _safe_json_text(value: Any, *, limit: int = 20000) -> str:
    if value in (None, ""):
        return ""
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        text = str(value)
    return text[:limit]


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


def is_legacy_timeout_zero_record(record: dict[str, Any]) -> bool:
    """Return whether an old error record should now count as timeout score 0."""
    metadata = _as_dict(record.get("metadata"))
    error = _as_dict(record.get("error"))
    explicit = str(record.get("score_status", "") or "").strip()
    if explicit == VALID_SCORE_STATUS:
        return False
    if record.get("score") is not None or record.get("correct") is not None:
        return False
    if not error and explicit not in {"agent_error", "runtime_error", "preserved_failure"}:
        return False
    if str(record.get("finish_reason", "") or "").strip() == "preserved_failure":
        return False
    if metadata.get("zeroclaw_preserved_failure"):
        return False

    error_type = str(error.get("error_type", "") or "").strip().lower()
    failure_reason = str(metadata.get("failure_reason", "") or "").strip().lower()
    if error_type in {
        "provider_error",
        "proxy_error",
        "control_plane_unavailable",
        "context_overflow",
    }:
        return False
    if failure_reason in {
        "provider_error",
        "proxy_error",
        "control_plane_unavailable",
        "context_overflow",
    }:
        return False

    response_json = _as_dict(record.get("response_json"))
    evidence_text = "\n".join([
        str(record.get("finish_reason", "") or ""),
        str(record.get("rationale", "") or ""),
        _safe_json_text(error),
        _safe_json_text(metadata),
        _safe_json_text(response_json),
        str(record.get("raw_output", "") or "")[:20000],
    ]).lower()

    if "contextoverflowerror" in evidence_text or "vllmvalidationerror" in evidence_text:
        return False
    if "openclaw_session_tainted" in evidence_text or "heartbeat" in evidence_text:
        return False

    timeout_markers = (
        "agenttimeouterror",
        "the operation timed out",
        "timed out after",
        "timeout after",
        "stream total timeout",
        "stream idle timeout",
        "request timed out",
        "readtimeout",
        "zeroclaw agent timed out",
        '"error_type": "timeout"',
        '"error_type":"timeout"',
        '"failure_reason": "timeout"',
        '"failure_reason":"timeout"',
    )
    if any(marker in evidence_text for marker in timeout_markers):
        return True

    returncode = metadata.get("returncode", response_json.get("returncode"))
    if returncode == -1 and "timeout" in evidence_text:
        return True

    runtime_only_markers = (
        "zeroclaw produced no assistant output; stdout contained only runtime/provider logs",
        "stdout contained only runtime/provider logs",
    )
    if (
        any(marker in evidence_text for marker in runtime_only_markers)
        and (
            '"event_type": "llm_request"' in evidence_text
            or '"event_type":"llm_request"' in evidence_text
        )
        and float(record.get("wall_time_sec") or 0.0) >= 30.0
    ):
        return True

    return False


def normalize_legacy_timeout_zero_record(record: dict[str, Any]) -> dict[str, Any]:
    if not is_legacy_timeout_zero_record(record):
        return record
    normalized = dict(record)
    metadata = dict(_as_dict(record.get("metadata")))
    metadata.update({
        "failure_reason": "timeout",
        "legacy_timeout_scored_zero": True,
    })
    score_metadata = dict(_as_dict(record.get("score_metadata")))
    score_metadata["legacy_timeout_scored_zero"] = True
    normalized.update({
        "predicted": None,
        "correct": False,
        "score": 0.0,
        "rationale": record.get("rationale") or "Legacy timeout counted as score 0.",
        "score_metadata": score_metadata,
        "finish_reason": "timeout",
        "metadata": metadata,
        "error": None,
        "score_status": VALID_SCORE_STATUS,
    })
    return normalized


def infer_score_status(record: dict[str, Any]) -> str:
    if is_legacy_timeout_zero_record(record):
        return VALID_SCORE_STATUS

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
