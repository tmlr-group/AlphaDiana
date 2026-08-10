from __future__ import annotations

import copy
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]

_ENV_PLACEHOLDER_RE = re.compile(
    r"^\s*(?:\$\{[A-Za-z_][A-Za-z0-9_]*\}|\$[A-Za-z_][A-Za-z0-9_]*)\s*$"
)


def _expand_env_vars(obj: Any) -> Any:
    """Recursively expand $VAR and ${VAR} in string values."""
    if isinstance(obj, str):
        return os.path.expandvars(obj)
    if isinstance(obj, dict):
        return {k: _expand_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env_vars(item) for item in obj]
    return obj


def _clear_unresolved_env_placeholders(obj: Any) -> Any:
    """Treat fully unresolved env-var placeholders as empty strings."""
    if isinstance(obj, str):
        return "" if _ENV_PLACEHOLDER_RE.fullmatch(obj) else obj
    if isinstance(obj, dict):
        return {k: _clear_unresolved_env_placeholders(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clear_unresolved_env_placeholders(item) for item in obj]
    return obj


def _is_blank_config_value(value: Any) -> bool:
    """Return True when a config field is blank or still an unresolved env placeholder."""
    if value is None:
        return True
    if isinstance(value, str):
        stripped = value.strip()
        return (
            not stripped
            or stripped.upper() == "EMPTY"
            or bool(_ENV_PLACEHOLDER_RE.fullmatch(stripped))
        )
    return False


def _resolve_output_dir(value: Any) -> str:
    raw = "./results" if _is_blank_config_value(value) else str(value)
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    return str(path.resolve())


def _apply_agent_env_defaults(agent_name: str, agent_config: Any) -> dict:
    """Populate agent config fields from environment when the YAML leaves them blank."""
    if not isinstance(agent_config, dict):
        return {}

    resolved = copy.deepcopy(agent_config)

    if agent_name in {
        "direct_llm",
        "openclaw",
        "zeroclaw",
        "opencode",
        "terminal_bench2_docker",
        "terminal_bench2_opencode",
        "terminal_bench2_openclaw",
        "terminal_bench2_zeroclaw",
    }:
        env_defaults = {
            "api_base": "OPENAI_BASE_URL",
            "api_key": "OPENAI_API_KEY",
        }
        if agent_name == "openclaw" and (
            resolved.get("runtime")
            or str(resolved.get("runtime_backend", "") or "").strip().lower() == "podman"
            or (
                resolved.get("rock_agent_config_path")
                and resolved.get("openclaw_config_path")
            )
        ):
            # In auto-deploy mode OPENAI_BASE_URL is the provider endpoint used
            # inside the sandbox, not an already-running OpenClaw gateway.  The
            # runner must leave api_base empty so it creates the gateway first.
            env_defaults.pop("api_base")
        if agent_name in {
            "direct_llm",
            "openclaw",
            "zeroclaw",
            "terminal_bench2_docker",
            "terminal_bench2_zeroclaw",
        }:
            env_defaults["model"] = "OPENAI_MODEL_NAME"
        if agent_name in {
            "opencode",
            "terminal_bench2_opencode",
            "terminal_bench2_openclaw",
            "terminal_bench2_zeroclaw",
        }:
            env_defaults["model_name"] = "OPENAI_MODEL_NAME"

        for key, env_var in env_defaults.items():
            if not _is_blank_config_value(resolved.get(key, "")):
                continue
            env_value = os.environ.get(env_var, "").strip()
            if env_value:
                resolved[key] = env_value

    if agent_name == "swebench_docker":
        agent_type = str(resolved.get("agent_type", "direct_llm")).strip() or "direct_llm"
        if agent_type == "direct_llm":
            env_defaults = {
                "model": "OPENAI_MODEL_NAME",
                "api_base": "OPENAI_BASE_URL",
                "api_key": "OPENAI_API_KEY",
            }
            for key, env_var in env_defaults.items():
                if not _is_blank_config_value(resolved.get(key, "")):
                    continue
                env_value = os.environ.get(env_var, "").strip()
                if env_value:
                    resolved[key] = env_value
        elif agent_type in {"openclaw", "opencode", "zeroclaw"}:
            env_defaults = {
                "OPENAI_MODEL_NAME": "OPENAI_MODEL_NAME",
                "OPENAI_BASE_URL": "OPENAI_BASE_URL",
                "OPENAI_API_KEY": "OPENAI_API_KEY",
            }
            nested_env = resolved.get("env", {})
            if not isinstance(nested_env, dict):
                nested_env = {}
            nested_env = copy.deepcopy(nested_env)
            for key, env_var in env_defaults.items():
                if not _is_blank_config_value(nested_env.get(key, "")):
                    continue
                env_value = os.environ.get(env_var, "").strip()
                if env_value:
                    nested_env[key] = env_value
            resolved["env"] = nested_env

    return resolved


def deep_merge(base: dict, override: dict) -> dict:
    """Deep merge *override* into *base* (non-mutating)."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def parse_override(s: str) -> dict:
    """Parse ``'a.b.c=value'`` into ``{'a': {'b': {'c': value}}}``."""
    if "=" not in s:
        raise ValueError(f"Invalid override (missing '='): {s}")
    key_path, raw_value = s.split("=", 1)
    key_path = key_path.strip()
    if not key_path:
        raise ValueError("Invalid override: empty key path")
    if key_path.startswith(".") or key_path.endswith(".") or ".." in key_path:
        raise ValueError(f"Invalid override key path: {key_path}")
    value: Any = raw_value
    if raw_value.lower() in ("true", "false"):
        value = raw_value.lower() == "true"
    else:
        try:
            value = int(raw_value)
        except ValueError:
            try:
                value = float(raw_value)
            except ValueError:
                pass
    parts = key_path.split(".")
    if any(not part.strip() for part in parts):
        raise ValueError(f"Invalid override key path: {key_path}")
    result: dict = {}
    current = result
    for part in parts[:-1]:
        current[part] = {}
        current = current[part]
    current[parts[-1]] = value
    return result


@dataclass
class ExperimentConfig:
    agent_name: str
    agent_version: str
    benchmark_name: str
    scorer_name: str
    run_id: str = ""
    agent_config: dict = field(default_factory=dict)
    benchmark_config: dict = field(default_factory=dict)
    sandbox_name: str | None = None
    sandbox_config: dict = field(default_factory=dict)
    scorer_config: dict = field(default_factory=dict)
    max_concurrent: int = 1
    output_dir: str = "./results"
    redo_all: bool = False
    sandbox_retries: int = 1
    num_samples: int = 1
    task_retries: int = 1
    task_retry_on_recoverable_only: bool = False
    strict_report: bool = False
    strict_isolation: bool = False
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.run_id:
            self.run_id = uuid.uuid4().hex[:12]
        self.run_id = self.run_id.replace("/", "_")

    @classmethod
    def from_yaml(
        cls,
        path: str,
        overrides: dict | None = None,
    ) -> ExperimentConfig:
        with open(path) as f:
            data = yaml.safe_load(f)
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ValueError("top-level YAML document must be a mapping")

        data = _expand_env_vars(data)

        if overrides:
            data = deep_merge(data, overrides)

        data = _clear_unresolved_env_placeholders(data)

        agent = data.get("agent", {})
        benchmark = data.get("benchmark", {})
        sandbox = data.get("sandbox")
        scorer = data.get("scorer", {})

        agent_name = agent.get("name", "")
        agent_config = _apply_agent_env_defaults(agent_name, agent.get("config", {}))

        return cls(
            run_id=data.get("run_id", ""),
            agent_name=agent_name,
            agent_version=agent.get("version", ""),
            agent_config=agent_config,
            benchmark_name=benchmark.get("name", ""),
            benchmark_config=benchmark.get("config", {}),
            sandbox_name=sandbox.get("name") if isinstance(sandbox, dict) else None,
            sandbox_config=sandbox.get("config", {}) if isinstance(sandbox, dict) else {},
            scorer_name=scorer.get("name", ""),
            scorer_config=scorer.get("config", {}),
            max_concurrent=data.get("max_concurrent", 1),
            output_dir=_resolve_output_dir(data.get("output_dir", "./results")),
            redo_all=data.get("redo_all", False),
            sandbox_retries=data.get("sandbox_retries", 1),
            num_samples=data.get("num_samples", 1),
            task_retries=data.get("task_retries", 0),
            task_retry_on_recoverable_only=data.get("task_retry_on_recoverable_only", False),
            strict_report=data.get("strict_report", False),
            strict_isolation=data.get("strict_isolation", False),
            metadata=data.get("metadata", {}),
        )
