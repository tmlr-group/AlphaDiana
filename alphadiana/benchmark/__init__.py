from alphadiana.benchmark.base import Benchmark, BenchmarkTask
from alphadiana.benchmark.registry import BenchmarkRegistry

# Register all built-in benchmarks so they are available by name.
import alphadiana.benchmark.aime  # noqa: F401
import alphadiana.benchmark.custom  # noqa: F401
import alphadiana.benchmark.swe_bench  # noqa: F401
import alphadiana.benchmark.mmmu_pro  # noqa: F401
import alphadiana.benchmark.gpqa  # noqa: F401

__all__ = ["Benchmark", "BenchmarkTask", "BenchmarkRegistry"]
