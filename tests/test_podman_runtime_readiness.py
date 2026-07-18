"""Focused regression tests for the standard-reasoning Podman paths."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from alphadiana.engine.container_runtime.proxy_env import podman_proxy_env
from alphadiana.harness.opencode.agent import OpenCodeAgent
from alphadiana.harness.zeroclaw.runtime import ZeroClawPodmanRuntimeManager


ROOT = Path(__file__).resolve().parents[1]
BRIDGE_PATH = ROOT / "alphadiana/harness/zeroclaw/deploy/zeroclaw_bridge.py"


def test_zeroclaw_bridge_port_is_shared_by_process_and_podman_spec():
    manager = ZeroClawPodmanRuntimeManager(
        {
            "bridge_port": 18080,
            "podman_image": "localhost/zeroclaw:test",
        }
    )

    assert manager._runtime_env()["ZEROCLAW_BRIDGE_PORT"] == "18080"
    spec = manager._build_podman_spec()
    assert spec.exposed_port == 18080
    assert spec.ports == {18080: None}
    assert any("import scipy" in command for command in spec.install_commands)


@pytest.mark.parametrize("value", [0, 65536, "not-a-port"])
def test_zeroclaw_bridge_port_rejects_invalid_values_before_start(value):
    with pytest.raises(ValueError, match="bridge_port"):
        ZeroClawPodmanRuntimeManager({"bridge_port": value})


def test_standalone_bridge_reads_configured_port():
    env = os.environ.copy()
    env["ZEROCLAW_BRIDGE_PORT"] = "18080"
    code = (
        "import importlib.util; "
        f"s=importlib.util.spec_from_file_location('zc_bridge', {str(BRIDGE_PATH)!r}); "
        "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print(m.PORT)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert completed.stdout.strip() == "18080"


def test_podman_proxy_env_synchronizes_bypass_hosts():
    env = podman_proxy_env(
        {
            "HTTP_PROXY": "http://127.0.0.1:7890",
            "NO_PROXY": "example.test",
            "no_proxy": "legacy.test,example.test",
        },
        host_alias="host.containers.internal",
        no_proxy_hosts=["provider.internal"],
    )

    assert env["HTTP_PROXY"] == "http://host.containers.internal:7890"
    assert env["NO_PROXY"] == env["no_proxy"]
    assert env["NO_PROXY"].split(",") == [
        "example.test",
        "legacy.test",
        "host.containers.internal",
        "localhost",
        "127.0.0.1",
        "::1",
        "provider.internal",
    ]


def test_opencode_podman_preserves_remote_proxy_and_bypasses_provider():
    agent = OpenCodeAgent()
    agent.setup(
        {
            "controller_mode": "podman",
            "controller_network": "slirp4netns",
            "api_base": "https://provider.example/v1",
            "api_key": "test-key",
            "model_name": "test-model",
        }
    )
    env = agent._podman_controller_proxy_env(
        {
            "OPENAI_BASE_URL": "https://provider.example/v1",
            "HTTPS_PROXY": "http://127.0.0.1:7890",
        }
    )

    assert env["HTTPS_PROXY"] == "http://host.containers.internal:7890"
    assert "provider.example" in env["NO_PROXY"].split(",")
