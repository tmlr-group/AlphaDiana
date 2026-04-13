from __future__ import annotations

import copy
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _expand_env_vars(obj: Any) -> Any:
    """Recursively expand $VAR and ${VAR} in string values."""
    if isinstance(obj, str):
        return os.path.expandvars(obj)
    if isinstance(obj, dict):
        return {k: _expand_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env_vars(item) for item in obj]
    return obj


def _apply_agent_env_defaults(agent_name: str, agent_config: Any) -> dict:
    """Populate agent config fields from environment when the YAML leaves them blank."""
    if not isinstance(agent_config, dict):
        return {}

    resolved = copy.deepcopy(agent_config)

    if agent_name == "direct_llm":
        env_defaults = {
            "model": "OPENAI_MODEL_NAME",
            "api_base": "OPENAI_BASE_URL",
            "api_key": "OPENAI_API_KEY",
        }
        for key, env_var in env_defaults.items():
            current = resolved.get(key, "")
            if current is None:
                current = ""
            if isinstance(current, str):
                current = current.strip()
                if current.upper() == "EMPTY":
                    current = ""
            if current:
                continue
            env_value = os.environ.get(env_var, "").strip()
            if env_value:
                resolved[key] = env_value

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
    value: Any = raw_value
    # Try to coerce to int/float/bool
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

        data = _expand_env_vars(data)

        if overrides:
            data = deep_merge(data, overrides)

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
            output_dir=data.get("output_dir", "./results"),
            redo_all=data.get("redo_all", False),
            sandbox_retries=data.get("sandbox_retries", 1),
            num_samples=data.get("num_samples", 1),
            task_retries=data.get("task_retries", 0),
            metadata=data.get("metadata", {}),
        )
