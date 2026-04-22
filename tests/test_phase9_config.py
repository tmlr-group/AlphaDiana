"""Phase 9: frozen YAML schema regression tests."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

CONFIG_PATH = Path("configs/full_runs/phase9_directllm_gpqa_diamond_qwen35_27b_logprobs.yaml")


@pytest.fixture(scope="module")
def config() -> dict:
    assert CONFIG_PATH.exists(), f"missing: {CONFIG_PATH}"
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


class TestPhase9YAMLSchema:
    def test_run_id(self, config):
        assert config["run_id"] == "phase9_directllm_gpqa_diamond_qwen35_27b_logprobs"

    def test_agent_direct_llm(self, config):
        assert config["agent"]["name"] == "direct_llm"
        assert config["agent"]["version"] == "1.0"

    def test_agent_model_and_endpoint(self, config):
        c = config["agent"]["config"]
        assert c["model"] == "Qwen/Qwen3.5-27B"
        assert c["api_base"] == "http://127.0.0.1:8011/v1"
        assert c["api_key"] == "EMPTY"

    def test_agent_sampling_parameters_frozen(self, config):
        c = config["agent"]["config"]
        assert c["temperature"] == 0.0
        assert c["top_p"] == 0.95
        assert c["max_tokens"] == 32768
        assert c["stream"] is True

    def test_agent_logprobs_enabled(self, config):
        c = config["agent"]["config"]
        assert c["capture_logprobs"] is True
        assert c["top_logprobs"] == 20

    def test_system_prompt_has_boxed_answers(self, config):
        prompt = config["agent"]["config"]["system_prompt"]
        assert "\\boxed{A}" in prompt
        assert "\\boxed{D}" in prompt

    def test_benchmark_gpqa_diamond(self, config):
        b = config["benchmark"]
        assert b["name"] == "gpqa_diamond"
        assert b["config"]["dataset"] == "fingertap/GPQA-Diamond"
        assert b["config"]["split"] == "test"
        assert b["config"]["seed"] == 42

    def test_scorer_exact_match(self, config):
        assert config["scorer"]["name"] == "exact_match"

    def test_run_scale(self, config):
        assert config["max_concurrent"] == 4
        assert config["num_samples"] == 1

    def test_output_dir(self, config):
        assert config["output_dir"] == "./results/phase9_directllm_gpqa_diamond_qwen35_27b_logprobs"

    def test_metadata_fields(self, config):
        md = config["metadata"]
        assert md["phase"] == "09"
        assert md["model"] == "Qwen/Qwen3.5-27B"
        assert md["api_base"] == "http://127.0.0.1:8011/v1"
        assert md["vllm_reasoning_parser"] == "qwen3"


class TestPhase9ConfigLoadsViaExperimentConfig:
    def test_loads_without_error(self, config):
        """If alphadiana has an ExperimentConfig loader, exercise it; else skip."""
        try:
            from alphadiana.config.experiment_config import ExperimentConfig
        except ImportError:
            pytest.skip("ExperimentConfig not importable")
        # Most loaders accept a file path; try both patterns.
        if hasattr(ExperimentConfig, "from_yaml"):
            cfg = ExperimentConfig.from_yaml(str(CONFIG_PATH))
            assert cfg is not None
        else:
            pytest.skip("ExperimentConfig.from_yaml not available")
