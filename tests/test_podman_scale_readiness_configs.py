from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

from alphadiana.config.experiment_config import ExperimentConfig
from alphadiana.config.validator import ConfigValidator


CONFIG_DIR = Path("configs/smokes/podman_scale_readiness")
RUNNER = Path("scripts/run_podman_scale_readiness.sh")

EXPECTED_MATRIX = {
    f"{agent}_{benchmark}_pilot.yaml"
    for agent in ("openclaw", "zeroclaw", "opencode")
    for benchmark in ("aime", "gpqa", "hle", "imo")
}

EXCLUDED_NAMES = {
    "swe_bench",
    "swebench",
    "swebench_pro",
    "terminal_bench2",
    "moltbench",
    "mmmu_pro",
}


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_podman_scale_readiness_matrix_is_exact() -> None:
    actual = {path.name for path in CONFIG_DIR.glob("*_pilot.yaml")}
    assert actual == EXPECTED_MATRIX


def test_podman_scale_readiness_configs_validate(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:8011/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "EMPTY")
    monkeypatch.setenv("OPENAI_MODEL_NAME", "Qwen/Qwen3.5-4B")
    monkeypatch.setenv("ALPHADIANA_OPENCLAW_PODMAN_IMAGE", "localhost/alphadiana-openclaw:latest")
    monkeypatch.setenv("ALPHADIANA_ZEROCLAW_PODMAN_IMAGE", "localhost/zeroclaw-reasoning:0.6.9")

    validator = ConfigValidator()
    for name in sorted(EXPECTED_MATRIX):
        config = ExperimentConfig.from_yaml(str(CONFIG_DIR / name))
        assert validator.validate(config) == []


def test_podman_scale_readiness_configs_are_standard_reasoning_only() -> None:
    for path in CONFIG_DIR.glob("*_pilot.yaml"):
        text = path.read_text(encoding="utf-8").lower()
        for excluded in EXCLUDED_NAMES:
            assert excluded not in text, f"{path} unexpectedly references {excluded}"

        data = _load_yaml(path)
        assert data["metadata"]["container_engine"] == "podman"
        assert data["metadata"]["validation_campaign"] == "podman-scale-readiness-pilot"
        assert data["max_concurrent"] == 1
        assert data["num_samples"] == 1

        benchmark_name = data["benchmark"]["name"]
        benchmark_config = data["benchmark"]["config"]
        assert benchmark_config["max_tasks"] == 3

        if benchmark_name == "hle":
            assert benchmark_config["answer_types"] == ["multipleChoice"]
        if benchmark_name == "imo_answerbench":
            assert data["scorer"]["name"] == "imo_verify"

        agent_name = data["agent"]["name"]
        agent_config = data["agent"]["config"]
        if agent_name in {"openclaw", "zeroclaw"}:
            assert agent_config["runtime_backend"] == "podman"
            assert agent_config["enable_thinking"] is True
        if agent_name == "opencode":
            assert agent_config["controller_mode"] == "podman"
            assert agent_config["controller_network"] == "host"
            assert agent_config["enable_thinking"] is True
        if agent_name == "openclaw":
            assert agent_config["podman_gateway_config_path"].endswith("openclaw_podman_gateway_host.yaml")
        if agent_name == "zeroclaw":
            assert agent_config["podman_network"] == "host"


def test_podman_scale_readiness_runner_is_standard_only() -> None:
    script = RUNNER.read_text(encoding="utf-8").lower()
    for excluded in EXCLUDED_NAMES:
        assert excluded not in script
    assert "openclaw zeroclaw opencode" in script
    assert "aime gpqa hle imo" in script
    assert "python -m alphadiana.cli run" in script
    assert "--redo-all" in script


def test_podman_scale_readiness_runner_allows_loopback_provider_url_with_host_network() -> None:
    env = os.environ.copy()
    env.update({
        "OPENAI_BASE_URL": "http://localhost:8011/v1",
        "OPENAI_API_KEY": "EMPTY",
        "OPENAI_MODEL_NAME": "Qwen/Qwen3.5-4B",
    })
    result = subprocess.run(
        [
            "bash",
            "-c",
            f"source {RUNNER}; reject_loopback_provider_base",
        ],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0


def test_podman_scale_readiness_runner_blocks_loopback_provider_url_without_host_network() -> None:
    env = os.environ.copy()
    env.update({
        "OPENAI_BASE_URL": "http://localhost:8011/v1",
        "OPENAI_API_KEY": "EMPTY",
        "OPENAI_MODEL_NAME": "Qwen/Qwen3.5-4B",
        "PODMAN_SCALE_PREFLIGHT_NETWORK": "slirp4netns:allow_host_loopback=true",
    })
    result = subprocess.run(
        [
            "bash",
            "-c",
            f"source {RUNNER}; reject_loopback_provider_base",
        ],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "container itself" in result.stderr
    assert "PODMAN_SCALE_PREFLIGHT_NETWORK=host" in result.stderr


def test_podman_scale_readiness_runner_has_container_provider_preflight() -> None:
    script = RUNNER.read_text(encoding="utf-8")
    assert "docker.io/curlimages/curl" in script
    assert "podman run --rm --network" in script
    assert 'PROVIDER_PREFLIGHT_NETWORK="${PODMAN_SCALE_PREFLIGHT_NETWORK:-host}"' in script
    assert "-e http_proxy=" in script
    assert "preflight_provider_from_podman" in script
    assert "extend_no_proxy_for_provider" in script
    assert "OPENAI_BASE_URL must be reachable" in script or "provider base must be reachable" in script


def test_podman_scale_readiness_runner_extends_no_proxy_for_provider() -> None:
    env = os.environ.copy()
    env.update({
        "OPENAI_BASE_URL": "http://host.containers.internal:8011/v1",
        "OPENAI_API_KEY": "EMPTY",
        "OPENAI_MODEL_NAME": "Qwen/Qwen3.5-4B",
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    })
    result = subprocess.run(
        [
            "bash",
            "-c",
            f"source {RUNNER}; extend_no_proxy_for_provider; printf '%s\\n' \"$NO_PROXY\"",
        ],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "host.containers.internal" in result.stdout
