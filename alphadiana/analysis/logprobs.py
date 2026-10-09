"""Compatibility exports from alphadiana.logprobs; no separate conversion state."""
from alphadiana.logprobs import (
    INT16_PROB_SCALE,
    _softmax_from_logprobs,
    _entropy_nats,
    _prob_to_i16,
    quantize_record_int16,
    quantize_records_int16,
)

__all__ = ['INT16_PROB_SCALE', '_softmax_from_logprobs', '_entropy_nats', '_prob_to_i16', 'quantize_record_int16', 'quantize_records_int16']
