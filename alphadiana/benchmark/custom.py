"""Custom benchmark: define problems directly in YAML config."""
from __future__ import annotations

from alphadiana.benchmark.base import Benchmark, BenchmarkTask
from alphadiana.benchmark.registry import BenchmarkRegistry


class CustomBenchmark(Benchmark):
    """Benchmark whose problems are defined directly in the config.

    Config keys:
        problems: list of dicts, each with:
            id:      unique task identifier (required)
            problem: problem text (required)
            answer:  ground truth answer (required)
    """

    name = "custom"

    def load_tasks(self, config: dict) -> list[BenchmarkTask]:
        problems = config.get("problems", [])
        if not problems:
            raise ValueError("CustomBenchmark requires a non-empty 'problems' list in config.")

        tasks: list[BenchmarkTask] = []
        for item in problems:
            task_id = str(item.get("id", ""))
            problem = str(item.get("problem", ""))
            answer = str(item.get("answer", ""))
            if not task_id or not problem:
                raise ValueError(f"Each problem must have 'id' and 'problem' fields. Got: {item}")
            tasks.append(BenchmarkTask(
                task_id=task_id,
                problem=problem,
                ground_truth=answer,
            ))
        return tasks

    def default_scorer(self) -> str:
        return "numeric"


BenchmarkRegistry.register("custom", CustomBenchmark)
