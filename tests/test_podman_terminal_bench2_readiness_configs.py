from __future__ import annotations

from pathlib import Path

import yaml

from alphadiana.config.experiment_config import ExperimentConfig
from alphadiana.config.validator import ConfigValidator


CONFIG_DIR = Path("configs/smokes/podman_terminal_bench2")
EXPECTED_MATRIX = {"terminal_bench2_opencode_pilot.yaml"}
SELECTED_TASK_NAMES = [
    "db-wal-recovery",
    "fix-git",
    "overfull-hbox",
    "adaptive-rejection-sampler",
    "break-filter-js-from-html",
]
SELECTED_TASK_IDS = [f"tb2_{name}" for name in SELECTED_TASK_NAMES]
EXCLUDED_BENCHMARKS = {
    "swe_bench",
    "swebench",
    "swebench_pro",
    "mmmu_pro",
    "moltbench",
    "aime",
    "gpqa",
    "hle",
    "imo_answerbench",
}


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_podman_terminal_bench2_readiness_matrix_is_exact() -> None:
    actual = {path.name for path in CONFIG_DIR.glob("*_pilot.yaml")}
    assert actual == EXPECTED_MATRIX


def test_podman_terminal_bench2_readiness_config_validates(tmp_path: Path, monkeypatch) -> None:
    tb2_dir = tmp_path / "tb2"
    tb2_dir.mkdir()
    monkeypatch.setenv("TERMINAL_BENCH2_DIR", str(tb2_dir))
    monkeypatch.setenv("OPENAI_BASE_URL", "http://host.containers.internal:8011/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "EMPTY")
    monkeypatch.setenv("OPENAI_MODEL_NAME", "coding-model")
    monkeypatch.setenv("TB2_OPENCODE_RUNTIME_IMAGE", "localhost/alphadiana/tb2-opencode-controller:latest")
    monkeypatch.setenv("ALPHADIANA_TB2_LOGS_DIR", str(tmp_path / "logs"))

    config = ExperimentConfig.from_yaml(str(CONFIG_DIR / "terminal_bench2_opencode_pilot.yaml"))

    assert ConfigValidator().validate(config) == []
    assert config.benchmark_name == "terminal_bench2"
    assert config.agent_name == "terminal_bench2_opencode"
    assert config.agent_config["container_engine"] == "podman"
    assert config.benchmark_config["task_ids"] == SELECTED_TASK_NAMES


def test_podman_terminal_bench2_readiness_config_is_open_code_tb2_only() -> None:
    path = CONFIG_DIR / "terminal_bench2_opencode_pilot.yaml"
    data = _load_yaml(path)
    text = path.read_text(encoding="utf-8").lower()

    for excluded in EXCLUDED_BENCHMARKS:
        assert excluded not in text, f"{path} unexpectedly references {excluded}"

    assert data["benchmark"]["name"] == "terminal_bench2"
    assert data["benchmark"]["config"]["task_ids"] == SELECTED_TASK_NAMES
    assert data["benchmark"]["config"]["max_tasks"] == 5
    assert data["agent"]["name"] == "terminal_bench2_opencode"
    assert data["agent"]["config"]["container_engine"] == "podman"
    assert data["scorer"]["name"] == "terminal_bench2"
    assert data["num_samples"] == 1
    assert data["max_concurrent"] == 1

    metadata = data["metadata"]
    assert metadata["validation_campaign"] == "podman-terminal-bench2-readiness-pilot"
    assert metadata["container_engine"] == "podman"
    assert metadata["run_suffix"] == "terminal_bench2_opencode"
    assert metadata["selected_task_names"] == SELECTED_TASK_NAMES
    assert metadata["selected_task_ids"] == SELECTED_TASK_IDS


def test_podman_terminal_bench2_readiness_uses_env_placeholders() -> None:
    text = (CONFIG_DIR / "terminal_bench2_opencode_pilot.yaml").read_text(encoding="utf-8")
    assert "${TERMINAL_BENCH2_DIR}" in text
    assert "${OPENAI_BASE_URL}" in text
    assert "${OPENAI_API_KEY}" in text
    assert "${OPENAI_MODEL_NAME}" in text
    assert "${TB2_OPENCODE_RUNTIME_IMAGE}" in text
    assert "${ALPHADIANA_TB2_LOGS_DIR}" in text
