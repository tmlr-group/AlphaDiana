from __future__ import annotations

import re

from alphadiana.config.experiment_config import ExperimentConfig


class ConfigValidator:
    SANDBOX_REQUIRED_BENCHMARKS = {"terminal_bench", "osworld"}

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
        if config.max_concurrent < 1:
            errors.append("max_concurrent must be >= 1")
        if config.benchmark_name in self.SANDBOX_REQUIRED_BENCHMARKS and not config.sandbox_name:
            errors.append(
                f"benchmark '{config.benchmark_name}' requires a sandbox "
                "(set sandbox_name to 'rock', 'boxlite', or 'local')"
            )

        return errors

    def load_and_validate(self, yaml_path: str) -> ExperimentConfig:
        config = ExperimentConfig.from_yaml(yaml_path)
        errors = self.validate(config)
        if errors:
            raise ValueError(
                "Config validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
            )
        return config
