"""HLE (Humanity's Last Exam) benchmark loader."""
from __future__ import annotations

from alphadiana.benchmark.base import Benchmark, BenchmarkTask
from alphadiana.benchmark.registry import BenchmarkRegistry


class HLEBenchmark(Benchmark):
    """Humanity's Last Exam. Mixed answer types."""

    name = "hle"

    def load_tasks(self, config: dict) -> list[BenchmarkTask]:
        try:
            from datasets import load_dataset
        except ImportError:
            raise RuntimeError(
                "The 'datasets' library is required for HLE benchmark. "
                "Install with: pip install datasets"
            )

        split = config.get("split", "test")
        dataset = load_dataset("cais/hle", split=split)

        tasks: list[BenchmarkTask] = []
        for idx, item in enumerate(dataset):
            tasks.append(BenchmarkTask(
                task_id=f"hle_{idx}",
                problem=item.get("question", item.get("problem", "")),
                ground_truth=item.get("answer", ""),
                metadata={
                    "category": item.get("category", ""),
                    "source": "hle",
                },
            ))
        return tasks

    def default_scorer(self) -> str:
        return "llm_judge"


BenchmarkRegistry.register("hle", HLEBenchmark)
