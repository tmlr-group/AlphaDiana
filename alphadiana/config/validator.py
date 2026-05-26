from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from alphadiana.config.experiment_config import ExperimentConfig


class ConfigValidator:
    SANDBOX_REQUIRED_BENCHMARKS: set[str] = {"terminal_bench", "osworld"}
    OPENCODE_CONTROLLER_MODES: set[str] = {"host", "docker", "podman"}
    PODMAN_ROCK_SANDBOX_FIELDS: set[str] = {
        "admin_base_url",
        "proxy_base_url",
        "use_kata_runtime",
        "limit_cpus",
        "auto_clear_seconds",
    }

    OPENCLAW_RUNTIME_AGENTS = {"openclaw"}
    API_AGENTS = OPENCLAW_RUNTIME_AGENTS | {
        "direct_llm",
        "zeroclaw",
        "opencode",
        "terminal_bench2_docker",
        "terminal_bench2_opencode",
        "terminal_bench2_openclaw",
        "terminal_bench2_zeroclaw",
    }

    @staticmethod
    def _validate_int_field(errors: list[str], field_name: str, value: object) -> int | None:
        if isinstance(value, bool):
            errors.append(f"{field_name} must be an integer, got boolean")
            return None
        if isinstance(value, int):
            return value
        errors.append(f"{field_name} must be an integer, got {type(value).__name__}")
        return None

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
        max_concurrent = self._validate_int_field(errors, "max_concurrent", config.max_concurrent)
        if max_concurrent is not None:
            if max_concurrent < 1:
                errors.append("max_concurrent must be >= 1")
            if max_concurrent > 64:
                errors.append("max_concurrent should be <= 64 to avoid resource exhaustion")
        if config.benchmark_name == "imo_answerbench" and config.scorer_name != "imo_verify":
            errors.append(
                "benchmark 'imo_answerbench' must use scorer 'imo_verify'; "
                f"got '{config.scorer_name}'"
            )
        if config.benchmark_name in self.SANDBOX_REQUIRED_BENCHMARKS and not config.sandbox_name:
            errors.append(
                f"benchmark '{config.benchmark_name}' requires a sandbox "
                "(set sandbox_name to 'rock', 'local', or 'podman')"
            )

        if config.agent_name in self.API_AGENTS:
            runtime = str(config.agent_config.get("runtime", "")).strip()
            has_api_base = self._has_nonempty_value(config.agent_config.get("api_base"))
            has_auto_deploy = bool(
                config.agent_config.get("rock_agent_config_path")
                and config.agent_config.get("openclaw_config_path")
            )
            has_zeroclaw_auto_deploy = bool(config.agent_config.get("rock_image"))
            runtime_backend = str(config.agent_config.get("runtime_backend", "") or "").strip().lower()
            has_podman_agent_runtime = runtime_backend == "podman"

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
            elif config.agent_name == "opencode" and runtime == "swebench_container":
                if config.sandbox_name != "swebench_container":
                    errors.append(
                        "agent 'opencode' with runtime='swebench_container' requires "
                        "sandbox.name == 'swebench_container'"
                    )
            elif config.agent_name in self.OPENCLAW_RUNTIME_AGENTS:
                if not has_api_base and not has_auto_deploy and not has_podman_agent_runtime:
                    errors.append(
                        f"agent '{config.agent_name}' requires 'api_base' or "
                        "'rock_agent_config_path' + 'openclaw_config_path' in agent_config "
                        "(auto-deploy mode)"
                    )
            elif config.agent_name == "zeroclaw":
                if not has_api_base and not has_zeroclaw_auto_deploy and not has_podman_agent_runtime:
                    errors.append(
                        "agent 'zeroclaw' requires 'api_base' in agent_config or "
                        "'rock_image' for ROCK auto-deploy mode"
                    )
            elif not has_api_base:
                errors.append(
                    f"agent '{config.agent_name}' requires 'api_base' in agent_config"
                )
            if config.agent_name == "direct_llm" and not self._has_nonempty_value(
                config.agent_config.get("model")
            ):
                errors.append("agent 'direct_llm' requires non-empty 'model' in agent_config")
        if config.agent_name == "opencode":
            controller_mode = (
                str(config.agent_config.get("controller_mode", "host") or "host").strip().lower()
            )
            if controller_mode not in self.OPENCODE_CONTROLLER_MODES:
                supported = ", ".join(sorted(self.OPENCODE_CONTROLLER_MODES))
                errors.append(
                    "agent 'opencode' controller_mode must be one of "
                    f"{supported}; got '{controller_mode}'"
                )
            if controller_mode == "docker":
                self._validate_controller_image(
                    errors,
                    agent_name="opencode",
                    image=config.agent_config.get("controller_image"),
                )

        if config.agent_name == "terminal_bench2_opencode":
            controller_mode = (
                str(config.agent_config.get("controller_mode", "host") or "host").strip().lower()
            )
            if controller_mode == "docker":
                self._validate_controller_image(
                    errors,
                    agent_name="terminal_bench2_opencode",
                    image=config.agent_config.get("controller_image"),
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
            if agent_type in {"openclaw", "opencode", "zeroclaw"}:
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
            "terminal_bench2_zeroclaw",
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

        num_samples = self._validate_int_field(errors, "num_samples", getattr(config, "num_samples", 1))
        if config.benchmark_name == "terminal_bench2":
            self._validate_terminal_bench2_tasks_dir(errors, config)

        if (
            config.benchmark_name in {"hle", "mmmu_pro"}
            and config.metadata.get("supports_multimodal") is False
        ):
            errors.append(
                f"benchmark '{config.benchmark_name}' requires a multimodal-capable model/config "
                "(metadata.supports_multimodal=false)"
            )

        if num_samples is not None and num_samples < 1:
            errors.append("num_samples must be >= 1")
        task_retries = self._validate_int_field(errors, "task_retries", getattr(config, "task_retries", 0))
        if task_retries is not None and task_retries < 0:
            errors.append("task_retries must be >= 0")

        return errors

    def warnings(self, config: ExperimentConfig) -> list[str]:
        """Return non-fatal configuration warnings."""
        warnings: list[str] = []
        if config.sandbox_name == "podman":
            for key in sorted(config.sandbox_config):
                key_text = str(key)
                if key_text in self.PODMAN_ROCK_SANDBOX_FIELDS or key_text.startswith("rock_"):
                    warnings.append(
                        "sandbox.name=podman ignores ROCK-specific sandbox.config."
                        f"{key_text}; remove it or use sandbox.name=rock"
                    )
        return warnings

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

    @classmethod
    def _validate_terminal_bench2_tasks_dir(
        cls,
        errors: list[str],
        config: ExperimentConfig,
    ) -> None:
        tasks_dir_raw = str(
            config.benchmark_config.get("tasks_dir")
            or os.environ.get("TERMINAL_BENCH2_DIR", "")
            or ""
        ).strip()
        if not tasks_dir_raw:
            errors.append(
                "benchmark 'terminal_bench2' requires benchmark.config.tasks_dir "
                "or TERMINAL_BENCH2_DIR"
            )
            return
        if not cls._has_nonempty_value(tasks_dir_raw):
            errors.append(
                "benchmark 'terminal_bench2' got an unresolved tasks_dir placeholder; "
                "set benchmark.config.tasks_dir or TERMINAL_BENCH2_DIR"
            )
            return
        tasks_dir = Path(tasks_dir_raw).expanduser()
        if not tasks_dir.exists():
            errors.append(f"benchmark 'terminal_bench2' tasks_dir does not exist: {tasks_dir_raw}")
            return
        if not tasks_dir.is_dir():
            errors.append(f"benchmark 'terminal_bench2' tasks_dir must be a directory: {tasks_dir_raw}")

    @staticmethod
    def _validate_controller_image(
        errors: list[str],
        *,
        agent_name: str,
        image: object,
    ) -> None:
        raw = str(image or "").strip()
        if not raw:
            errors.append(f"agent '{agent_name}' controller_mode=docker requires controller_image")
            return
        try:
            result = subprocess.run(
                ["docker", "image", "inspect", raw],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except Exception as exc:
            errors.append(
                f"agent '{agent_name}' controller_image preflight failed for '{raw}': {exc}"
            )
            return
        if result.returncode != 0:
            errors.append(
                f"agent '{agent_name}' controller_image not present locally: {raw}"
            )
