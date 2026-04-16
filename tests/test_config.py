"""Tests for experiment configuration and validation."""

import textwrap

import pytest
import yaml

from alphadiana.config.experiment_config import ExperimentConfig
from alphadiana.config.validator import ConfigValidator


class TestExperimentConfigFromYAML:
    """Verify loading ExperimentConfig from a YAML file."""

    def test_full_config(self, tmp_path):
        """A complete YAML config loads correctly."""
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
        assert config.agent_config["temperature"] == 0.0
        assert config.benchmark_name == "aime2024"
        assert config.benchmark_config["split"] == "test"
        assert config.sandbox_name is None
        assert config.scorer_name == "numeric"
        assert config.scorer_config["tolerance"] == 0.000001
        assert config.max_concurrent == 4
        assert config.output_dir == "./results"
        assert config.metadata["author"] == "tester"

    def test_minimal_config(self, tmp_path):
        """Minimal config with only required fields."""
        config_content = textwrap.dedent("""\
            agent:
              name: openclaw
              version: "v1.0"
            benchmark:
              name: aime
            scorer:
              name: numeric
        """)
        yaml_path = tmp_path / "minimal.yaml"
        yaml_path.write_text(config_content)

        config = ExperimentConfig.from_yaml(str(yaml_path))
        assert config.agent_name == "openclaw"
        assert config.benchmark_name == "aime"
        assert config.scorer_name == "numeric"
        assert config.max_concurrent == 1  # default
        assert config.output_dir == "./results"  # default

    def test_sandbox_config(self, tmp_path):
        """YAML config with a sandbox section."""
        config_content = textwrap.dedent("""\
            agent:
              name: openclaw
              version: "v1.0"
            benchmark:
              name: aime
            sandbox:
              name: rock
              config:
                image: python:3.11
            scorer:
              name: exact_match
        """)
        yaml_path = tmp_path / "sandbox.yaml"
        yaml_path.write_text(config_content)

        config = ExperimentConfig.from_yaml(str(yaml_path))
        assert config.sandbox_name == "rock"
        assert config.sandbox_config["image"] == "python:3.11"

    def test_unresolved_rock_placeholders_fall_back_to_empty(self, tmp_path, monkeypatch):
        """Unexpanded ROCK placeholders should not block runtime port fallback."""
        monkeypatch.delenv("ROCK_BASE_URL", raising=False)
        monkeypatch.delenv("ROCK_PROXY_URL", raising=False)

        config_content = textwrap.dedent("""\
            agent:
              name: zeroclaw
              version: "0.6.9"
              config:
                admin_base_url: "${ROCK_BASE_URL}"
                proxy_base_url: "${ROCK_PROXY_URL}"
            benchmark:
              name: aime
            scorer:
              name: math_verify
        """)
        yaml_path = tmp_path / "zeroclaw-rock.yaml"
        yaml_path.write_text(config_content)

        config = ExperimentConfig.from_yaml(str(yaml_path))

        assert config.agent_config["admin_base_url"] == ""
        assert config.agent_config["proxy_base_url"] == ""

    def test_overrides_clear_unresolved_env_placeholders(self, tmp_path, monkeypatch):
        """Overrides with unresolved env placeholders should also collapse to empty."""
        monkeypatch.delenv("ROCK_BASE_URL", raising=False)
        monkeypatch.delenv("ROCK_PROXY_URL", raising=False)

        config_content = textwrap.dedent("""\
            agent:
              name: zeroclaw
              version: "0.6.9"
            benchmark:
              name: aime
            scorer:
              name: math_verify
        """)
        yaml_path = tmp_path / "zeroclaw-overrides.yaml"
        yaml_path.write_text(config_content)

        config = ExperimentConfig.from_yaml(
            str(yaml_path),
            overrides={
                "agent": {
                    "config": {
                        "admin_base_url": "${ROCK_BASE_URL}",
                        "proxy_base_url": "${ROCK_PROXY_URL}",
                    }
                }
            },
        )

        assert config.agent_config["admin_base_url"] == ""
        assert config.agent_config["proxy_base_url"] == ""


class TestExperimentConfigDefaults:
    """Verify ExperimentConfig default values."""

    def test_auto_run_id(self):
        """run_id is auto-generated when not specified."""
        config = ExperimentConfig(
            agent_name="openclaw",
            agent_version="v1.0",
            benchmark_name="aime",
            scorer_name="numeric",
        )
        assert len(config.run_id) == 12  # uuid hex[:12]

    def test_default_max_concurrent(self):
        """The default max_concurrent is 1."""
        config = ExperimentConfig(
            agent_name="openclaw",
            agent_version="v1.0",
            benchmark_name="aime",
            scorer_name="numeric",
        )
        assert config.max_concurrent == 1

    def test_default_output_dir(self):
        """The default output_dir is ./results."""
        config = ExperimentConfig(
            agent_name="openclaw",
            agent_version="v1.0",
            benchmark_name="aime",
            scorer_name="numeric",
        )
        assert config.output_dir == "./results"


class TestConfigValidator:
    """Verify ConfigValidator validation logic."""

    def test_valid_config(self):
        """A valid config produces no errors."""
        config = ExperimentConfig(
            agent_name="openclaw",
            agent_version="v0.3.1",
            benchmark_name="aime2024",
            scorer_name="numeric",
            agent_config={"api_base": "http://localhost:8000/v1"},
        )
        validator = ConfigValidator()
        errors = validator.validate(config)
        assert errors == []

    def test_missing_agent_name(self):
        """Missing agent_name should report an error."""
        config = ExperimentConfig(
            agent_name="",
            agent_version="v0.3.1",
            benchmark_name="aime2024",
            scorer_name="numeric",
        )
        validator = ConfigValidator()
        errors = validator.validate(config)
        assert any("agent_name" in e for e in errors)

    def test_missing_agent_version(self):
        """Missing agent_version should report an error."""
        config = ExperimentConfig(
            agent_name="openclaw",
            agent_version="",
            benchmark_name="aime2024",
            scorer_name="numeric",
        )
        validator = ConfigValidator()
        errors = validator.validate(config)
        assert any("agent_version" in e for e in errors)

    def test_invalid_version_format(self):
        """agent_version containing no digits should trigger a warning."""
        config = ExperimentConfig(
            agent_name="openclaw",
            agent_version="latest",
            benchmark_name="aime2024",
            scorer_name="numeric",
        )
        validator = ConfigValidator()
        errors = validator.validate(config)
        assert any("version" in e.lower() for e in errors)

    def test_missing_benchmark_name(self):
        """Missing benchmark_name should report an error."""
        config = ExperimentConfig(
            agent_name="openclaw",
            agent_version="v1.0",
            benchmark_name="",
            scorer_name="numeric",
        )
        validator = ConfigValidator()
        errors = validator.validate(config)
        assert any("benchmark_name" in e for e in errors)

    def test_missing_scorer_name(self):
        """Missing scorer_name should report an error."""
        config = ExperimentConfig(
            agent_name="openclaw",
            agent_version="v1.0",
            benchmark_name="aime",
            scorer_name="",
        )
        validator = ConfigValidator()
        errors = validator.validate(config)
        assert any("scorer_name" in e for e in errors)

    def test_sandbox_not_required_for_aime(self):
        """aime does not require sandbox."""
        config = ExperimentConfig(
            agent_name="openclaw",
            agent_version="v0.3.1",
            benchmark_name="aime",
            scorer_name="numeric",
            sandbox_name=None,
        )
        validator = ConfigValidator()
        errors = validator.validate(config)
        assert not any("sandbox" in e.lower() for e in errors)

    def test_load_and_validate(self, tmp_path):
        """load_and_validate loads and validates a YAML file."""
        config_content = textwrap.dedent("""\
            agent:
              name: openclaw
              version: "v0.3.1"
              config:
                api_base: "http://localhost:8000/v1"
            benchmark:
              name: aime
            scorer:
              name: numeric
        """)
        yaml_path = tmp_path / "valid.yaml"
        yaml_path.write_text(config_content)

        validator = ConfigValidator()
        config = validator.load_and_validate(str(yaml_path))
        assert config.agent_name == "openclaw"

    def test_load_and_validate_invalid(self, tmp_path):
        """load_and_validate raises ValueError for an invalid config."""
        config_content = textwrap.dedent("""\
            agent:
              name: ""
              version: "v0.3.1"
            benchmark:
              name: aime
            scorer:
              name: numeric
        """)
        yaml_path = tmp_path / "invalid.yaml"
        yaml_path.write_text(config_content)

        validator = ConfigValidator()
        with pytest.raises(ValueError, match="Config validation failed"):
            validator.load_and_validate(str(yaml_path))
