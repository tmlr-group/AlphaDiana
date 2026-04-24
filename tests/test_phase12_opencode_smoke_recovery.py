from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from alphadiana.agent.opencode import OpenCodeAgent, extract_opencode_logprob_records
from alphadiana.benchmark.base import BenchmarkTask

RUN_ID = "phase11_opencode_gpqa_diamond_qwen35_27b_logprobs_smoke"
TASK_PATH = Path(f"results/{RUN_ID}/tasks/gpqa_0.json")


def _load_task_record() -> dict:
    payload = json.loads(TASK_PATH.read_text(encoding="utf-8"))
    return payload[0] if isinstance(payload, list) else payload


def test_phase11_opencode_requested_missing_fixture() -> None:
    agent = OpenCodeAgent()
    agent.setup(_base_agent_config())
    process = MagicMock()
    process.communicate.return_value = (
        "\n".join(
            [
                json.dumps({"type": "session", "sessionID": "session-missing"}),
                json.dumps({"type": "assistant", "message": {"content": "Done.\\n\\boxed{4}"}}),
            ]
        ),
        "",
    )
    process.returncode = 0
    task = BenchmarkTask(task_id="t-missing", problem="2+2?", ground_truth="4")
    mock_proxy = MagicMock()
    mock_proxy.proxy_url = "http://127.0.0.1:19999"
    mock_proxy.captured_records.return_value = []

    with patch("subprocess.Popen", return_value=process):
        with patch("alphadiana.agent.opencode.LogprobCaptureProxy", return_value=mock_proxy):
            result = agent._solve_cli(task)

    assert result.metadata["logprobs_capture_status"] == "requested_missing"
    assert result.token_entropy_stats["n_tokens"] == 0
    assert result.metadata["logprob_probe_proxy_count"] == 0


def _logprob_payload(token: str, value: float) -> dict:
    return {
        "choices": [
            {
                "logprobs": {
                    "content": [
                        {
                            "token": token,
                            "logprob": value,
                            "top_logprobs": [{"token": token, "logprob": value}],
                        }
                    ]
                }
            }
        ]
    }


def test_extract_opencode_logprobs_from_envelope_variants() -> None:
    events = [
        {
            "type": "trace",
            "response": {
                "provider_response": {
                    "payload": json.dumps({"data": _logprob_payload("alpha", -0.1)})
                }
            },
        }
    ]
    session_trace = json.dumps(
        {"message": {"part": {"payload": {"data": _logprob_payload("beta", -0.2)}}}}
    )
    stdout = json.dumps(
        {"part": {"response": {"provider_response": {"data": _logprob_payload("gamma", -0.3)}}}}
    )

    records = extract_opencode_logprob_records(
        events=events,
        session_trace=session_trace,
        stdout=stdout,
    )

    assert [record["token"] for record in records] == ["alpha", "beta", "gamma"]
    assert [record["token_index"] for record in records] == [0, 1, 2]


def _base_agent_config(**overrides) -> dict:
    config = {
        "model_name": "Qwen/Qwen3.5-27B",
        "api_base": "http://127.0.0.1:8011/v1",
        "api_key": "EMPTY",
        "timeout": 5,
        "capture_logprobs": True,
        "top_logprobs": 5,
    }
    config.update(overrides)
    return config


def test_fetch_logprobs_direct_returns_records_from_httpx_payload() -> None:
    agent = OpenCodeAgent()
    agent.setup(_base_agent_config())
    process = MagicMock()
    process.communicate.return_value = (
        json.dumps({"type": "assistant", "message": {"content": "Done.\\n\\boxed{4}"}}),
        "",
    )
    process.returncode = 0
    task = BenchmarkTask(task_id="t-proxy-records", problem="2+2?", ground_truth="4")
    mock_proxy = MagicMock()
    mock_proxy.proxy_url = "http://127.0.0.1:19999"
    mock_proxy.captured_records.return_value = [
        {"token": "A", "logprob": -0.1, "top_logprobs": [], "token_index": 0},
        {"token": "B", "logprob": -0.2, "top_logprobs": [], "token_index": 1},
        {"token": "C", "logprob": -0.3, "top_logprobs": [], "token_index": 2},
    ]

    with patch("subprocess.Popen", return_value=process):
        with patch("alphadiana.agent.opencode.LogprobCaptureProxy", return_value=mock_proxy):
            result = agent._solve_cli(task)

    assert result.metadata["logprob_probe_proxy_count"] == 3
    assert result.metadata["logprob_probe_record_count"] == 3
    assert result.token_entropy_stats["n_tokens"] == 3


def test_solve_cli_uses_direct_api_fallback_when_stream_has_no_logprobs() -> None:
    agent = OpenCodeAgent()
    agent.setup(_base_agent_config())
    process = MagicMock()
    process.communicate.return_value = (
        "\n".join(
            [
                json.dumps({"type": "session", "sessionID": "session-fallback"}),
                json.dumps({"type": "tool_use", "message": "Inspecting"}),
                json.dumps({"type": "text", "text": "Working"}),
                json.dumps({"type": "trace", "payload": {"foo": "bar"}}),
                json.dumps({"type": "trace", "response": {"data": "noop"}}),
                json.dumps({"type": "assistant", "message": {"content": "Done.\\n\\boxed{4}"}}),
                json.dumps({"type": "final", "status": "ok"}),
            ]
        ),
        "",
    )
    process.returncode = 0

    task = BenchmarkTask(task_id="t-fallback", problem="2+2?", ground_truth="4")
    mock_proxy = MagicMock()
    mock_proxy.proxy_url = "http://127.0.0.1:19999"
    mock_proxy.captured_records.return_value = [
        {
            "token": "A",
            "logprob": -0.1,
            "top_logprobs": [],
            "token_index": 0,
        }
    ]

    with patch("subprocess.Popen", return_value=process):
        with patch("alphadiana.agent.opencode.LogprobCaptureProxy", return_value=mock_proxy):
            result = agent._solve_cli(task)

    assert result.metadata["logprob_probe_proxy_count"] > 0
    assert result.metadata["logprobs_capture_status"] == "captured"
