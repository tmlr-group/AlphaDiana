"""Focused regression tests for the standard-reasoning Podman paths."""

from __future__ import annotations

import os
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from alphadiana.engine.container_runtime.agent_runtime import (
    PodmanAgentRuntime,
    PodmanAgentSpec,
)
from alphadiana.engine.container_runtime.podman_cli import PodmanResult
from alphadiana.engine.container_runtime.proxy_env import podman_proxy_env
from alphadiana.harness.opencode.agent import OpenCodeAgent
from alphadiana.harness.openclaw.runtime import OpenClawPodmanRuntimeManager
from alphadiana.harness.zeroclaw.runtime import ZeroClawPodmanRuntimeManager
from alphadiana.utils.openclaw_security import is_weak_openclaw_gateway_token


ROOT = Path(__file__).resolve().parents[1]
BRIDGE_PATH = ROOT / "alphadiana/harness/zeroclaw/deploy/zeroclaw_bridge.py"
SCALE_SCRIPT = ROOT / "scripts/run_podman_scale_readiness.sh"
AUDIT_PATH = ROOT / "scripts/audit_podman_scale_readiness.py"


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


class _FakePodmanRuntime:
    def __init__(self):
        self.run_kwargs = {}

    def _run(self, *args, **kwargs):
        return PodmanResult(("podman", "ps"), 0, "", "")

    def run(self, image, **kwargs):
        self.run_kwargs = {"image": image, **kwargs}
        return PodmanResult(("podman", "run"), 0, "container-123\n", "")

    def inspect(self, container_id, **kwargs):
        payload = (
            '[{"Image":"sha256:abc","ImageName":"localhost/test:latest",'
            '"Config":{"Entrypoint":"/bin/sh","Cmd":["-c","sleep infinity"]}}]'
        )
        return PodmanResult(("podman", "inspect"), 0, payload, "")

    def logs(self, container_id, **kwargs):
        return PodmanResult(("podman", "logs"), 0, "", "")


def test_agent_runtime_overrides_image_entrypoint_and_records_identity():
    fake = _FakePodmanRuntime()
    runtime = PodmanAgentRuntime(runtime=fake)
    result = runtime.start(
        PodmanAgentSpec(
            adapter_name="test-podman",
            image="localhost/test:latest",
            run_command="",
            exposed_port=None,
        )
    )

    assert fake.run_kwargs["entrypoint"] == "/bin/sh"
    assert fake.run_kwargs["command"] == ["-c", "sleep infinity"]
    assert result.metadata["runtime_image_id"] == "sha256:abc"
    assert result.metadata["runtime_entrypoint"] == "/bin/sh"


def test_scale_runner_rejects_weak_openclaw_token():
    completed = subprocess.run(
        [
            "bash",
            "-c",
            f"source {str(SCALE_SCRIPT)!r}; unset OPENCLAW_GATEWAY_TOKEN; "
            "require_openclaw_gateway_token",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "Missing or weak OPENCLAW_GATEWAY_TOKEN" in completed.stderr


def test_scale_runner_preserves_failed_command_exit_code(tmp_path):
    command = f"""
source {str(SCALE_SCRIPT)!r}
ROOT_DIR={str(tmp_path)!r}
STATUS_FILE="$ROOT_DIR/status.tsv"
COMMAND_TIMEOUT_SECONDS=1
mkdir -p "$ROOT_DIR/logs"
printf 'scope\\trun_id\\tconfig\\texit_code\\tlog_path\\n' > "$STATUS_FILE"
timeout() {{ return 7; }}
run_config pilot fake.yaml unit_failure
"""
    completed = subprocess.run(
        ["bash", "-c", command],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 7
    assert "\t7\t" in (tmp_path / "status.tsv").read_text(encoding="utf-8")


def test_scale_audit_rejects_non_scored_task(tmp_path):
    spec = importlib.util.spec_from_file_location("podman_scale_audit", AUDIT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    log_path = tmp_path / "run.log"
    log_path.write_text("complete", encoding="utf-8")
    artifact_path = tmp_path / "results/run/run/artifacts/sample/trajectory.json"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("{}", encoding="utf-8")
    task_path = tmp_path / "task.json"
    row = module.row_from_record(
        record={
            "score_status": "provider_error",
            "score": 0,
            "metadata": {"container_engine": "podman"},
            "artifact_manifest": {"local_artifact_root": "sample"},
        },
        task_json_path=task_path,
        cell={
            "config_path": tmp_path / "config.yaml",
            "agent": "openclaw",
            "benchmark": "aime",
        },
        run_id="run",
        log_path=log_path,
        results_dir=tmp_path / "results",
        root=tmp_path,
    )

    assert row["audit_status"] == "fail"
    assert "invalid_score_status" in row["audit_failures"]


def test_openclaw_podman_spec_checks_declared_version():
    manager = OpenClawPodmanRuntimeManager(
        {
            "_agent_version": "2026.3.7",
            "podman_image": "localhost/openclaw:test",
            "gateway_token": "strong-test-token-for-version-check",
            "gateway_warmup_enabled": False,
            "openai_base_url": "https://provider.example/v1",
            "openai_api_key": "test-key",
            "openai_model_name": "test-model",
        }
    )

    spec = manager._build_podman_spec()
    assert any(
        "OpenClaw version mismatch: expected 2026.3.7" in command
        for command in spec.install_commands
    )
    assert spec.metadata["expected_openclaw_version"] == "2026.3.7"


def test_openclaw_podman_generates_strong_default_token():
    manager = OpenClawPodmanRuntimeManager(
        {
            "podman_image": "localhost/openclaw:test",
            "gateway_warmup_enabled": False,
        }
    )

    assert not is_weak_openclaw_gateway_token(manager._gateway_token)
