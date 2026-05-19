"""Helpers for compact token logprob result artifacts."""
from __future__ import annotations

import math
from typing import Any, Iterable

INT16_PROB_SCALE = 32767
DEFAULT_TOP_LOGPROBS = 20


def probability_to_i16(prob: float) -> int:
    """Convert a probability into a non-negative Int16-compatible code."""
    code = int(round(prob * INT16_PROB_SCALE))
    return max(0, min(INT16_PROB_SCALE, code))


def softmax_from_logprobs(top_logprobs: list[dict]) -> list[float]:
    """Normalize top-K logprobs with the log-sum-exp trick."""
    if not top_logprobs:
        return []
    logps = [float(entry["logprob"]) for entry in top_logprobs]
    max_logp = max(logps)
    weights = [math.exp(logp - max_logp) for logp in logps]
    total = sum(weights)
    if total <= 0.0:
        return []
    return [weight / total for weight in weights]


def entropy_nats(probs: Iterable[float]) -> float:
    """Shannon entropy in nats for a probability distribution."""
    return -sum(prob * math.log(prob) for prob in probs if prob > 0.0)


def percentile(sorted_values: list[float], p: float) -> float:
    """Phase 9 percentile helper: floor index and clamp to the last value."""
    if not sorted_values:
        return 0.0
    idx = int(len(sorted_values) * p)
    return sorted_values[min(idx, len(sorted_values) - 1)]


def _candidate_to_raw(candidate: Any) -> dict:
    if isinstance(candidate, dict):
        return {
            "token": candidate.get("token", ""),
            "logprob": float(candidate.get("logprob", 0.0)),
        }
    return {
        "token": getattr(candidate, "token", ""),
        "logprob": float(getattr(candidate, "logprob", 0.0)),
    }


def _int16_record(token_index: int, token: str, top_entries: list[dict]) -> dict:
    probs = softmax_from_logprobs(top_entries)
    return {
        "token_index": token_index,
        "token": token,
        "top20": [
            {"token": entry["token"], "prob_i16": probability_to_i16(prob)}
            for entry, prob in zip(top_entries, probs)
        ],
        "entropy_nats": entropy_nats(probs),
    }


def top_logprob_to_int16_record(
    token_logprob: Any,
    token_index: int,
    *,
    top_k: int = DEFAULT_TOP_LOGPROBS,
) -> dict:
    """Convert one OpenAI SDK token-logprob object into compact Int16 form."""
    top_entries = [
        _candidate_to_raw(candidate)
        for candidate in (getattr(token_logprob, "top_logprobs", None) or [])[:top_k]
    ]
    return _int16_record(
        token_index,
        getattr(token_logprob, "token", ""),
        top_entries,
    )


def raw_record_to_int16_record(record: dict, *, top_k: int = DEFAULT_TOP_LOGPROBS) -> dict:
    """Convert a raw Phase 9 logprob JSONL record into compact Int16 form."""
    top_entries = [_candidate_to_raw(entry) for entry in record.get("top_logprobs", [])[:top_k]]
    return _int16_record(
        int(record.get("token_index", 0)),
        str(record.get("token", "")),
        top_entries,
    )


def entropy_stats_from_int16_records(records: list[dict]) -> dict:
    """Summarize per-token Int16 artifact entropies using the Phase 9 shape."""
    entropies = [float(record.get("entropy_nats") or 0.0) for record in records]
    if not entropies:
        return {"mean": 0.0, "max": 0.0, "p50": 0.0, "p90": 0.0, "n_tokens": 0}
    sorted_entropies = sorted(entropies)
    return {
        "mean": sum(entropies) / len(entropies),
        "max": max(entropies),
        "p50": percentile(sorted_entropies, 0.5),
        "p90": percentile(sorted_entropies, 0.9),
        "n_tokens": len(entropies),
    }
