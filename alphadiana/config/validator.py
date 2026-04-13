from __future__ import annotations

import re

from alphadiana.config.experiment_config import ExperimentConfig


class ConfigValidator:
    SANDBOX_REQUIRED_BENCHMARKS: set[str] = set()

    # Agents that require an api_base in agent_config.
    API_AGENTS = {"openclaw", "direct_llm"}

    def validate(self, config: ExperimentConfig) -> list[str]:
        errors: list[str] = []

        if not config.agent_name:
            errors.append("agent_name is required")
        if not config.agent_version:
            errors.append("agent_version is required")
        elif not re.search(r"[0-9]", config.agent_version):
            errors.append(
                f"agent_version '{config.agent_version}' does not look like a version "
                "(should contain digits, e.g. 'v0.3.1')"
            )
        if not config.benchmark_name:
            errors.append("benchmark_name is required")
        if not config.scorer_name:
            errors.append("scorer_name is required")
        if (config.max_concurrent or 0) < 1:
            errors.append("max_concurrent must be >= 1")
        if (config.max_concurrent or 0) > 64:
            errors.append("max_concurrent should be <= 64 to avoid resource exhaustion")
        if config.benchmark_name in self.SANDBOX_REQUIRED_BENCHMARKS and not config.sandbox_name:
            errors.append(
                f"benchmark '{config.benchmark_name}' requires a sandbox "
                "(set sandbox_name to 'rock' or 'local')"
            )
        # Validate agent_config has api_base or auto-deploy gateway config.
        if config.agent_name in self.API_AGENTS:
            has_api_base = bool(config.agent_config.get("api_base"))
            has_auto_deploy = bool(
                config.agent_config.get("rock_agent_config_path")
                and config.agent_config.get("openclaw_config_path")
            )
            if not has_api_base and not has_auto_deploy:
                errors.append(
                    f"agent '{config.agent_name}' requires 'api_base' or "
                    "'rock_agent_config_path' + 'openclaw_config_path' in agent_config "
                    "(auto-deploy mode)"
                )
        # Validate num_samples and task_retries.
        num_samples = getattr(config, "num_samples", 1)
        if num_samples < 1:
            errors.append("num_samples must be >= 1")
        task_retries = getattr(config, "task_retries", 0)
        if task_retries < 0:
            errors.append("task_retries must be >= 0")

        return errors

    def load_and_validate(self, yaml_path: str) -> ExperimentConfig:
        config = ExperimentConfig.from_yaml(yaml_path)
        errors = self.validate(config)
        if errors:
            raise ValueError(
                "Config validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
            )
        return config
