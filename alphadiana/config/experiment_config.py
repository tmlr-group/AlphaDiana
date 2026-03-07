from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path

import yaml


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
    def from_yaml(cls, path: str) -> ExperimentConfig:
        with open(path) as f:
            data = yaml.safe_load(f)

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
