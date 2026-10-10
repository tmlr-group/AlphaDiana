"""Dataset contracts; built-ins are discovered explicitly by engine.components."""
from alphadiana.benchmarks.base import Benchmark, BenchmarkTask
from alphadiana.engine.components import BenchmarkRegistry

__all__ = ["Benchmark", "BenchmarkTask", "BenchmarkRegistry"]
