"""Typed component registries. Built-in imports belong to registry_loader."""

from __future__ import annotations
from typing import TYPE_CHECKING, Generic, TypeVar

if TYPE_CHECKING:
    from alphadiana.benchmarks.base import Benchmark
    from alphadiana.harness.base import Agent
    from alphadiana.engine.sandbox.base import Sandbox
    from alphadiana.scorer.base import Scorer
    from alphadiana.metrics.base import EvaluationMetric

T = TypeVar("T")


class _Registry(Generic[T]):
    _registry: dict[str, type[T]]
    _label: str

    @classmethod
    def register(cls, name: str, implementation: type[T]) -> None:
        cls._registry[name] = implementation

    @classmethod
    def _missing_message(cls, name: str) -> str:
        return f"{cls._label} '{name}' not found. Available: {cls.list()}"

    @classmethod
    def get(cls, name: str) -> type[T]:
        if name not in cls._registry:
            raise KeyError(cls._missing_message(name))
        return cls._registry[name]

    @classmethod
    def list(cls) -> list[str]:
        return sorted(cls._registry)

    @classmethod
    def decorator(cls, name: str):
        def decorate(implementation: type[T]) -> type[T]:
            cls.register(name, implementation)
            return implementation

        return decorate


class BenchmarkRegistry(_Registry["Benchmark"]):
    """Independent benchmark namespace; replacement registration is supported."""

    _registry: dict[str, type[Benchmark]] = {}
    _label = "Benchmark"

    @classmethod
    def register(cls, name: str, benchmark_cls: type[Benchmark]) -> None:
        # Preserve the public keyword argument while sharing the mechanics.
        super().register(name, benchmark_cls)


def register_benchmark(name: str):
    """Register a benchmark implementation under its stable configuration key."""
    return BenchmarkRegistry.decorator(name)


class AgentRegistry(_Registry["Agent"]):
    """Independent agent namespace; replacement registration is supported."""

    _registry: dict[str, type[Agent]] = {}
    _label = "Agent"

    @classmethod
    def register(cls, name: str, agent_cls: type[Agent]) -> None:
        # Preserve the public keyword argument while sharing the mechanics.
        super().register(name, agent_cls)


def register_agent(name: str):
    """Register a agent implementation under its stable configuration key."""
    return AgentRegistry.decorator(name)


class SandboxRegistry(_Registry["Sandbox"]):
    """Independent sandbox namespace; replacement registration is supported."""

    _registry: dict[str, type[Sandbox]] = {}
    _label = "Sandbox"

    @classmethod
    def register(cls, name: str, sandbox_cls: type[Sandbox]) -> None:
        # Preserve the public keyword argument while sharing the mechanics.
        super().register(name, sandbox_cls)


def register_sandbox(name: str):
    """Register a sandbox implementation under its stable configuration key."""
    return SandboxRegistry.decorator(name)


class ScorerRegistry(_Registry["Scorer"]):
    """Independent scorer namespace; replacement registration is supported."""

    _registry: dict[str, type[Scorer]] = {}
    _label = "Scorer"

    @classmethod
    def register(cls, name: str, scorer_cls: type[Scorer]) -> None:
        # Preserve the public keyword argument while sharing the mechanics.
        super().register(name, scorer_cls)

    @classmethod
    def _missing_message(cls, name: str) -> str:
        available = ", ".join(cls.list()) or "(none)"
        return f"Scorer '{name}' is not registered. Available: {available}"


def register_scorer(name: str):
    """Register a scorer implementation under its stable configuration key."""
    return ScorerRegistry.decorator(name)


class MetricRegistry(_Registry["EvaluationMetric"]):
    """Independent metric namespace; replacement registration is supported."""

    _registry: dict[str, type[EvaluationMetric]] = {}
    _label = "Metric"

    @classmethod
    def register(cls, name: str, metric_cls: type[EvaluationMetric]) -> None:
        # Preserve the public keyword argument while sharing the mechanics.
        super().register(name, metric_cls)


def register_metric(name: str):
    """Register a metric implementation under its stable configuration key."""
    return MetricRegistry.decorator(name)
