"""Tests for experiment configuration and validation."""

import textwrap

import pytest
import yaml

from alphadiana.config.experiment_config import ExperimentConfig
from alphadiana.config.validator import ConfigValidator


class TestExperimentConfigFromYAML:
    """验证从 YAML 文件加载 ExperimentConfig。"""

    def test_full_config(self, tmp_path):
        """完整 YAML 配置加载正确。"""
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
        """最小配置（只有必需字段）。"""
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
        """带 sandbox 配置的 YAML。"""
        config_content = textwrap.dedent("""\
            agent:
              name: openclaw
              version: "v1.0"
            benchmark:
              name: terminal_bench
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


class TestExperimentConfigDefaults:
    """验证 ExperimentConfig 默认值。"""

    def test_auto_run_id(self):
        """未指定 run_id 时自动生成。"""
        config = ExperimentConfig(
            agent_name="openclaw",
            agent_version="v1.0",
            benchmark_name="aime",
            scorer_name="numeric",
        )
        assert len(config.run_id) == 12  # uuid hex[:12]

    def test_default_max_concurrent(self):
        """默认 max_concurrent 为 1。"""
        config = ExperimentConfig(
            agent_name="openclaw",
            agent_version="v1.0",
            benchmark_name="aime",
            scorer_name="numeric",
        )
        assert config.max_concurrent == 1

    def test_default_output_dir(self):
        """默认 output_dir 为 ./results。"""
        config = ExperimentConfig(
            agent_name="openclaw",
            agent_version="v1.0",
            benchmark_name="aime",
            scorer_name="numeric",
        )
        assert config.output_dir == "./results"


class TestConfigValidator:
    """验证 ConfigValidator 的校验逻辑。"""

    def test_valid_config(self):
        """合法配置无错误。"""
        config = ExperimentConfig(
            agent_name="openclaw",
            agent_version="v0.3.1",
            benchmark_name="aime2024",
            scorer_name="numeric",
        )
        validator = ConfigValidator()
        errors = validator.validate(config)
        assert errors == []

    def test_missing_agent_name(self):
        """缺少 agent_name 应报错。"""
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
        """缺少 agent_version 应报错。"""
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
        """agent_version 不含数字应警告。"""
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
        """缺少 benchmark_name 应报错。"""
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
        """缺少 scorer_name 应报错。"""
        config = ExperimentConfig(
            agent_name="openclaw",
            agent_version="v1.0",
            benchmark_name="aime",
            scorer_name="",
        )
        validator = ConfigValidator()
        errors = validator.validate(config)
        assert any("scorer_name" in e for e in errors)

    def test_sandbox_required_for_terminal_bench(self):
        """terminal_bench 需要 sandbox 配置。"""
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

    def test_sandbox_required_for_osworld(self):
        """osworld 需要 sandbox 配置。"""
        config = ExperimentConfig(
            agent_name="openclaw",
            agent_version="v0.3.1",
            benchmark_name="osworld",
            scorer_name="exact_match",
            sandbox_name=None,
        )
        validator = ConfigValidator()
        errors = validator.validate(config)
        assert any("sandbox" in e.lower() for e in errors)

    def test_sandbox_not_required_for_aime(self):
        """aime 不需要 sandbox。"""
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
        """load_and_validate 加载并验证 YAML 文件。"""
        config_content = textwrap.dedent("""\
            agent:
              name: openclaw
              version: "v0.3.1"
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
        """load_and_validate 对无效配置抛出 ValueError。"""
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
