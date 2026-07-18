"""Focused regression tests for the standard-reasoning Podman paths."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

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
