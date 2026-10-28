"""SWE OpenCode must consume the declared native budget and version."""

import json
from types import SimpleNamespace

import pytest

from alphadiana.harness.opencode.container_runtime import (
    OpenCodeContainerRuntimeManager,
)


def test_native_container_config_preserves_limits_and_permissions():
    runtime = OpenCodeContainerRuntimeManager({
        "context_limit": 32768, "output_limit": 8192, "input_limit": 24576,
        "max_tokens": 8192, "permission": {"bash": "allow", "edit": "deny"},
    })
    uploads = []
    sandbox = SimpleNamespace(upload=lambda path, content: uploads.append(content))
    runtime._write_opencode_config(sandbox, model_name="fixture", api_base="http://local/v1", api_key="test")
    config = json.loads(uploads[0])
    assert config["provider"]["custom"]["models"]["fixture"]["limit"] == {
        "context": 32768, "output": 8192, "input": 24576,
    }
    assert config["permission"] == {"bash": "allow", "edit": "deny"}


def test_explicit_version_is_installed_and_observed():
    runtime = OpenCodeContainerRuntimeManager({"opencode_version": "1.3.2"})
    calls = []
    results = iter([
        SimpleNamespace(exit_code=1, stdout="", stderr=""),
        SimpleNamespace(exit_code=0, stdout="", stderr=""),
        SimpleNamespace(exit_code=0, stdout="1.3.2\n", stderr=""),
    ])
    def execute(command):
        calls.append(command)
        return next(results)
    runtime._install_opencode_if_needed(SimpleNamespace(execute=execute))
    assert "opencode-ai@1.3.2" in calls[1]


def test_existing_wrong_version_is_rejected():
    runtime = OpenCodeContainerRuntimeManager({"opencode_version": "1.3.2"})
    sandbox = SimpleNamespace(execute=lambda command: SimpleNamespace(exit_code=0, stdout="1.2.0"))
    with pytest.raises(RuntimeError, match="Expected OpenCode 1.3.2"):
        runtime._install_opencode_if_needed(sandbox)


def test_custom_install_command_is_preserved():
    runtime = OpenCodeContainerRuntimeManager({
        "opencode_version": "1.3.2", "install_opencode_command": "custom install",
    })
    assert runtime._install_opencode_command == "custom install"
