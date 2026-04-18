from __future__ import annotations

import re
from pathlib import Path

from alphadiana.config.experiment_config import ExperimentConfig


class ConfigValidator:
    SANDBOX_REQUIRED_BENCHMARKS: set[str] = {"terminal_bench", "osworld"}

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

        if config.agent_name in self.API_AGENTS:
            runtime = str(config.agent_config.get("runtime", "")).strip()
            if config.agent_name == "openclaw" and runtime == "swebench_container":
                if config.sandbox_name != "swebench_container":
                    errors.append(
                        "agent 'openclaw' with runtime='swebench_container' requires "
                        "sandbox.name == 'swebench_container'"
                    )
                if not config.agent_config.get("openclaw_config_path"):
                    errors.append(
                        "agent 'openclaw' with runtime='swebench_container' requires "
                        "'openclaw_config_path' in agent_config"
                    )
            else:
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

        if (
            config.agent_name == "opencode"
            and str(config.agent_config.get("runtime", "")).strip() == "swebench_container"
            and config.sandbox_name != "swebench_container"
        ):
            errors.append(
                "agent 'opencode' with runtime='swebench_container' requires "
                "sandbox.name == 'swebench_container'"
            )

        if (
            config.agent_name == "swebench_docker"
            and str(config.agent_config.get("agent_type", "direct_llm")).strip() == "direct_llm"
        ):
            missing = [
                key
                for key in ("model", "api_base", "api_key")
                if not self._has_nonempty_value(config.agent_config.get(key))
            ]
            if missing:
                errors.append(
                    "agent 'swebench_docker' with agent_type 'direct_llm' requires "
                    f"{', '.join(missing)} in agent_config or OPENAI_* env defaults"
                )

        if config.agent_name == "swebench_docker":
            agent_type = str(config.agent_config.get("agent_type", "direct_llm")).strip() or "direct_llm"
            if agent_type in {"openclaw", "opencode"}:
                nested_env = config.agent_config.get("env", {})
                if not isinstance(nested_env, dict):
                    nested_env = {}
                missing = [
                    key
                    for key in ("OPENAI_MODEL_NAME", "OPENAI_BASE_URL", "OPENAI_API_KEY")
                    if not self._has_nonempty_value(nested_env.get(key))
                ]
                if missing:
                    errors.append(
                        "agent 'swebench_docker' with agent_type "
                        f"'{agent_type}' requires env.{', env.'.join(missing)} "
                        "or OPENAI_* env defaults"
                    )

        if config.agent_name in {
            "terminal_bench2_docker",
            "terminal_bench2_openclaw",
            "terminal_bench2_opencode",
        } and not self._has_nonempty_value(config.agent_config.get("api_base")):
            errors.append(
                f"agent '{config.agent_name}' requires 'api_base' in agent_config "
                "or OPENAI_BASE_URL env defaults"
            )

        if config.scorer_name == "swebench_pro":
            self._validate_existing_path(
                errors,
                scorer_name="swebench_pro",
                key="eval_script_path",
                value=config.scorer_config.get("eval_script_path"),
                expect_dir=False,
            )
            self._validate_existing_path(
                errors,
                scorer_name="swebench_pro",
                key="scripts_dir",
                value=config.scorer_config.get("scripts_dir"),
                expect_dir=True,
            )

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

    @staticmethod
    def _has_nonempty_value(value: object) -> bool:
        """Return True when a config field is meaningfully populated."""
        if value is None:
            return False
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped or stripped.upper() == "EMPTY":
                return False
            return not bool(
                re.fullmatch(
                    r"\$(?:\{[A-Za-z_][A-Za-z0-9_]*\}|[A-Za-z_][A-Za-z0-9_]*)",
                    stripped,
                )
            )
        return True

    @classmethod
    def _validate_existing_path(
        cls,
        errors: list[str],
        *,
        scorer_name: str,
        key: str,
        value: object,
        expect_dir: bool,
    ) -> None:
        """Append a human-readable error when a required scorer path is invalid."""
        if not cls._has_nonempty_value(value):
            errors.append(f"scorer '{scorer_name}' requires non-empty {key}")
            return
        raw = str(value).strip()
        path = Path(raw).expanduser()
        if not path.exists():
            errors.append(f"scorer '{scorer_name}' {key} does not exist: {raw}")
            return
        if expect_dir and not path.is_dir():
            errors.append(f"scorer '{scorer_name}' {key} must be a directory: {raw}")
        if not expect_dir and not path.is_file():
            errors.append(f"scorer '{scorer_name}' {key} must be a file: {raw}")
