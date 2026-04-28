from __future__ import annotations

import json
from collections.abc import Iterable

import pytest

from alphadiana.agent.openclaw import OpenClawAgent, OpenClawRequestError
from alphadiana.benchmark.base import BenchmarkTask


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        body_text: str = "",
        sse_lines: Iterable[str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {"content-type": "application/json"}
        self._body_text = body_text
        self._sse_lines = list(sse_lines or [])

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def read(self) -> None:
        return None

    @property
    def text(self) -> str:
        return self._body_text

    def iter_lines(self):
        for line in self._sse_lines:
            yield line


class _FakeClient:
    def __init__(self, responses: list[_FakeResponse], **_: object) -> None:
        self._responses = responses

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def stream(self, method: str, url: str, headers: dict, json: dict):
        assert method == "POST"
        assert url.endswith("/chat/completions")
        assert isinstance(headers, dict)
        assert isinstance(json, dict)
        assert self._responses
        return self._responses.pop(0)


class _TimeoutAfterFirstLineResponse(_FakeResponse):
    def iter_lines(self):
        import httpx

        yield from self._sse_lines[:1]
        raise httpx.ReadTimeout("OpenClaw stream total timeout after 1.0s")


def _base_config(**overrides) -> dict:
    config = {
        "api_base": "http://127.0.0.1:18001/v1",
        "model": "Qwen/Qwen3.5-27B",
        "max_tokens": 128,
        "temperature": 0.0,
        "max_attempts": 2,
        "stream": False,
        "capture_logprobs": True,
        "top_logprobs": 20,
    }
    config.update(overrides)
    return config


def _task() -> BenchmarkTask:
    return BenchmarkTask(task_id="t1", problem="2+2?", ground_truth="4")


def _patch_network(monkeypatch: pytest.MonkeyPatch, responses: list[_FakeResponse]) -> None:
    import httpx

    monkeypatch.setattr(OpenClawAgent, "_health_check", lambda self, *_a, **_k: None)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: _FakeClient(responses, **kwargs))


def test_openclaw_request_payload_uses_shared_capture_config() -> None:
    agent = OpenClawAgent()
    agent.setup(_base_config(capture_logprobs=True, top_logprobs=20, enable_thinking=True, top_p=0.95))
    payload = agent._build_request_payload([{"role": "user", "content": "x"}])
    assert payload["logprobs"] is True
    assert payload["top_logprobs"] == 20
    assert payload["top_p"] == 0.95
    assert payload["max_tokens"] == 128
    assert payload["chat_template_kwargs"] == {"enable_thinking": True}

    agent_disabled = OpenClawAgent()
    agent_disabled.setup(_base_config(capture_logprobs=False))
    payload_disabled = agent_disabled._build_request_payload([{"role": "user", "content": "x"}])
    assert "logprobs" not in payload_disabled
    assert "top_logprobs" not in payload_disabled


def test_non_streaming_json_collects_logprob_records(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {
        "id": "resp-1",
        "choices": [
            {
                "message": {"content": "The answer is 4"},
                "logprobs": {
                    "content": [
                        {"token": "A", "logprob": -0.1, "top_logprobs": [{"token": "A", "logprob": -0.1}]},
                        {"token": "B", "logprob": -0.2, "top_logprobs": [{"token": "B", "logprob": -0.2}]},
                    ]
                },
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
    }
    _patch_network(monkeypatch, [_FakeResponse(body_text=json.dumps(body))])

    agent = OpenClawAgent()
    agent.setup(_base_config(stream=False))
    resp = agent.solve(_task())

    assert resp.metadata["logprobs_capture_status"] == "captured"
    assert resp.metadata["logprob_capture_harness"] == "openclaw"
    assert resp.token_entropy_stats["n_tokens"] == 2
    assert len(resp.metadata["logprob_records"]) == 2


def test_streaming_sse_collects_logprob_records(monkeypatch: pytest.MonkeyPatch) -> None:
    chunk1 = {
        "choices": [
            {
                "delta": {"content": "A"},
                "logprobs": {
                    "content": [
                        {"token": "A", "logprob": -0.1, "top_logprobs": [{"token": "A", "logprob": -0.1}]}
                    ]
                },
            }
        ]
    }
    chunk2 = {
        "choices": [
            {
                "delta": {"content": "B"},
                "logprobs": {
                    "content": [
                        {"token": "B", "logprob": -0.2, "top_logprobs": [{"token": "B", "logprob": -0.2}]}
                    ]
                },
            }
        ]
    }
    usage_chunk = {
        "choices": [{"delta": {}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
    }
    response = _FakeResponse(
        headers={"content-type": "text/event-stream"},
        sse_lines=[
            f"data: {json.dumps(chunk1)}",
            f"data: {json.dumps(chunk2)}",
            f"data: {json.dumps(usage_chunk)}",
            "data: [DONE]",
        ],
    )
    _patch_network(monkeypatch, [response])

    agent = OpenClawAgent()
    agent.setup(_base_config(stream=True))
    resp = agent.solve(_task())

    assert resp.metadata["logprobs_capture_status"] == "captured"
    assert [r["token_index"] for r in resp.metadata["logprob_records"]] == [0, 1]
    assert resp.token_entropy_stats["n_tokens"] == 2


def test_streaming_timeout_preserves_partial_output_and_logprobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunk = {
        "choices": [
            {
                "delta": {"content": "The answer is 4"},
                "logprobs": {
                    "content": [
                        {"token": "4", "logprob": -0.1, "top_logprobs": [{"token": "4", "logprob": -0.1}]}
                    ]
                },
            }
        ]
    }
    response = _TimeoutAfterFirstLineResponse(
        headers={"content-type": "text/event-stream"},
        sse_lines=[f"data: {json.dumps(chunk)}"],
    )
    _patch_network(monkeypatch, [response])

    agent = OpenClawAgent()
    agent.setup(_base_config(stream=True, max_attempts=1))

    response = agent.solve(_task())

    assert response.answer is None
    assert response.raw_output == "The answer is 4"
    assert response.finish_reason == "timeout"
    assert response.response_json["choices"][0]["finish_reason"] == "incomplete"
    assert response.response_json["openclaw_error_name"] == "OpenClawTimeout"
    assert response.metadata["received_done"] is False
    assert response.metadata["failure_reason"] == "timeout"
    assert response.metadata["openclaw_timeout_scored_zero"] is True
    assert response.metadata["openclaw_error_message"] == "OpenClaw stream total timeout after 1.0s"
    assert response.metadata["logprobs_capture_status"] == "captured"
    assert response.metadata["logprob_records"][0]["token"] == "4"
    assert response.token_entropy_stats["n_tokens"] == 1


def test_streaming_timeout_without_partial_output_scores_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _TimeoutAfterFirstLineResponse(
        headers={"content-type": "text/event-stream"},
        sse_lines=[],
    )
    _patch_network(monkeypatch, [response])

    agent = OpenClawAgent()
    agent.setup(_base_config(stream=True, max_attempts=1, stream_total_timeout=1))

    result = agent.solve(_task())

    assert result.answer is None
    assert result.raw_output == ""
    assert result.finish_reason == "timeout"
    assert result.metadata["received_done"] is False
    assert result.metadata["failure_reason"] == "timeout"
    assert result.metadata["openclaw_timeout_scored_zero"] is True
    assert result.metadata["openclaw_timeout_seconds"] == 1.0
    assert result.response_json["openclaw_error_name"] == "OpenClawTimeout"


def test_streaming_without_done_preserves_partial_as_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunk = {
        "choices": [
            {
                "delta": {"content": "The answer is 4"},
                "logprobs": {
                    "content": [
                        {"token": "4", "logprob": -0.1, "top_logprobs": [{"token": "4", "logprob": -0.1}]}
                    ]
                },
            }
        ]
    }
    response = _FakeResponse(
        headers={"content-type": "text/event-stream"},
        sse_lines=[f"data: {json.dumps(chunk)}"],
    )
    _patch_network(monkeypatch, [response])

    agent = OpenClawAgent()
    agent.setup(_base_config(stream=True, max_attempts=1))

    with pytest.raises(OpenClawRequestError) as exc_info:
        agent.solve(_task())

    partial = exc_info.value.partial_response
    assert partial is not None
    assert partial.raw_output == "The answer is 4"
    assert partial.metadata["received_done"] is False
    assert partial.metadata["failure_reason"] == "incomplete_stream"
    assert partial.metadata["retry_count"] == 1
    assert partial.metadata["logprobs_capture_status"] == "captured"
    assert partial.metadata["logprob_records"][0]["token"] == "4"


def test_retry_uses_only_final_successful_attempt_records(monkeypatch: pytest.MonkeyPatch) -> None:
    first_attempt = _FakeResponse(
        status_code=500,
        body_text=json.dumps({"error": {"message": "boom"}}),
    )
    second_attempt_body = {
        "id": "resp-2",
        "choices": [
            {
                "message": {"content": "4"},
                "logprobs": {
                    "content": [
                        {"token": "X", "logprob": -0.3, "top_logprobs": [{"token": "X", "logprob": -0.3}]}
                    ]
                },
            }
        ],
        "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
    }
    second_attempt = _FakeResponse(body_text=json.dumps(second_attempt_body))
    _patch_network(monkeypatch, [first_attempt, second_attempt])

    agent = OpenClawAgent()
    agent.setup(_base_config(stream=False, max_attempts=2))
    resp = agent.solve(_task())

    assert resp.metadata["logprobs_capture_status"] == "captured"
    assert len(resp.metadata["logprob_records"]) == 1
    assert resp.metadata["logprob_records"][0]["token"] == "X"
    assert resp.metadata["logprob_attempt_count"] == 2
    assert resp.metadata["logprob_source_attempt"] == 2


def test_empty_assistant_error_preserves_logprob_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunk = {
        "choices": [
            {
                "delta": {},
                "logprobs": {
                    "content": [
                        {
                            "token": "tool",
                            "logprob": -0.4,
                            "top_logprobs": [{"token": "tool", "logprob": -0.4}],
                        }
                    ]
                },
            }
        ]
    }
    response = _FakeResponse(
        headers={"content-type": "text/event-stream"},
        sse_lines=[f"data: {json.dumps(chunk)}", "data: [DONE]"],
    )
    _patch_network(monkeypatch, [response])

    agent = OpenClawAgent()
    agent.setup(_base_config(stream=True, max_attempts=1))

    with pytest.raises(OpenClawRequestError) as exc_info:
        agent.solve(_task())

    partial = exc_info.value.partial_response
    assert partial is not None
    assert partial.raw_output == ""
    assert partial.response_json["stream_status"]["received_done"] is True
    assert partial.metadata["failure_reason"] == "empty_response"
    assert partial.metadata["logprobs_capture_status"] == "captured"
    assert partial.metadata["logprob_source"] == "gateway_response"
    assert partial.metadata["logprob_records"][0]["token"] == "tool"
    assert partial.token_entropy_stats["n_tokens"] == 1


def test_success_after_empty_attempt_does_not_reuse_failed_logprobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("alphadiana.agent.openclaw.time.sleep", lambda _seconds: None)
    failed_chunk = {
        "choices": [
            {
                "delta": {},
                "logprobs": {
                    "content": [
                        {
                            "token": "failed",
                            "logprob": -0.4,
                            "top_logprobs": [{"token": "failed", "logprob": -0.4}],
                        }
                    ]
                },
            }
        ]
    }
    failed_attempt = _FakeResponse(
        headers={"content-type": "text/event-stream"},
        sse_lines=[f"data: {json.dumps(failed_chunk)}", "data: [DONE]"],
    )
    successful_attempt = _FakeResponse(
        body_text=json.dumps(
            {
                "id": "resp-success",
                "choices": [{"message": {"content": "The answer is 4"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        )
    )
    _patch_network(monkeypatch, [failed_attempt, successful_attempt])

    agent = OpenClawAgent()
    agent.setup(_base_config(stream=True, max_attempts=2))
    resp = agent.solve(_task())

    assert resp.raw_output == "The answer is 4"
    assert resp.metadata["logprobs_capture_status"] == "requested_missing"
    assert "logprob_records" not in resp.metadata
    assert resp.metadata["logprob_source_attempt"] == 0
    assert resp.token_entropy_stats["n_tokens"] == 0


def test_requested_missing_when_capture_enabled_without_provider_logprobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = {
        "id": "resp-3",
        "choices": [{"message": {"content": "The answer is 4"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    _patch_network(monkeypatch, [_FakeResponse(body_text=json.dumps(body))])

    agent = OpenClawAgent()
    agent.setup(_base_config(stream=False, capture_logprobs=True))
    resp = agent.solve(_task())

    assert resp.metadata["logprobs_capture_status"] == "requested_missing"
    assert "logprob_records" not in resp.metadata
