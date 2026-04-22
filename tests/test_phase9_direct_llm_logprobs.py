"""Phase 9: DirectLLMAgent logprobs capture + entropy unit tests."""
from __future__ import annotations

import math

import pytest

from alphadiana.agent._entropy import _compute_entropy, _compute_entropy_stats
from alphadiana.agent.base import AgentResponse


class TestComputeEntropy:
    def test_empty_returns_zero(self):
        assert _compute_entropy([]) == 0.0

    def test_single_option_returns_zero(self):
        assert _compute_entropy([{"token": "a", "logprob": 0.0}]) == 0.0

    def test_uniform_top20_returns_log20(self):
        lp = math.log(1 / 20)
        top = [{"token": f"t{i}", "logprob": lp} for i in range(20)]
        assert abs(_compute_entropy(top) - math.log(20)) < 1e-9

    def test_skewed_distribution_low_entropy(self):
        top = [{"token": "winner", "logprob": 0.0}] + [
            {"token": f"t{i}", "logprob": -10.0} for i in range(19)
        ]
        assert _compute_entropy(top) < 0.01


class TestComputeEntropyStats:
    def test_empty_records(self):
        assert _compute_entropy_stats([]) == {
            "mean": 0.0, "max": 0.0, "p50": 0.0, "p90": 0.0, "n_tokens": 0,
        }

    def test_identical_records(self):
        top = [{"token": "a", "logprob": 0.0}, {"token": "b", "logprob": 0.0}]
        records = [{"top_logprobs": top} for _ in range(10)]
        stats = _compute_entropy_stats(records)
        assert stats["n_tokens"] == 10
        assert abs(stats["mean"] - math.log(2)) < 1e-9
        assert abs(stats["max"] - math.log(2)) < 1e-9
        assert abs(stats["p50"] - math.log(2)) < 1e-9
        assert abs(stats["p90"] - math.log(2)) < 1e-9

    def test_p50_and_p90_indexing(self):
        # Construct records with strictly increasing entropies (uniform over K=i tokens)
        records = []
        for k in range(1, 11):  # k = 1..10 tokens
            top = [{"token": f"t{j}", "logprob": 0.0} for j in range(k)]
            records.append({"top_logprobs": top})
        stats = _compute_entropy_stats(records)
        assert stats["n_tokens"] == 10
        # sorted entropies = [log(1)=0, log(2), ..., log(10)]
        # p50 index = int(10*0.5) = 5 -> sorted[5] = log(6)
        assert abs(stats["p50"] - math.log(6)) < 1e-9
        # p90 index = int(10*0.9) = 9 -> sorted[9] = log(10)
        assert abs(stats["p90"] - math.log(10)) < 1e-9


class TestAgentResponseField:
    def test_default_empty_dict(self):
        r = AgentResponse(answer="X")
        assert r.token_entropy_stats == {}

    def test_explicit_value_retained(self):
        payload = {"mean": 1.0, "max": 2.0, "p50": 1.0, "p90": 1.5, "n_tokens": 5}
        r = AgentResponse(answer="X", token_entropy_stats=payload)
        assert r.token_entropy_stats == payload
