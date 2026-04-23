"""Int16 quantization for per-token top-K logprobs.

The agent captures raw-float logprob records
    {token_index, token, logprob, top_logprobs: [{token, logprob}, ...]}
and `_compute_entropy_stats` summarises the whole list into a 5-number
aggregate. When many tasks are kept on disk, the raw-float JSONL is bulky
(~400-600 bytes per token). This module converts each record into a
compact form:

    {token_index, token, top20: [{token, prob_i16}, ...], entropy_nats}

where `prob_i16` is the softmax-normalised probability rounded to
`round(p * 32767)` in the range [0, 32767]. `entropy_nats` is the Shannon
entropy (nats) of the top-K truncated+renormalised distribution, stored
per-token so downstream analyses don't have to recompute it.

The quantisation is a lossy one-way transform: the float logprob values
are discarded. Callers that need the raw floats should keep
`logprobs_format: "float"` (default).
"""
from __future__ import annotations

import math
from typing import Any, Iterable

INT16_PROB_SCALE: int = 32767


def _softmax_from_logprobs(top_logprobs: list[dict]) -> list[float]:
    if not top_logprobs:
        return []
    logps = [float(entry["logprob"]) for entry in top_logprobs]
    max_lp = max(logps)
    weights = [math.exp(lp - max_lp) for lp in logps]
    total = sum(weights)
    if total <= 0.0:
        return []
    return [w / total for w in weights]


def _entropy_nats(probs: Iterable[float]) -> float:
    return -sum(p * math.log(p) for p in probs if p > 0.0)


def _prob_to_i16(prob: float) -> int:
    code = int(round(prob * INT16_PROB_SCALE))
    if code < 0:
        return 0
    if code > INT16_PROB_SCALE:
        return INT16_PROB_SCALE
    return code


def quantize_record_int16(record: dict, top_k: int = 20) -> dict:
    """Convert one raw-float logprob record into the compact int16 form."""
    top_entries = list(record.get("top_logprobs", []))[:top_k]
    probs = _softmax_from_logprobs(top_entries)
    return {
        "token_index": record.get("token_index", 0),
        "token": record.get("token", ""),
        "top20": [
            {"token": entry["token"], "prob_i16": _prob_to_i16(p)}
            for entry, p in zip(top_entries, probs)
        ],
        "entropy_nats": _entropy_nats(probs),
    }


def quantize_records_int16(records: list[dict], top_k: int = 20) -> list[dict]:
    """Apply `quantize_record_int16` over a list of raw-float records."""
    return [quantize_record_int16(r, top_k=top_k) for r in records]
