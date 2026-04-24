from __future__ import annotations

import json
from pathlib import Path

import pytest

from alphadiana.agent.zeroclaw import ZeroClawAgent, extract_zeroclaw_logprob_records
from alphadiana.benchmark.base import BenchmarkTask


def _runtime_payload(token: str, value: float) -> dict:
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


def test_zeroclaw_config_does_not_write_unsupported_provider_logprob_options() -> None:
    agent = ZeroClawAgent()
    agent.setup(
        {
            "model": "Qwen/Qwen3.5-27B",
            "api_base": "http://127.0.0.1:8011/v1",
            "api_key": "EMPTY",
            "capture_logprobs": True,
            "top_logprobs": 20,
        }
    )
    toml = agent._build_config_toml()
    assert "[model_providers.custom.options]" not in toml
    assert "logprobs = true" not in toml
    assert "top_logprobs = 20" not in toml


def test_zeroclaw_config_omits_provider_logprobs_when_disabled() -> None:
    agent = ZeroClawAgent()
    agent.setup(
        {
            "model": "Qwen/Qwen3.5-27B",
            "api_base": "http://127.0.0.1:8011/v1",
            "api_key": "EMPTY",
            "capture_logprobs": False,
        }
    )
    toml = agent._build_config_toml()
    assert "logprobs = true" not in toml
    assert "top_logprobs =" not in toml


def test_zeroclaw_parser_extracts_runtime_trace_logprobs() -> None:
    records = extract_zeroclaw_logprob_records(
        runtime_records=[{"payload": _runtime_payload("A", -0.1)}],
        runtime_trace="",
        raw_output="",
        raw_stderr="",
    )
    assert len(records) == 1
    assert records[0]["token_index"] == 0
    assert records[0]["token"] == "A"


def test_zeroclaw_parser_scans_stdout_and_stderr() -> None:
    records = extract_zeroclaw_logprob_records(
        runtime_records=[],
        runtime_trace="",
        raw_output=json.dumps({"nested": _runtime_payload("B", -0.2)}),
        raw_stderr=json.dumps({"nested": _runtime_payload("C", -0.3)}),
    )
    assert len(records) == 2
    assert [r["token"] for r in records] == ["B", "C"]


def test_zeroclaw_parser_ignores_malformed_json() -> None:
    records = extract_zeroclaw_logprob_records(
        runtime_records=[],
        runtime_trace='{"bad": true\n',
        raw_output="not json",
        raw_stderr="{bad",
    )
    assert records == []


def test_zeroclaw_capture_enables_runtime_trace_mode_all() -> None:
    agent = ZeroClawAgent()
    agent.setup(
        {
            "model": "Qwen/Qwen3.5-27B",
            "api_base": "http://127.0.0.1:8011/v1",
            "api_key": "EMPTY",
            "capture_logprobs": True,
            "runtime_trace_mode": "none",
        }
    )
    toml = agent._build_config_toml()
    assert 'runtime_trace_mode = "all"' in toml


def test_zeroclaw_requested_missing_when_no_provider_logprobs() -> None:
    agent = ZeroClawAgent()
    agent.setup(
        {
            "model": "Qwen/Qwen3.5-27B",
            "api_base": "http://127.0.0.1:8011/v1",
            "api_key": "EMPTY",
            "capture_logprobs": True,
        }
    )
    response = agent._build_cli_response(
        prompt="Problem",
        raw_output="$$\\boxed{4}$$",
        raw_stderr="",
        runtime_trace="",
        attachment_items=[],
        metadata={},
        wall_time_sec=0.1,
        system_prompt="",
        request_messages=[{"role": "user", "content": "Problem"}],
    )
    assert response.metadata["logprobs_capture_status"] == "requested_missing"
    assert "logprob_records" not in response.metadata


class _FailureSandbox:
    sandbox_id = "sandbox-failure"
    name = "rock"

    def __init__(self) -> None:
        self.files: dict[str, str] = {}

    def execute(self, command: str):
        if command == "pwd":
            return type("Result", (), {"exit_code": 0, "stdout": "/sandbox/work\n", "stderr": ""})()
        if command.startswith("mkdir -p ") or command.startswith("chmod 600 "):
            return type("Result", (), {"exit_code": 0, "stdout": "", "stderr": ""})()
        if "command -v zeroclaw" in command:
            return type("Result", (), {"exit_code": 0, "stdout": "/usr/local/bin/zeroclaw\n", "stderr": ""})()
        if "zeroclaw --version" in command:
            return type("Result", (), {"exit_code": 0, "stdout": "zeroclaw 0.6.9\n", "stderr": ""})()
        if "zeroclaw agent -m" in command:
            stdout_path = command.split("> ", 1)[1].split(" 2> ", 1)[0]
            stderr_path = command.split(" 2> ", 1)[1]
            self.files[stdout_path] = ""
            self.files[stderr_path] = "Error: provider failed"
            task_path = next((path for path in self.files if path.endswith("/task.txt")), "")
            trace_path = str(Path(task_path).parent / "state" / "runtime-trace.jsonl")
            self.files[trace_path] = json.dumps(_runtime_payload("Z", -0.5))
            return type("Result", (), {"exit_code": 1, "stdout": "", "stderr": "provider failure"})()
        if command.startswith("bash -lc ") and " && cat " in command:
            inner = command[len("bash -lc "):].strip()
            if inner and inner[0] in {"'", '"'} and inner[-1] == inner[0]:
                inner = inner[1:-1]
            filename = inner.split(" && cat ", 1)[1].split(" || true", 1)[0]
            return type("Result", (), {"exit_code": 0, "stdout": self.files.get(filename, ""), "stderr": ""})()
        if command.startswith("cat "):
            filename = command[4:]
            return type("Result", (), {"exit_code": 0, "stdout": self.files.get(filename, ""), "stderr": ""})()
        return type("Result", (), {"exit_code": 0, "stdout": "", "stderr": ""})()

    def upload(self, filename: str, content: bytes) -> None:
        self.files[filename] = content.decode("latin1")

    def read_text(self, filename: str) -> str:
        return self.files.get(filename, "")

    def metadata(self) -> dict:
        return {"sandbox_id": self.sandbox_id}


def test_partial_failure_preserves_logprob_records() -> None:
    agent = ZeroClawAgent()
    agent.setup(
        {
            "model": "Qwen/Qwen3.5-27B",
            "api_base": "http://127.0.0.1:8011/v1",
            "api_key": "EMPTY",
            "capture_logprobs": True,
            "runtime_trace_mode": "all",
        }
    )
    task = BenchmarkTask(task_id="zf", problem="2+2?", ground_truth="4", metadata={"execution_id": "zf-1"})
    sandbox = _FailureSandbox()

    with pytest.raises(RuntimeError) as excinfo:
        agent.solve(task, sandbox=sandbox)

    partial = getattr(excinfo.value, "partial_response", None)
    assert partial is not None
    assert partial.metadata["failure_reason"] in {"provider_error", "cli_error", "proxy_error"}
    assert partial.metadata["logprobs_capture_status"] == "captured"
    assert partial.metadata["logprob_records"][0]["token"] == "Z"
