"""FrontierMath benchmark loader."""
from __future__ import annotations

from alphadiana.benchmark.base import Benchmark, BenchmarkTask, load_dataset_with_retry
from alphadiana.benchmark.registry import BenchmarkRegistry


class FrontierMathBenchmark(Benchmark):
    """FrontierMath problems. Numeric with tolerance."""

    name = "frontier_math"

    def load_tasks(self, config: dict) -> list[BenchmarkTask]:
        try:
            from datasets import load_dataset
        except ImportError:
            raise RuntimeError(
                "The 'datasets' library is required for FrontierMath benchmark. "
                "Install with: pip install datasets"
            )

        split = config.get("split", "test")
        dataset = load_dataset_with_retry("AI-MO/FrontierMath", split=split)

        tasks: list[BenchmarkTask] = []
        for idx, item in enumerate(dataset):
            tasks.append(BenchmarkTask(
                task_id=f"frontier_math_{idx}",
                problem=item.get("problem", item.get("question", "")),
                ground_truth=item.get("answer", ""),
                metadata={
                    "difficulty": item.get("difficulty", ""),
                    "domain": item.get("domain", ""),
                    "source": "frontier_math",
                },
            ))
        return tasks

    def default_scorer(self) -> str:
        return "numeric"


BenchmarkRegistry.register("frontier_math", FrontierMathBenchmark)
