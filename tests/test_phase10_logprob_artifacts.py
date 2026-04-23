"""Phase 10 logprob artifact conversion tests."""
from __future__ import annotations

import math

from alphadiana.results.logprob_artifacts import (
    INT16_PROB_SCALE,
    entropy_stats_from_int16_records,
    probability_to_i16,
    raw_record_to_int16_record,
    softmax_from_logprobs,
    top_logprob_to_int16_record,
)


class _Candidate:
    def __init__(self, token: str, logprob: float) -> None:
        self.token = token
        self.logprob = logprob


class _TokenLogprob:
    def __init__(self, token: str, logprob: float, top_logprobs: list[_Candidate]) -> None:
        self.token = token
        self.logprob = logprob
        self.top_logprobs = top_logprobs


def test_probability_to_i16_clamps_to_int16_range() -> None:
    assert probability_to_i16(-0.5) == 0
    assert probability_to_i16(0.0) == 0
    assert probability_to_i16(1.0) == INT16_PROB_SCALE
    assert probability_to_i16(2.0) == INT16_PROB_SCALE
    assert probability_to_i16(0.5) == 16384


def test_softmax_from_logprobs_normalizes_with_logsumexp() -> None:
    probs = softmax_from_logprobs(
        [
            {"token": "A", "logprob": -1001.0},
            {"token": "B", "logprob": -1000.0},
        ]
    )

    assert math.isclose(sum(probs), 1.0)
    assert probs[1] > probs[0]


def test_top_logprob_to_int16_record_accepts_sdk_objects_and_limits_top20() -> None:
    token_logprob = _TokenLogprob(
        "answer",
        -0.1,
        [_Candidate(f"tok{i}", -float(i)) for i in range(25)],
    )

    record = top_logprob_to_int16_record(token_logprob, 7)

    assert record["token_index"] == 7
    assert record["token"] == "answer"
    assert len(record["top20"]) == 20
    assert {"token", "prob_i16"} == set(record["top20"][0])
    assert record["top20"][0]["token"] == "tok0"
    assert record["entropy_nats"] > 0.0


def test_raw_record_to_int16_record_keeps_top20_shape() -> None:
    raw_record = {
        "token_index": 3,
        "token": "A",
        "top_logprobs": [
            {"token": "A", "logprob": -0.1},
            {"token": "B", "logprob": -1.1},
        ],
    }

    record = raw_record_to_int16_record(raw_record)

    assert record["token_index"] == 3
    assert record["token"] == "A"
    assert list(record) == ["token_index", "token", "top20", "entropy_nats"]
    assert record["top20"][0]["token"] == "A"
    assert isinstance(record["top20"][0]["prob_i16"], int)
    assert record["entropy_nats"] > 0.0


def test_entropy_stats_from_int16_records_preserves_phase9_shape() -> None:
    assert entropy_stats_from_int16_records([]) == {
        "mean": 0.0,
        "max": 0.0,
        "p50": 0.0,
        "p90": 0.0,
        "n_tokens": 0,
    }

    stats = entropy_stats_from_int16_records(
        [
            {"entropy_nats": 0.1},
            {"entropy_nats": 0.2},
            {"entropy_nats": 0.3},
            {"entropy_nats": 0.4},
        ]
    )

    assert stats == {
        "mean": 0.25,
        "max": 0.4,
        "p50": 0.3,
        "p90": 0.4,
        "n_tokens": 4,
    }
