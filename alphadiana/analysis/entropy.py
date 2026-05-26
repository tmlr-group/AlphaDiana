"""Entropy helpers for Phase 9 per-token logprob analysis.

Shannon entropy (nats) over top-K softmax-normalized logprobs.
Uses the log-sum-exp trick for numerical stability.
"""
from __future__ import annotations

import math
from typing import Iterable


def _compute_entropy(top_logprobs: list[dict]) -> float:
    """Shannon entropy (nats) from a list of {'token', 'logprob'} dicts.

    Because only the top-K logprobs are returned by the SDK, this is a
    lower-bound on the true token-distribution entropy.
    """
    if not top_logprobs:
        return 0.0
    log_probs = [float(entry["logprob"]) for entry in top_logprobs]
    max_lp = max(log_probs)
    probs = [math.exp(lp - max_lp) for lp in log_probs]
    total = sum(probs)
    if total <= 0:
        return 0.0
    probs = [p / total for p in probs]
    return -sum(p * math.log(p) for p in probs if p > 0)


def _compute_entropy_stats(records: Iterable[dict]) -> dict:
    """Summarize per-token entropies into {mean, max, p50, p90, n_tokens}."""
    entropies = [_compute_entropy(r.get("top_logprobs", [])) for r in records]
    n = len(entropies)
    if n == 0:
        return {"mean": 0.0, "max": 0.0, "p50": 0.0, "p90": 0.0, "n_tokens": 0}
    sorted_e = sorted(entropies)

    def _pct(arr: list[float], p: float) -> float:
        idx = int(len(arr) * p)
        return arr[min(idx, len(arr) - 1)]

    return {
        "mean": sum(entropies) / n,
        "max": max(entropies),
        "p50": _pct(sorted_e, 0.5),
        "p90": _pct(sorted_e, 0.9),
        "n_tokens": n,
    }
