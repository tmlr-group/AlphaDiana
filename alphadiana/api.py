"""Typed composition over the same registries and Runner used by the CLI.

Factories describe components without starting runtimes. Register custom components
with the existing registries; this layer does not dispatch on benchmark identities.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from alphadiana.engine.config.experiment_config import ExperimentConfig


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentSpec:
    name: str
    version: str = ""
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EnvironmentSpec:
    name: str
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScorerSpec:
    name: str
    config: dict[str, Any] = field(default_factory=dict)


def dataset(path: str, *, benchmark: str | None = None, **config: Any) -> DatasetSpec:
    """Select a registered dataset adapter, optionally with an explicit source.

    ``dataset('aime', dataset='MathArena/aime_2026')`` and
    ``dataset('MathArena/aime_2026', benchmark='aime')`` are equivalent.
    Dataset schemas are not guessed from a hub path.
    """
    if benchmark is not None:
        if "dataset" in config:
            raise ValueError("Provide dataset source through path or dataset, not both")
        config["dataset"] = path
    return DatasetSpec(benchmark or path, deepcopy(config))


def agent(
    harness: str, *, model: str | None = None, version: str = "", **config: Any
) -> AgentSpec:
    """Select a registered harness. Version is explicit, never stripped from a name."""
    if model is not None:
        config["model"] = model
    return AgentSpec(harness, version, deepcopy(config))


def environment(backend: str, **config: Any) -> EnvironmentSpec:
    """Select an explicitly registered environment backend."""
    return EnvironmentSpec(backend, deepcopy(config))


def scorer(metric: str, **config: Any) -> ScorerSpec:
    return ScorerSpec(metric, deepcopy(config))


class EvaluationRunner:
    """Reusable execution recipe; each compute owns its runtime lifecycle."""

    def __init__(self, env: EnvironmentSpec | None = None, **options: Any) -> None:
        if env is not None and not isinstance(env, EnvironmentSpec):
            raise TypeError(
                "env must be environment(backend=...), or None; choose a concrete backend"
            )
        self.env = deepcopy(env)
        self.options = deepcopy(options)
        self.options.setdefault("task_retries", 0)

    def configuration(
        self, *, agent: AgentSpec, data: DatasetSpec, scorer: ScorerSpec
    ) -> ExperimentConfig:
        """Build the canonical CLI configuration without executing anything."""
        for value, expected in (
            (agent, AgentSpec),
            (data, DatasetSpec),
            (scorer, ScorerSpec),
        ):
            if not isinstance(value, expected):
                raise TypeError(
                    f"Expected {expected.__name__}, got {type(value).__name__}"
                )
        return ExperimentConfig(
            agent_name=agent.name,
            agent_version=agent.version,
            agent_config=deepcopy(agent.config),
            benchmark_name=data.name,
            benchmark_config=deepcopy(data.config),
            scorer_name=scorer.name,
            scorer_config=deepcopy(scorer.config),
            sandbox_name=self.env.name if self.env else None,
            sandbox_config=deepcopy(self.env.config) if self.env else {},
            **deepcopy(self.options),
        )

    def compute(self, *, agent: AgentSpec, data: DatasetSpec, scorer: ScorerSpec):
        """Execute and persist evidence, releasing resources even on failure."""
        from alphadiana.artifacts import Artifacts
        from alphadiana.benchmarks.registry import BenchmarkRegistry
        from alphadiana.engine.components import register_builtin_components
        from alphadiana.engine.config.validator import ConfigValidator
        from alphadiana.engine.runner import Runner
        from alphadiana.engine.sandbox.registry import SandboxRegistry
        from alphadiana.harness.registry import AgentRegistry
        from alphadiana.scorer.registry import ScorerRegistry

        config = self.configuration(agent=agent, data=data, scorer=scorer)
        register_builtin_components()
        # Resolve everything before starting a model, session or dataset download.
        for registry, name in (
            (BenchmarkRegistry, config.benchmark_name),
            (AgentRegistry, config.agent_name),
            (ScorerRegistry, config.scorer_name),
        ):
            registry.get(name)
        if config.sandbox_name:
            SandboxRegistry.get(config.sandbox_name)
        errors = ConfigValidator().validate(config)
        if errors:
            raise ValueError("Invalid evaluation configuration: " + "; ".join(errors))
        execution = Runner(config)
        try:
            execution.setup()
            execution.run()
        finally:
            execution.teardown()
        artifacts = Artifacts(Path(config.output_dir) / f"{config.run_id}.jsonl")
        records = artifacts.records()
        if not any(record.get("score_status") == "valid_scored" for record in records):
            raise RuntimeError(
                f"Evaluation produced no valid scored results; evidence: {artifacts.path}"
            )
        return artifacts


def runner(env: EnvironmentSpec | None = None, **options: Any) -> EvaluationRunner:
    return EvaluationRunner(env, **options)


def analyzer(artifacts, **options: Any):
    """Open one or multiple runs for structured measurement and figure export."""
    from alphadiana.analysis.analyzer import Analyzer

    return Analyzer(artifacts, **options)
