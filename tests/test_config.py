"""Tests for experiment configuration and validation."""

import os
import textwrap

import pytest
import yaml

from alphadiana.config.experiment_config import ExperimentConfig
from alphadiana.config.validator import ConfigValidator


def test_experiment_config_from_yaml(tmp_path):
    """Load an ExperimentConfig from a YAML file and check fields."""
    config_content = textwrap.dedent("""\
        run_id: "test-run-001"
        agent:
          name: openclaw
          version: "v0.3.1"
          config:
            model: deepseek-r1
            temperature: 0.0
        benchmark:
          name: aime2024
          config:
            split: test
        sandbox: null
        scorer:
          name: numeric
          config:
            tolerance: 0.000001
        max_concurrent: 4
        output_dir: "./results"
        metadata:
          author: tester
    """)
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(config_content)

    config = ExperimentConfig.from_yaml(str(yaml_path))

    assert config.run_id == "test-run-001"
    assert config.agent_name == "openclaw"
    assert config.agent_version == "v0.3.1"
    assert config.agent_config["model"] == "deepseek-r1"
    assert config.benchmark_name == "aime2024"
    assert config.sandbox_name is None
    assert config.scorer_name == "numeric"
    assert config.max_concurrent == 4
    assert config.metadata["author"] == "tester"


def test_config_validator_valid():
    """A valid config should produce no errors."""
    config = ExperimentConfig(
        agent_name="openclaw",
        agent_version="v0.3.1",
        benchmark_name="aime2024",
        scorer_name="numeric",
    )
    validator = ConfigValidator()
    errors = validator.validate(config)
    assert errors == []


def test_config_validator_missing_agent():
    """Missing agent_name should produce an error."""
    config = ExperimentConfig(
        agent_name="",
        agent_version="v0.3.1",
        benchmark_name="aime2024",
        scorer_name="numeric",
    )
    validator = ConfigValidator()
    errors = validator.validate(config)
    assert any("agent_name" in e for e in errors)


def test_config_validator_sandbox_required():
    """terminal_bench without a sandbox should produce an error."""
    config = ExperimentConfig(
        agent_name="openclaw",
        agent_version="v0.3.1",
        benchmark_name="terminal_bench",
        scorer_name="exact_match",
        sandbox_name=None,
    )
    validator = ConfigValidator()
    errors = validator.validate(config)
    assert any("sandbox" in e.lower() for e in errors)
