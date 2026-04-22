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


from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from alphadiana.agent.direct_llm import DirectLLMAgent
from alphadiana.benchmark.base import BenchmarkTask


def _make_top(lp_value: float, index: int) -> SimpleNamespace:
    return SimpleNamespace(
        token=f"tok{index}",
        logprob=lp_value,
        top_logprobs=[
            SimpleNamespace(token=f"cand{i}", logprob=-float(i))
            for i in range(20)
        ],
    )


def _fake_stream_chunks(num_tokens: int):
    """Yield OpenAI-SDK-shaped stream chunks with logprobs."""
    for i in range(num_tokens):
        yield SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content=f"tok{i}"),
                    finish_reason=None,
                    logprobs=SimpleNamespace(
                        content=[_make_top(-0.5, i)]
                    ),
                )
            ],
            usage=None,
        )
    # Final chunk with finish_reason + usage
    yield SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content=""),
                finish_reason="stop",
                logprobs=None,
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=10, completion_tokens=num_tokens,
            total_tokens=10 + num_tokens,
        ),
    )


class TestDirectLLMAgentLogprobsSetup:
    def test_default_disabled(self):
        agent = DirectLLMAgent()
        agent.setup({"model": "m", "api_base": "b", "api_key": "EMPTY"})
        assert agent._capture_logprobs is False
        assert agent._top_logprobs == 0

    def test_enabled_defaults_to_20(self):
        agent = DirectLLMAgent()
        agent.setup({"model": "m", "api_base": "b", "api_key": "EMPTY", "capture_logprobs": True})
        assert agent._capture_logprobs is True
        assert agent._top_logprobs == 20

    def test_enabled_with_custom_k(self):
        agent = DirectLLMAgent()
        agent.setup({"model": "m", "api_base": "b", "api_key": "EMPTY",
                     "capture_logprobs": True, "top_logprobs": 5})
        assert agent._top_logprobs == 5


class TestDirectLLMAgentStreamingLogprobs:
    def _build_agent(self, capture: bool):
        agent = DirectLLMAgent()
        agent.setup({
            "model": "m", "api_base": "b", "api_key": "EMPTY",
            "stream": True, "max_tokens": 256,
            "capture_logprobs": capture,
        })
        # Bypass real client — stub it with a mock whose create() returns our stream.
        agent._client = MagicMock()
        agent._resolved_max_tokens = 256  # avoid /models probe
        return agent

    def test_streaming_collects_records_and_stats(self):
        agent = self._build_agent(capture=True)
        agent._client.chat.completions.create.return_value = _fake_stream_chunks(3)
        task = BenchmarkTask(task_id="t1", problem="2+2?", ground_truth="4")
        resp = agent.solve(task)
        records = resp.metadata["logprob_records"]
        assert len(records) == 3
        assert records[0] == {
            "token_index": 0,
            "token": "tok0",
            "logprob": -0.5,
            "top_logprobs": [{"token": f"cand{i}", "logprob": -float(i)} for i in range(20)],
        }
        assert resp.token_entropy_stats["n_tokens"] == 3
        assert resp.token_entropy_stats["mean"] > 0

    def test_streaming_disabled_yields_no_records(self):
        agent = self._build_agent(capture=False)
        agent._client.chat.completions.create.return_value = _fake_stream_chunks(3)
        task = BenchmarkTask(task_id="t1", problem="2+2?", ground_truth="4")
        resp = agent.solve(task)
        assert resp.metadata["logprob_records"] == []
        assert resp.token_entropy_stats == {
            "mean": 0.0, "max": 0.0, "p50": 0.0, "p90": 0.0, "n_tokens": 0,
        }
        # Confirm request did NOT include logprobs
        call_kwargs = agent._client.chat.completions.create.call_args.kwargs
        assert "logprobs" not in call_kwargs

    def test_streaming_enabled_sends_logprobs_params(self):
        agent = self._build_agent(capture=True)
        agent._client.chat.completions.create.return_value = _fake_stream_chunks(1)
        task = BenchmarkTask(task_id="t1", problem="2+2?", ground_truth="4")
        agent.solve(task)
        call_kwargs = agent._client.chat.completions.create.call_args.kwargs
        assert call_kwargs.get("logprobs") is True
        assert call_kwargs.get("top_logprobs") == 20


class TestDirectLLMAgentNonStreamingLogprobs:
    def _build_agent(self):
        agent = DirectLLMAgent()
        agent.setup({
            "model": "m", "api_base": "b", "api_key": "EMPTY",
            "stream": False, "max_tokens": 256,
            "capture_logprobs": True,
        })
        agent._client = MagicMock()
        agent._resolved_max_tokens = 256
        return agent

    def test_non_streaming_collects_records(self):
        agent = self._build_agent()
        mock_response = SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content="answer", model_extra={}),
                finish_reason="stop",
                logprobs=SimpleNamespace(content=[_make_top(-0.1, i) for i in range(4)]),
            )],
            usage=SimpleNamespace(prompt_tokens=5, completion_tokens=4, total_tokens=9),
        )
        agent._client.chat.completions.create.return_value = mock_response
        task = BenchmarkTask(task_id="t1", problem="2+2?", ground_truth="4")
        resp = agent.solve(task)
        assert len(resp.metadata["logprob_records"]) == 4
        assert resp.token_entropy_stats["n_tokens"] == 4
