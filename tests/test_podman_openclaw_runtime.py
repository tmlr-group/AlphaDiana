from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from alphadiana.agent.openclaw import OpenClawAgent
from alphadiana.agent.openclaw_runtime import OpenClawPodmanRuntimeManager, OpenClawRuntimeManager
from alphadiana.config.experiment_config import ExperimentConfig
from alphadiana.container_runtime import PodmanAgentRuntimeResult, PodmanAgentSpec
from alphadiana.runner.runner import _is_gateway_autodeploy_agent


class FakePodmanAgentRuntime:
    def __init__(self) -> None:
        self.started_spec: PodmanAgentSpec | None = None
        self.cleanup_called = False
        self.result = PodmanAgentRuntimeResult(
            container_id="openclaw-podman-cid",
            api_base="http://127.0.0.1:18080/v1",
            process_id="1234",
            logs="podman container logs\n",
            config_snapshots={
                "/tmp/openclaw.json": '{"env":{"OPENAI_API_KEY":"REDACTED","OPENCLAW_GATEWAY_TOKEN":"REDACTED"}}'
            },
            artifacts={
                "/tmp/gateway.log": "gateway ready\n",
                "/tmp/oc_home/agents/main/sessions.json": '{"sessions":[]}\n',
            },
            metadata={
                "container_engine": "podman",
                "adapter_name": "openclaw-podman",
                "image": "localhost/openclaw:test",
                "container_id": "openclaw-podman-cid",
                "published_api_base": "http://127.0.0.1:18080/v1",
            },
        )

    def start(self, spec: PodmanAgentSpec) -> PodmanAgentRuntimeResult:
        self.started_spec = spec
        return self.result

    def collect_artifacts(self) -> PodmanAgentRuntimeResult:
        return self.result

    def cleanup(self, *, check: bool = False) -> None:
        self.cleanup_called = True


def _write_openclaw_config(tmp_path: Path) -> Path:
    path = tmp_path / "openclaw.json"
    path.write_text(
        json.dumps({
            "env": {
                "OPENAI_BASE_URL": "",
                "OPENAI_API_KEY": "EMPTY",
                "OPENAI_MODEL_NAME": "",
                "OPENCLAW_GATEWAY_TOKEN": "${OPENCLAW_GATEWAY_TOKEN}",
            },
            "models": {"providers": {}},
        }),
        encoding="utf-8",
    )
    return path


def _write_rock_config(tmp_path: Path) -> Path:
    path = tmp_path / "rock_agent_config.yaml"
    path.write_text("env: {}\nrun_cmd: openclaw gateway\n", encoding="utf-8")
    return path


def test_openclaw_podman_runtime_builds_spec_and_runtime_info(tmp_path: Path) -> None:
    fake_runtime = FakePodmanAgentRuntime()
    openclaw_config = _write_openclaw_config(tmp_path)
    manager = OpenClawPodmanRuntimeManager({
        "runtime_backend": "podman",
        "openclaw_config_path": str(openclaw_config),
        "podman_image": "localhost/openclaw:test",
        "OPENAI_BASE_URL": "http://provider.test/v1",
        "OPENAI_API_KEY": "sk-secret",
        "OPENAI_MODEL_NAME": "qwen-test",
        "gateway_token": "OPENCLAW_TEST",
        "_podman_agent_runtime": fake_runtime,
    })

    with patch.dict("os.environ", {}, clear=True):
        info = manager.ensure_ready(None)

    spec = fake_runtime.started_spec
    assert spec is not None
    assert spec.adapter_name == "openclaw-podman"
    assert spec.image == "localhost/openclaw:test"
    assert spec.network == "slirp4netns:allow_host_loopback=true"
    assert spec.exposed_port == 8080
    assert spec.api_base_suffix == "/v1"
    assert spec.healthcheck is not None
    assert getattr(spec.healthcheck, "token") == "OPENCLAW_TEST"
    assert spec.env["OPENAI_BASE_URL"] == "http://provider.test/v1"
    assert spec.env["OPENAI_API_KEY"] == "sk-secret"
    assert spec.env["OPENAI_MODEL_NAME"] == "qwen-test"
    assert spec.env["OPENCLAW_GATEWAY_TOKEN"] == "OPENCLAW_TEST"
    rendered_config = json.loads(spec.files[0].content)  # type: ignore[arg-type]
    assert rendered_config["env"]["OPENAI_BASE_URL"] == "http://provider.test/v1"
    assert rendered_config["env"]["OPENAI_API_KEY"] == "sk-secret"
    assert rendered_config["env"]["OPENAI_MODEL_NAME"] == "qwen-test"
    assert rendered_config["env"]["OPENCLAW_GATEWAY_TOKEN"] == "OPENCLAW_TEST"
    assert info["api_base"] == "http://127.0.0.1:18080/v1"
    assert info["gateway_url"] == "http://127.0.0.1:18080/v1/chat/completions"
    assert info["container_engine"] == "podman"
    assert info["adapter_name"] == "openclaw-podman"
    assert info["metadata"]["logprob_support"] == "disabled"

    artifacts = manager.collect_artifacts(None)
    assert artifacts["gateway_log_excerpt"] == "gateway ready\n"
    assert "sk-secret" not in artifacts["workspace_file_contents"]["openclaw_runtime_config.json"]
    assert artifacts["sandbox_metadata"]["container_engine"] == "podman"

    manager.teardown()
    assert fake_runtime.cleanup_called is True


def test_openclaw_podman_gateway_defaults_do_not_reference_rock_urls() -> None:
    text = Path("openclaw_deploy/podman_gateway.yaml").read_text(encoding="utf-8").lower()

    assert "admin_base_url" not in text
    assert "proxy_base_url" not in text
    assert "rock" not in text


def test_openclaw_agent_selects_podman_manager_and_preserves_legacy_rock(tmp_path: Path) -> None:
    openclaw_config = _write_openclaw_config(tmp_path)
    rock_config = _write_rock_config(tmp_path)

    with patch.dict("os.environ", {}, clear=True):
        podman_agent = OpenClawAgent()
        podman_agent.setup({
            "runtime_backend": "podman",
            "openclaw_config_path": str(openclaw_config),
            "podman_image": "localhost/openclaw:test",
            "OPENAI_BASE_URL": "http://provider.test/v1",
            "OPENAI_API_KEY": "sk-secret",
            "OPENAI_MODEL_NAME": "qwen-test",
            "_podman_agent_runtime": FakePodmanAgentRuntime(),
        })
        legacy_agent = OpenClawAgent()
        legacy_agent.setup({
            "rock_agent_config_path": str(rock_config),
            "openclaw_config_path": str(openclaw_config),
        })

    assert isinstance(podman_agent._runtime_manager, OpenClawPodmanRuntimeManager)
    assert getattr(podman_agent._runtime_manager, "requires_sandbox") is False
    assert isinstance(legacy_agent._runtime_manager, OpenClawRuntimeManager)
    assert not isinstance(legacy_agent._runtime_manager, OpenClawPodmanRuntimeManager)
    assert getattr(legacy_agent._runtime_manager, "requires_sandbox") is True


def test_openclaw_podman_runtime_does_not_trigger_rock_autodeploy() -> None:
    config = ExperimentConfig(
        agent_name="openclaw",
        agent_version="2026.3.20",
        agent_config={
            "runtime_backend": "podman",
            "rock_agent_config_path": "openclaw_deploy/rock_agent_config.prebuilt.yaml",
            "openclaw_config_path": "openclaw_deploy/openclaw.json",
        },
        benchmark_name="aime",
        scorer_name="numeric",
    )

    assert _is_gateway_autodeploy_agent(config) is False
