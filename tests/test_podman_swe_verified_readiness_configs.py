from __future__ import annotations

import json
from pathlib import Path

import yaml

from alphadiana.config.experiment_config import ExperimentConfig
from alphadiana.config.validator import ConfigValidator
from alphadiana.sandbox.swebench_container import SWEBenchContainerSandbox
from alphadiana.scorer.swe_bench import SWEBenchScorer


CONFIG_DIR = Path("configs/smokes/podman_swe_verified_readiness")
TASKSET_DIR = Path("context/podman-swe-verified-readiness/tasksets")
EXPECTED_TIERS = {
    "smoke": 2,
    "pilot32": 10,
    "long64": 2,
    "sample128": 2,
}
EXPECTED_TOKENS = {
    "smoke": 32768,
    "pilot32": 32768,
    "long64": 65536,
    "sample128": 131072,
}
EXPECTED_AGENTS = {"openclaw", "opencode", "zeroclaw"}


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_podman_swe_verified_readiness_matrix_is_exact() -> None:
    actual = {path.name for path in CONFIG_DIR.glob("*.yaml")}
    expected = {
        f"{agent}_{tier}.yaml"
        for agent in EXPECTED_AGENTS
        for tier in EXPECTED_TIERS
    }
    assert actual == expected


def test_podman_swe_verified_readiness_tasksets_are_locked() -> None:
    for tier, expected_count in EXPECTED_TIERS.items():
        payload = json.loads((TASKSET_DIR / f"{tier}.json").read_text(encoding="utf-8"))
        assert payload["dataset"] == "SWE-bench/SWE-bench_Verified"
        assert payload["split"] == "test"
        assert payload["task_count"] == expected_count
        assert len(payload["task_ids"]) == expected_count
        assert payload["force_included"] == [
            "astropy__astropy-12907",
            "astropy__astropy-13033",
        ]
        assert payload["missing_force_includes"] == []


def test_podman_swe_verified_readiness_configs_validate(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:8011/v1")
    monkeypatch.setenv("PODMAN_SWE_CONTAINER_OPENAI_BASE_URL", "http://host.containers.internal:8011/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "EMPTY")
    monkeypatch.setenv("OPENAI_MODEL_NAME", "Qwen/Qwen3.5-27B")
    monkeypatch.setenv("ALPHADIANA_PODMAN_SOCKET", "/tmp/podman.sock")
    monkeypatch.setenv("PODMAN_SWE_ZEROCLAW_BINARY_SOURCE_IMAGE", "localhost/zeroclaw-reasoning:0.6.9")
    monkeypatch.delenv("ALPHADIANA_PODMAN_DOCKER_API_VERSION", raising=False)

    for path in sorted(CONFIG_DIR.glob("*.yaml")):
        config = ExperimentConfig.from_yaml(str(path))
        errors = ConfigValidator().validate(config)

        assert errors == []
        assert config.benchmark_name == "swe_bench"
        assert config.scorer_name == "swe_bench"
        assert config.sandbox_name == "swebench_container"
        assert config.sandbox_config["container_engine"] == "podman"
        assert config.scorer_config["container_engine"] == "podman"
        assert config.agent_name in EXPECTED_AGENTS
        assert config.max_concurrent == 1
        assert config.num_samples == 1


def test_podman_swe_verified_readiness_configs_match_tiers() -> None:
    seen: dict[str, set[str]] = {tier: set() for tier in EXPECTED_TIERS}
    for path in sorted(CONFIG_DIR.glob("*.yaml")):
        data = _load_yaml(path)
        text = path.read_text(encoding="utf-8").lower()
        assert "swebench_pro" not in text
        assert "moltbench" not in text
        assert "full sweep" not in text

        agent = data["agent"]["name"]
        metadata = data["metadata"]
        tier = metadata["tier"]
        seen[tier].add(agent)
        assert metadata["container_engine"] == "podman"
        assert metadata["max_output_tokens"] == EXPECTED_TOKENS[tier]
        assert metadata["taskset_path"] == f"context/podman-swe-verified-readiness/tasksets/{tier}.json"
        assert data["benchmark"]["config"]["taskset_path"] == metadata["taskset_path"]
        assert data["benchmark"]["config"]["max_tasks"] == EXPECTED_TIERS[tier]
        assert data["sandbox"]["config"]["network_mode"] == "host"
        assert data["sandbox"]["config"]["podman_socket"] == "${ALPHADIANA_PODMAN_SOCKET}"
        assert data["scorer"]["config"]["podman_socket"] == "${ALPHADIANA_PODMAN_SOCKET}"

    assert seen == {tier: EXPECTED_AGENTS for tier in EXPECTED_TIERS}


def test_podman_swe_verified_long64_and_sample128_are_three_by_two() -> None:
    for tier in ("long64", "sample128"):
        payload = json.loads((TASKSET_DIR / f"{tier}.json").read_text(encoding="utf-8"))
        configs = list(CONFIG_DIR.glob(f"*_{tier}.yaml"))
        assert len(configs) == 3
        assert {path.stem.removesuffix(f"_{tier}") for path in configs} == EXPECTED_AGENTS
        assert payload["task_count"] == 2
        assert len(payload["task_ids"]) == 2


def test_podman_swe_verified_runner_mentions_required_scopes() -> None:
    script = Path("scripts/run_podman_swe_verified_readiness.sh").read_text(encoding="utf-8")
    for scope in ("validate", "preflight", "smoke", "pilot32", "long64", "sample128", "audit", "auto"):
        assert scope in script
    assert "validate_config || return" in script
    assert "run_preflight || return" in script
    assert "run_audit \"$completed\"" in script
    assert "audit) shift; run_audit \"$*\"" in script
    assert "preflight\\t-" in script


def test_podman_swe_defaults_to_docker_api_141(monkeypatch) -> None:
    monkeypatch.delenv("ALPHADIANA_PODMAN_DOCKER_API_VERSION", raising=False)

    sandbox = SWEBenchContainerSandbox()
    sandbox.setup({"container_engine": "podman"})
    scorer = SWEBenchScorer()
    scorer.setup({"container_engine": "podman"})

    assert sandbox._docker_api_version == "1.41"
    assert scorer._docker_api_version == "1.41"


def test_docker_swe_keeps_api_version_unset_by_default(monkeypatch) -> None:
    monkeypatch.delenv("ALPHADIANA_PODMAN_DOCKER_API_VERSION", raising=False)

    sandbox = SWEBenchContainerSandbox()
    sandbox.setup({"container_engine": "docker"})
    scorer = SWEBenchScorer()
    scorer.setup({"container_engine": "docker"})

    assert sandbox._docker_api_version == ""
    assert scorer._docker_api_version == ""
