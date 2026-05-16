from __future__ import annotations

from pathlib import Path

import yaml

from alphadiana.config.experiment_config import ExperimentConfig
from alphadiana.config.validator import ConfigValidator


CONFIG_DIR = Path("configs/smokes/podman_mmmu_pro_readiness")

EXPECTED_MATRIX = {
    "openclaw_mmmu_pro_pilot.yaml",
    "zeroclaw_mmmu_pro_pilot.yaml",
    "opencode_mmmu_pro_pilot.yaml",
}

SELECTED_INDICES = [0, 1, 2]
SELECTED_TASK_IDS = [
    "mmmu_pro_test_History_1",
    "mmmu_pro_test_Art_113",
    "mmmu_pro_validation_Design_19",
]

EXCLUDED_NAMES = {
    "swe_bench",
    "swebench",
    "swebench_pro",
    "terminal_bench2",
    "moltbench",
    "aime",
    "gpqa",
    "hle",
    "imo_answerbench",
}


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_podman_mmmu_pro_readiness_matrix_is_exact() -> None:
    actual = {path.name for path in CONFIG_DIR.glob("*_pilot.yaml")}
    assert actual == EXPECTED_MATRIX


def test_podman_mmmu_pro_readiness_configs_validate(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:8011/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "EMPTY")
    monkeypatch.setenv("OPENAI_MODEL_NAME", "vision-model")
    monkeypatch.setenv("ALPHADIANA_OPENCLAW_PODMAN_IMAGE", "localhost/alphadiana-openclaw:latest")
    monkeypatch.setenv("ALPHADIANA_ZEROCLAW_PODMAN_IMAGE", "localhost/zeroclaw-reasoning:0.6.9")

    validator = ConfigValidator()
    for name in sorted(EXPECTED_MATRIX):
        config = ExperimentConfig.from_yaml(str(CONFIG_DIR / name))
        assert validator.validate(config) == []


def test_podman_mmmu_pro_readiness_configs_are_vision_podman_only() -> None:
    for path in CONFIG_DIR.glob("*_pilot.yaml"):
        text = path.read_text(encoding="utf-8").lower()
        for excluded in EXCLUDED_NAMES:
            assert excluded not in text, f"{path} unexpectedly references {excluded}"

        data = _load_yaml(path)
        assert data["benchmark"]["name"] == "mmmu_pro"
        benchmark_config = data["benchmark"]["config"]
        assert benchmark_config["dataset"] == "MMMU/MMMU_Pro"
        assert benchmark_config["data_config"] == "vision"
        assert benchmark_config["split"] == "test"
        assert benchmark_config["dataset_indices"] == SELECTED_INDICES
        assert "max_tasks" not in benchmark_config

        metadata = data["metadata"]
        assert metadata["validation_campaign"] == "podman-mmmu-pro-readiness-pilot"
        assert metadata["container_engine"] == "podman"
        assert metadata["supports_multimodal"] is True
        assert metadata["selected_dataset_indices"] == SELECTED_INDICES
        assert metadata["selected_task_ids"] == SELECTED_TASK_IDS

        assert data["max_concurrent"] == 1
        assert data["num_samples"] == 1
        assert data["scorer"]["name"] == "exact_match"

        agent_name = data["agent"]["name"]
        agent_config = data["agent"]["config"]
        assert agent_config["enable_thinking"] is True
        if agent_name in {"openclaw", "zeroclaw"}:
            assert agent_config["runtime_backend"] == "podman"
        if agent_name == "zeroclaw":
            assert agent_config["provider_max_tokens"] >= 8192
        else:
            assert agent_config["max_tokens"] >= 8192
        if agent_name == "opencode":
            assert agent_config["controller_mode"] == "podman"
            assert agent_config["controller_network"] == "host"


def test_podman_mmmu_pro_readiness_configs_use_provider_env_placeholders() -> None:
    for path in CONFIG_DIR.glob("*_pilot.yaml"):
        text = path.read_text(encoding="utf-8")
        assert "${OPENAI_BASE_URL}" in text
        assert "${OPENAI_API_KEY}" in text
        assert "${OPENAI_MODEL_NAME}" in text
        assert "Qwen/Qwen3.5-4B" not in text


def test_podman_mmmu_pro_readiness_configs_never_disable_thinking() -> None:
    needle = "enable" + "_thinking"
    for path in CONFIG_DIR.glob("*_pilot.yaml"):
        text = path.read_text(encoding="utf-8").lower()
        assert f"{needle}: true" in text
        assert f"{needle}: " + "false" not in text
