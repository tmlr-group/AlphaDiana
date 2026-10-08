"""Registry for post-run evaluation metrics."""

from __future__ import annotations

from typing import Type

from alphadiana.metrics.base import EvaluationMetric


class MetricRegistry:
    _registry: dict[str, Type[EvaluationMetric]] = {}

    @classmethod
    def register(cls, name: str, metric_cls: Type[EvaluationMetric]) -> None:
        cls._registry[name] = metric_cls

    @classmethod
    def get(cls, name: str) -> Type[EvaluationMetric]:
        if name not in cls._registry:
            raise KeyError(f"Metric '{name}' not found. Available: {cls.list()}")
        return cls._registry[name]

    @classmethod
    def list(cls) -> list[str]:
        return sorted(cls._registry)


def register_metric(name: str):
    def decorator(metric_cls: Type[EvaluationMetric]):
        MetricRegistry.register(name, metric_cls)
        return metric_cls

    return decorator
