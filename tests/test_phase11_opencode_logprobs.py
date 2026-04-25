from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from alphadiana.agent.opencode import OpenCodeAgent, extract_opencode_logprob_records
from alphadiana.agent.opencode_container_runtime import OpenCodeContainerRuntimeManager
from alphadiana.agent.logprob_capture import extract_openai_logprob_records
from alphadiana.benchmark.base import BenchmarkTask


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


def test_opencode_parser_extracts_openai_logprob_records() -> None:
    records = extract_opencode_logprob_records(
        events=[
            {"type": "trace", "payload": _logprob_payload("A", -0.1)},
            {"type": "trace", "payload": _logprob_payload("B", -0.2)},
        ],
        session_trace="",
        stdout="",
    )
    assert len(records) == 2
    assert records[0]["token_index"] == 0
    assert records[0]["token"] == "A"
    assert records[1]["token_index"] == 1
    assert records[1]["token"] == "B"
    token_indices = [record["token_index"] for record in records]
    assert token_indices == sorted(token_indices)


def test_extract_chat_completion_logprobs() -> None:
    payload = {
        "choices": [
            {
                "logprobs": {
                    "content": [{"token": "X", "logprob": -0.5, "top_logprobs": []}]
                }
            }
        ]
    }
    records, next_idx = extract_openai_logprob_records(payload, start_index=0)
    assert len(records) == 1
    assert records[0]["token"] == "X"
    assert records[0]["token_index"] == 0
    assert next_idx == 1


def test_opencode_parser_scans_session_trace() -> None:
    trace = "\n".join([
        json.dumps({"type": "assistant", "message": {"content": "done"}}),
        json.dumps({"nested": _logprob_payload("B", -0.2)}),
    ])
    records = extract_opencode_logprob_records(events=[], session_trace=trace, stdout="")
    assert len(records) == 1
    assert records[0]["token"] == "B"


def test_opencode_parser_ignores_malformed_json() -> None:
    records = extract_opencode_logprob_records(
        events=[],
        session_trace='{"bad": true\nnot-json',
        stdout='{"also_bad":\n',
    )
    assert records == []


def _base_agent_config(**overrides) -> dict:
    config = {
        "model_name": "Qwen/Qwen3.5-27B",
        "api_base": "http://127.0.0.1:8011/v1",
        "api_key": "EMPTY",
        "timeout": 5,
        "capture_logprobs": True,
        "top_logprobs": 20,
    }
    config.update(overrides)
    return config


def test_opencode_host_provider_options_request_logprobs_when_enabled() -> None:
    agent = OpenCodeAgent()
    agent.setup(
        _base_agent_config(
            capture_logprobs=True,
            top_logprobs=20,
            temperature=0.0,
            top_p=0.95,
            max_tokens=131072,
            enable_thinking=True,
        )
    )
    process = MagicMock()
    process.communicate.return_value = (
        json.dumps({"type": "assistant", "message": {"content": "Done.\\n\\boxed{4}"}}),
        "",
    )
    process.returncode = 0
    captured_config: dict = {}

    def _fake_popen(cmd, **kwargs):
        config_path = Path(kwargs["env"]["XDG_CONFIG_HOME"]) / "opencode" / "opencode.json"
        captured_config.update(json.loads(config_path.read_text(encoding="utf-8")))
        return process

    mock_proxy = MagicMock()
    mock_proxy.proxy_url = "http://127.0.0.1:19999"
    mock_proxy.captured_records.return_value = []

    with patch("subprocess.Popen", side_effect=_fake_popen):
        with patch("alphadiana.agent.opencode.LogprobCaptureProxy", return_value=mock_proxy) as proxy_cls:
            response = agent.solve(BenchmarkTask(task_id="t1", problem="2+2?", ground_truth="4"))

    options = captured_config["provider"]["custom"]["options"]
    assert options["logprobs"] is True
    assert options["top_logprobs"] == 20
    assert options["temperature"] == 0.0
    assert options["top_p"] == 0.95
    assert options["max_tokens"] == 131072
    assert options["chat_template_kwargs"] == {"enable_thinking": True}
    assert proxy_cls.call_args.kwargs["request_overrides"] == {
        "temperature": 0.0,
        "top_p": 0.95,
        "max_tokens": 131072,
        "chat_template_kwargs": {"enable_thinking": True},
    }
    assert response.metadata["logprobs_capture_status"] == "requested_missing"
    assert response.metadata["logprob_proxy_request_overrides"] == {
        "temperature": 0.0,
        "top_p": 0.95,
        "max_tokens": 131072,
        "chat_template_kwargs": {"enable_thinking": True},
    }


def test_opencode_timeout_preserves_proxy_partial_output() -> None:
    agent = OpenCodeAgent()
    agent.setup(_base_agent_config(timeout=1, capture_logprobs=True, controller_mode="host"))

    timeout_event = "\n".join(
        [
            json.dumps({"type": "step_start", "timestamp": 1, "sessionID": "ses_test"}),
            json.dumps(
                {
                    "type": "error",
                    "timestamp": 1801,
                    "sessionID": "ses_test",
                    "error": {"name": "UnknownError", "data": {"message": "The operation timed out."}},
                }
            ),
        ]
    )
    process = MagicMock()
    process.pid = 12345
    process.communicate.side_effect = [
        subprocess.TimeoutExpired(cmd=["opencode"], timeout=1),
        (timeout_event, ""),
    ]

    def _fake_popen(*args, **kwargs):
        return process

    mock_proxy = MagicMock()
    mock_proxy.proxy_url = "http://127.0.0.1:19999"
    mock_proxy.captured_records.return_value = [
        {
            "token_index": 0,
            "token": "partial ",
            "logprob": -0.1,
            "top_logprobs": [{"token": "partial ", "logprob": -0.1}],
        },
        {
            "token_index": 1,
            "token": "answer",
            "logprob": -0.2,
            "top_logprobs": [{"token": "answer", "logprob": -0.2}],
        },
    ]

    with patch("subprocess.Popen", side_effect=_fake_popen), patch("os.killpg"):
        with patch("alphadiana.agent.opencode.LogprobCaptureProxy", return_value=mock_proxy):
            with pytest.raises(RuntimeError, match="timed out") as exc_info:
                agent.solve(BenchmarkTask(task_id="timeout", problem="2+2?", ground_truth="4"))

    response = exc_info.value.partial_response
    assert response.raw_output == "partial answer"
    assert getattr(exc_info.value, "response_body") == "partial answer"
    assert response.metadata["logprobs_capture_status"] == "captured"
    assert response.metadata["partial_output_source"] == "logprob_records"
    assert response.metadata["partial_output_used_for_raw_output"] is True
    assert response.metadata["partial_output_chars"] == len("partial answer")
    assert response.workspace_file_contents["opencode_output.jsonl"] == timeout_event
    assert response.workspace_file_contents["opencode_partial_output.txt"] == "partial answer"
    assert response.artifact_manifest["files"]["partial_output"] == "opencode_partial_output.txt"


def test_opencode_host_provider_options_omit_logprobs_when_disabled() -> None:
    agent = OpenCodeAgent()
    agent.setup(_base_agent_config(capture_logprobs=False))
    process = MagicMock()
    process.communicate.return_value = (
        json.dumps({"type": "assistant", "message": {"content": "Done.\\n\\boxed{4}"}}),
        "",
    )
    process.returncode = 0
    captured_config: dict = {}

    def _fake_popen(cmd, **kwargs):
        config_path = Path(kwargs["env"]["XDG_CONFIG_HOME"]) / "opencode" / "opencode.json"
        captured_config.update(json.loads(config_path.read_text(encoding="utf-8")))
        return process

    with patch("subprocess.Popen", side_effect=_fake_popen):
        response = agent.solve(BenchmarkTask(task_id="t2", problem="2+2?", ground_truth="4"))

    options = captured_config["provider"]["custom"]["options"]
    assert "logprobs" not in options
    assert "top_logprobs" not in options
    assert response.metadata["logprobs_capture_status"] == "not_requested"


def test_opencode_container_provider_config_requests_logprobs() -> None:
    mgr = OpenCodeContainerRuntimeManager(
        {
            "api_base": "http://127.0.0.1:8011/v1",
            "api_key": "EMPTY",
            "capture_logprobs": True,
            "top_logprobs": 20,
            "temperature": 0.0,
            "top_p": 0.95,
            "max_tokens": 131072,
            "enable_thinking": True,
        }
    )

    class _Sandbox:
        def __init__(self) -> None:
            self.uploaded = {}

        def upload(self, path: str, content: bytes) -> None:
            self.uploaded[path] = json.loads(content.decode("utf-8"))

    sandbox = _Sandbox()
    mgr._write_opencode_config(sandbox, model_name="Qwen/Qwen3.5-27B")
    options = sandbox.uploaded["/tmp/opencode-xdg/opencode/opencode.json"]["provider"]["custom"]["options"]
    assert options["logprobs"] is True
    assert options["top_logprobs"] == 20
    assert options["temperature"] == 0.0
    assert options["top_p"] == 0.95
    assert options["max_tokens"] == 131072
    assert options["chat_template_kwargs"] == {"enable_thinking": True}


def test_opencode_container_runtime_uses_proxy_records(monkeypatch) -> None:
    mgr = OpenCodeContainerRuntimeManager(
        {
            "api_base": "http://127.0.0.1:8011/v1",
            "api_key": "EMPTY",
            "capture_logprobs": True,
            "top_logprobs": 7,
            "temperature": 0.0,
            "top_p": 0.95,
            "max_tokens": 4096,
            "timeout": 5,
            "logprob_proxy_advertise_host": "127.0.0.1",
        }
    )
    mgr._installed_sandboxes.add("sandbox-1")

    class _Result:
        def __init__(self, stdout: str = "", stderr: str = "", exit_code: int = 0) -> None:
            self.stdout = stdout
            self.stderr = stderr
            self.exit_code = exit_code

    class _Sandbox:
        sandbox_id = "sandbox-1"

        def __init__(self) -> None:
            self.uploaded = {}
            self.commands = []

        def metadata(self) -> dict:
            return {"repo_workdir": "/repo"}

        def upload(self, path: str, content: bytes) -> None:
            self.uploaded[path] = json.loads(content.decode("utf-8"))

        def execute(self, command: str) -> _Result:
            self.commands.append(command)
            if "git diff HEAD" in command:
                return _Result(stdout="diff --git a/a.py b/a.py\n")
            return _Result(
                stdout=json.dumps({"type": "assistant", "message": {"content": "done"}}),
            )

    proxy_instances = []

    class _FakeProxy:
        def __init__(self, upstream, top_logprobs, **kwargs):
            self.upstream = upstream
            self.top_logprobs = top_logprobs
            self.kwargs = kwargs
            self.proxy_url = "http://127.0.0.1:19999"
            self.stopped = False
            proxy_instances.append(self)

        def start(self):
            pass

        def drain_records(self):
            return [
                {
                    "token_index": 0,
                    "token": "C",
                    "logprob": -0.3,
                    "top_logprobs": [{"token": "C", "logprob": -0.3}],
                }
            ]

        def stop(self):
            self.stopped = True

    monkeypatch.setattr(
        "alphadiana.agent.opencode_container_runtime.LogprobCaptureProxy",
        _FakeProxy,
    )

    sandbox = _Sandbox()
    result = mgr.run_task(
        sandbox,
        "problem",
        model_name="Qwen/Qwen3.5-27B",
        task_id="tc",
    )

    options = sandbox.uploaded["/tmp/opencode-xdg/opencode/opencode.json"]["provider"]["custom"]["options"]
    assert options["baseURL"] == "http://127.0.0.1:19999/v1"
    assert options["logprobs"] is True
    assert options["top_logprobs"] == 7
    assert options["max_tokens"] == 4096
    assert "export OPENAI_BASE_URL=http://127.0.0.1:19999/v1" in sandbox.commands[0]
    assert result["logprob_records"][0]["token"] == "C"
    assert result["logprob_proxy_metadata"]["logprob_proxy_upstream"] == "http://127.0.0.1:8011"
    assert proxy_instances[0].kwargs["request_overrides"]["max_tokens"] == 4096
    assert proxy_instances[0].stopped is True


def test_opencode_container_response_finalizes_captured_logprobs() -> None:
    agent = OpenCodeAgent()
    agent.setup(
        {
            "runtime": "swebench_container",
            "model_name": "Qwen/Qwen3.5-27B",
            "api_base": "http://127.0.0.1:8011/v1",
            "api_key": "EMPTY",
            "capture_logprobs": True,
            "top_logprobs": 20,
            "timeout": 5,
        }
    )
    runtime_manager = MagicMock()
    runtime_manager.run_task.return_value = {
        "stdout": json.dumps({"type": "assistant", "message": {"content": "Done.\\n\\boxed{4}"}}),
        "stderr": "",
        "exit_code": 0,
        "patch": "",
        "logprob_records": [
            {
                "token_index": 0,
                "token": "C",
                "logprob": -0.3,
                "top_logprobs": [{"token": "C", "logprob": -0.3}],
            }
        ],
        "logprob_proxy_metadata": {
            "logprob_proxy_enabled": True,
            "logprob_proxy_url": "http://127.0.0.1:19999/v1",
            "logprob_proxy_upstream": "http://127.0.0.1:8011",
        },
    }
    runtime_manager.collect_artifacts.return_value = {
        "artifact_manifest": {},
        "workspace_file_contents": {},
    }
    agent._runtime_manager = runtime_manager

    response = agent.solve(BenchmarkTask(task_id="tc", problem="2+2?", ground_truth="4"), sandbox=MagicMock())
    assert response.metadata["logprobs_capture_status"] == "captured"
    assert response.metadata["logprob_capture_harness"] == "opencode"
    assert response.metadata["logprob_source"] == "provider_proxy"
    assert response.metadata["logprob_probe_proxy_count"] == 1
    assert response.token_entropy_stats["n_tokens"] == 1
