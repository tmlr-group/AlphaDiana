"""MATH benchmark loader."""
from __future__ import annotations

from alphadiana.benchmark.base import Benchmark, BenchmarkTask, load_dataset_with_retry
from alphadiana.benchmark.registry import BenchmarkRegistry


class MATHBenchmark(Benchmark):
    """MATH dataset (Hendrycks). LaTeX answer extraction."""

    name = "math"

    def load_tasks(self, config: dict) -> list[BenchmarkTask]:
        try:
            from datasets import load_dataset
        except ImportError:
            raise RuntimeError(
                "The 'datasets' library is required for MATH benchmark. "
                "Install with: pip install datasets"
            )

        split = config.get("split", "test")
        subset = config.get("subset", None)
        dataset_name = "hendrycks/competition_math"

        if subset:
            dataset = load_dataset_with_retry(dataset_name, subset, split=split)
        else:
            dataset = load_dataset_with_retry(dataset_name, split=split)

        tasks: list[BenchmarkTask] = []
        for idx, item in enumerate(dataset):
            tasks.append(BenchmarkTask(
                task_id=f"math_{idx}",
                problem=item.get("problem", ""),
                ground_truth=item.get("solution", item.get("answer", "")),
                metadata={
                    "level": item.get("level", ""),
                    "type": item.get("type", ""),
                    "source": "math",
                },
            ))
        return tasks

    def default_scorer(self) -> str:
        return "exact_match"


BenchmarkRegistry.register("math", MATHBenchmark)
