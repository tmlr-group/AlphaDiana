"""Opt-in native shell budget preserves Pi execution and timeout feedback."""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from alphadiana.harness._internal.deploy import bridge
from alphadiana.harness._internal.podman_agent import validate_config


def config(**overrides):
    return {
        "image": "sha256:" + "a" * 64,
        "model": "fixture",
        "api_base": "http://localhost:1/v1",
        "timeout": 20,
        "bash_timeout": 1,
        **overrides,
    }


@pytest.mark.parametrize("budget", [True, 0, -1, 1.5, "1", 20, 21])
def test_invalid_bash_timeout_rejected(budget):
    with pytest.raises(ValueError, match="bash_timeout"):
        validate_config(config(bash_timeout=budget))


def test_deepseek_rejects_bash_timeout():
    from alphadiana.harness.deepseek_harness.agent import DeepSeekHarnessAgent

    with pytest.raises(ValueError, match="bash_timeout is supported only by Pi"):
        DeepSeekHarnessAgent().setup(config())


def test_process_runtime_rejects_bash_timeout(tmp_path):
    import sys

    settings = config(
        runtime="process", pi_bin=sys.executable, process_root=str(tmp_path)
    )
    settings.pop("image")
    tmp_path.chmod(0o700)
    with pytest.raises(ValueError, match="requires the Pi Podman image"):
        validate_config(settings)


@pytest.mark.skipif(
    not Path("/usr/bin/timeout").is_file() or not shutil.which("bash"),
    reason="GNU timeout and bash required",
)
def test_native_shell_budget_kills_children_and_preserves_output(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    run = subprocess.run

    def native(*args, **kwargs):
        settings = json.loads((tmp_path / ".pi/agent/settings.json").read_text())
        shell = settings["shellPath"]
        assert "--extension" not in args[0]
        fast = run(
            [shell, "-c", "printf FAST"], capture_output=True, text=True
        )
        assert fast.returncode == 0 and fast.stdout == "FAST"
        started = time.monotonic()
        slow = run(
            [shell, "-c", "printf START; (sleep 2; touch leaked) & wait"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=8,
        )
        assert slow.returncode == 124
        assert slow.stdout == "START"
        assert "/usr/bin/timeout:" in slow.stderr and "TERM" in slow.stderr
        assert time.monotonic() - started < 5
        time.sleep(1.2)
        assert not (tmp_path / "leaked").exists()
        return SimpleNamespace(
            stderr="",
            stdout=json.dumps(
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "ok"}],
                        "stopReason": "stop",
                    },
                }
            ),
        )

    monkeypatch.setattr(bridge.subprocess, "run", native)
    validate_config(config())
    assert bridge.pi_run({"config": config(), "problem": "fixture"})["answer"] == "ok"


@pytest.mark.skipif(
    not os.environ.get("ALPHADIANA_PI_BIN"),
    reason="Set ALPHADIANA_PI_BIN to an installed Pi executable",
)
def test_installed_pi_continues_after_shell_timeout(tmp_path, monkeypatch):
    # Exercise the real bridge and CLI on Linux; container startup is tested
    # separately by test_native_runtime.py with an installed Podman image.
    from scripts.validation.native_model_fixture import model_server

    monkeypatch.setenv("HOME", str(tmp_path))
    with model_server(shell_command="printf START; sleep 3; printf TOO_LATE") as (
        url, requests
    ):
        result = bridge.pi_run(
            {
                "config": config(
                    pi_bin=os.environ["ALPHADIANA_PI_BIN"],
                    api_base=url.replace("host.containers.internal", "127.0.0.1"),
                    timeout=30,
                ),
                "problem": "Run the shell command and report its result.",
                "work_dir": str(tmp_path),
            }
        )
    assert result["answer"] == "0" and result["finish_reason"] == "stop"
    assert len(requests) == 2
    tool = next(m for m in requests[-1]["messages"] if m.get("role") == "tool")
    assert "START" in tool["content"] and "TOO_LATE" not in tool["content"]
    assert "124" in tool["content"]
    assert any(
        step.get("is_error")
        for step in result["trajectory"]
        if step["role"] == "tool_result"
    )
