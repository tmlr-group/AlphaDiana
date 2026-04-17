from alphadiana.benchmark.base import Benchmark, BenchmarkTask
from alphadiana.benchmark.registry import BenchmarkRegistry
import alphadiana.benchmark.mmmu_pro  # noqa: F401
import alphadiana.benchmark.gpqa  # noqa: F401

__all__ = ["Benchmark", "BenchmarkTask", "BenchmarkRegistry"]
