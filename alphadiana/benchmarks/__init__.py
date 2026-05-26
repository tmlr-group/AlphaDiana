from alphadiana.benchmarks.base import Benchmark, BenchmarkTask
from alphadiana.benchmarks.registry import BenchmarkRegistry
import alphadiana.benchmarks.mmmu_pro.benchmark  # noqa: F401
import alphadiana.benchmarks.gpqa.benchmark  # noqa: F401

__all__ = ["Benchmark", "BenchmarkTask", "BenchmarkRegistry"]
