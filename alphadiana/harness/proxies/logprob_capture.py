from __future__ import annotations

import logging
from typing import Any

from alphadiana.analysis.entropy import _compute_entropy_stats
from alphadiana.analysis.io.logprob_artifacts import DEFAULT_TOP_LOGPROBS

logger = logging.getLogger(__name__)


def resolve_logprob_capture_config(config: dict) -> dict:
    enabled = bool(config.get("capture_logprobs", False))
    if not enabled:
        return {"enabled": False, "top_logprobs": 0}
    top_logprobs = int(config.get("top_logprobs", DEFAULT_TOP_LOGPROBS))
    return {"enabled": True, "top_logprobs": max(0, top_logprobs)}


def apply_openai_logprob_request(payload: dict, capture: dict) -> None:
    if not capture.get("enabled", False):
        return
    payload["logprobs"] = True
    payload["top_logprobs"] = int(capture.get("top_logprobs", DEFAULT_TOP_LOGPROBS))


def _candidate_to_dict(candidate: Any) -> dict:
    if isinstance(candidate, dict):
        return {
            "token": candidate.get("token", ""),
            "logprob": float(candidate.get("logprob", 0.0)),
        }
    return {
        "token": getattr(candidate, "token", ""),
        "logprob": float(getattr(candidate, "logprob", 0.0)),
    }


def raw_token_logprob_to_record(token_logprob: object, token_index: int) -> dict:
    return {
        "token_index": token_index,
        "token": getattr(token_logprob, "token", ""),
        "logprob": float(getattr(token_logprob, "logprob", 0.0)),
        "top_logprobs": [
            _candidate_to_dict(candidate)
            for candidate in (getattr(token_logprob, "top_logprobs", None) or [])
        ],
    }


def raw_token_logprob_dict_to_record(raw: dict, token_index: int) -> dict:
    return {
        "token_index": token_index,
        "token": raw.get("token", ""),
        "logprob": float(raw.get("logprob", 0.0)),
        "top_logprobs": [
            _candidate_to_dict(candidate)
            for candidate in (raw.get("top_logprobs") or [])
        ],
    }


def _payload_choices(payload: object) -> list[Any]:
    if isinstance(payload, dict):
        choices = payload.get("choices", [])
        return choices if isinstance(choices, list) else []
    choices = getattr(payload, "choices", [])
    return choices if isinstance(choices, list) else []


def _choice_logprobs_content(choice: Any) -> list[Any]:
    if isinstance(choice, dict):
        logprobs = choice.get("logprobs") or {}
        if isinstance(logprobs, dict):
            content = logprobs.get("content", [])
            return content if isinstance(content, list) else []
        return []
    logprobs = getattr(choice, "logprobs", None)
    content = getattr(logprobs, "content", []) if logprobs is not None else []
    return content if isinstance(content, list) else []


def extract_openai_logprob_records(payload: object, *, start_index: int = 0) -> tuple[list[dict], int]:
    records: list[dict] = []
    token_index = start_index
    choices = _payload_choices(payload)
    if not choices:
        return records, token_index

    # OpenAI chat-completions logprobs shape is choices[0].logprobs.content (list of per-token dict/object entries).
    content = _choice_logprobs_content(choices[0])
    for token_logprob in content:
        if isinstance(token_logprob, dict):
            records.append(raw_token_logprob_dict_to_record(token_logprob, token_index))
        else:
            records.append(raw_token_logprob_to_record(token_logprob, token_index))
        token_index += 1
    return records, token_index


def finalize_logprob_capture(
    *,
    harness: str,
    enabled: bool,
    records: list[dict],
    metadata: dict,
) -> tuple[dict, dict]:
    response_metadata = dict(metadata or {})

    if enabled and records:
        status = "captured"
        response_metadata["logprob_records"] = records
        entropy_input = records
    elif enabled:
        status = "requested_missing"
        response_metadata.pop("logprob_records", None)
        entropy_input = []
    else:
        status = "not_requested"
        response_metadata.pop("logprob_records", None)
        entropy_input = []

    response_metadata["logprobs_capture_status"] = status
    response_metadata["logprob_capture_harness"] = harness
    token_entropy_stats = _compute_entropy_stats(entropy_input)

    logger.info(
        "logprob capture harness=%s enabled=%s status=%s records=%d",
        harness,
        enabled,
        status,
        len(records),
    )

    return token_entropy_stats, response_metadata
