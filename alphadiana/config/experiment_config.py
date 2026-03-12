from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


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
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.run_id:
            self.run_id = uuid.uuid4().hex[:12]

    @classmethod
    def from_yaml(
        cls,
        path: str,
        overrides: dict | None = None,
    ) -> ExperimentConfig:
        with open(path) as f:
            data = yaml.safe_load(f)

        if overrides:
            data = deep_merge(data, overrides)

        agent = data.get("agent", {})
        benchmark = data.get("benchmark", {})
        sandbox = data.get("sandbox")
        scorer = data.get("scorer", {})

        return cls(
            run_id=data.get("run_id", ""),
            agent_name=agent.get("name", ""),
            agent_version=agent.get("version", ""),
            agent_config=agent.get("config", {}),
            benchmark_name=benchmark.get("name", ""),
            benchmark_config=benchmark.get("config", {}),
            sandbox_name=sandbox.get("name") if isinstance(sandbox, dict) else None,
            sandbox_config=sandbox.get("config", {}) if isinstance(sandbox, dict) else {},
            scorer_name=scorer.get("name", ""),
            scorer_config=scorer.get("config", {}),
            max_concurrent=data.get("max_concurrent", 1),
            output_dir=data.get("output_dir", "./results"),
            metadata=data.get("metadata", {}),
        )
