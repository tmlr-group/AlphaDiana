"""Compatibility exports from alphadiana.logprobs; no separate conversion state."""
from alphadiana.logprobs import (
    _compute_entropy,
    _compute_entropy_stats,
)

__all__ = ['_compute_entropy', '_compute_entropy_stats']
