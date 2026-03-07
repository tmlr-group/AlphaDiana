"""Benchmark registry for discovering benchmarks by name."""

from __future__ import annotations

from typing import Type

from alphadiana.benchmark.base import Benchmark


class BenchmarkRegistry:
    """Global registry for benchmark classes."""

    _registry: dict[str, Type[Benchmark]] = {}

    @classmethod
    def register(cls, name: str, benchmark_cls: Type[Benchmark]) -> None:
        cls._registry[name] = benchmark_cls

    @classmethod
    def get(cls, name: str) -> Type[Benchmark]:
        if name not in cls._registry:
            raise KeyError(f"Benchmark '{name}' not found. Available: {cls.list()}")
        return cls._registry[name]

    @classmethod
    def list(cls) -> list[str]:
        return sorted(cls._registry.keys())


def register_benchmark(name: str):
    """Decorator to register a Benchmark implementation."""
    def decorator(cls):
        BenchmarkRegistry.register(name, cls)
        return cls
    return decorator
