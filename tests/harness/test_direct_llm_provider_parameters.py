"""Providers without sampling controls must not receive rejected fields."""

from types import SimpleNamespace

from alphadiana.benchmarks.base import BenchmarkTask
from alphadiana.harness.direct_llm.agent import DirectLLMAgent


def test_explicit_null_temperature_omits_sampling_controls(monkeypatch):
    observed = {}

    def create(**kwargs):
        observed.update(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="\\boxed{2}", model_extra={}),
                    finish_reason="stop",
                    logprobs=None,
                )
            ],
            usage=None,
        )

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    monkeypatch.setattr(DirectLLMAgent, "_build_client", lambda self: client)
    agent = DirectLLMAgent()
    agent.setup(
        {
            "model": "provider-model",
            "temperature": None,
            "top_p": None,
            "max_completion_tokens": 131072,
            "stream": False,
            "capture_logprobs": False,
        }
    )
    response = agent.solve(
        BenchmarkTask(task_id="small", problem="1+1?", ground_truth="2")
    )
    assert response.answer == "2"
    assert "temperature" not in observed
    assert "top_p" not in observed
    assert "max_tokens" not in observed
    assert "logprobs" not in observed
    assert observed["max_completion_tokens"] == 131072
