"""Images survive native transport; failed native output never becomes a success."""

import base64
import json

import httpx
import pytest

from alphadiana.benchmarks.base import BenchmarkTask
from alphadiana.harness.openclaw.agent import OpenClawAgent, OpenClawRequestError
from alphadiana.harness.openclaw.responses import build_responses_payload, has_images
from alphadiana.harness.openclaw.runtime import OpenClawRuntimeManager


def test_runtime_enables_native_image_endpoint_without_changing_chat():
    manager = OpenClawRuntimeManager({})
    config = manager._build_openclaw_config(
        {"gateway": {"http": {"endpoints": {"chatCompletions": {"enabled": True}}}}}
    )
    assert config["gateway"]["http"]["endpoints"] == {
        "responses": {"enabled": True},
        "chatCompletions": {"enabled": True},
    }
    disabled = OpenClawRuntimeManager({"responses_enabled": False})
    assert "http" not in disabled._build_openclaw_config({})["gateway"]


def test_multiple_image_sources_and_text_are_preserved():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Compare"},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,YQ=="},
                },
                {
                    "type": "image_url",
                    "image_url": {"url": "https://example.org/image.png"},
                },
            ],
        }
    ]
    assert has_images(messages)
    assert not has_images([{"role": "user", "content": "plain text"}])
    payload = build_responses_payload(
        {
            "messages": messages,
            "model": "native",
            "stream": True,
            "max_tokens": 99,
            "temperature": 0.2,
            "top_p": 0.8,
        }
    )
    assert payload["max_output_tokens"] == 99 and payload["stream"] is True
    assert payload["temperature"] == 0.2 and payload["top_p"] == 0.8
    parts = payload["input"][0]["content"]
    assert parts[0] == {"type": "input_text", "text": "Compare"}
    assert parts[1]["source"] == {
        "type": "base64",
        "media_type": "image/png",
        "data": "YQ==",
    }
    assert parts[2]["source"] == {"type": "url", "url": "https://example.org/image.png"}
    assert messages[0]["content"][1]["type"] == "image_url"


def test_default_sampling_omits_null_native_response_parameters():
    chat = {
        "model": "native", "stream": False,
        "messages": [{"role": "user", "content": "fixture"}],
        "temperature": None, "top_p": None,
    }
    payload = build_responses_payload(chat)
    assert "temperature" not in payload and "top_p" not in payload
    assert payload["stream"] is False
    assert chat["temperature"] is None
    payload = build_responses_payload({**chat, "temperature": 0.0, "top_p": 1.0})
    assert payload["temperature"] == 0.0 and payload["top_p"] == 1.0


@pytest.mark.parametrize(
    "stream,status",
    [(True, "completed"), (False, "completed"), (True, "failed"), (True, "missing")],
)
def test_agent_image_transport_and_terminal_status(monkeypatch, stream, status):
    native = {
        "id": "resp_1",
        "model": "native",
        "status": status,
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Answer: B"}],
            }
        ],
        "usage": {"input_tokens": 11, "output_tokens": 3, "total_tokens": 14},
    }
    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        assert request.url.path == "/v1/responses"
        part = body["input"][0]["content"][1]
        assert base64.b64decode(part["source"]["data"]) == b"image-bytes"
        if not stream:
            return httpx.Response(200, json=native)
        events = [{"type": "response.output_text.delta", "delta": "Answer: B"}]
        if status != "missing":
            events.append({"type": "response.completed", "response": native})
        data = (
            "".join("data: " + json.dumps(e) + "\n\n" for e in events)
            + "data: [DONE]\n\n"
        )
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, text=data
        )

    client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: client(transport=httpx.MockTransport(handle), **kwargs),
    )
    agent = OpenClawAgent()
    agent.setup(
        {
            "api_base": "http://fixture/v1",
            "max_tokens": 99,
            "max_attempts": 1,
            "stream": stream,
        }
    )
    agent._health_checked = True
    monkeypatch.setattr(agent, "_retrieve_trajectory_sync", lambda **kwargs: [])
    task = BenchmarkTask(
        task_id="vision",
        problem="Select the letter.",
        ground_truth="B",
        attachments={"image_1": b"image-bytes", "image_1_mime": b"image/png"},
    )
    if status == "completed":
        result = agent.solve(task)
        assert result.raw_output == "Answer: B"
        assert result.response_json["native_response"] == native
        assert result.token_usage["completion_tokens"] == 3
    else:
        with pytest.raises(OpenClawRequestError, match="completed native response"):
            agent.solve(task)
    assert len(requests) == 1
