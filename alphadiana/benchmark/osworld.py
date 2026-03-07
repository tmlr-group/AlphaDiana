"""OSWorld benchmark loader."""
from __future__ import annotations

import json

from alphadiana.benchmark.base import Benchmark, BenchmarkTask
from alphadiana.benchmark.registry import BenchmarkRegistry


class OSWorldBenchmark(Benchmark):
    """Requires sandbox. Desktop environment tasks."""

    name = "osworld"

    def load_tasks(self, config: dict) -> list[BenchmarkTask]:
        data_path = config.get("data_path")
        if not data_path:
            raise ValueError("osworld requires 'data_path' in benchmark config")

        with open(data_path) as f:
            data = json.load(f)

        items = data if isinstance(data, list) else data.get("tasks", [])

        tasks: list[BenchmarkTask] = []
        for idx, item in enumerate(items):
            tasks.append(BenchmarkTask(
                task_id=item.get("task_id", f"osworld_{idx}"),
                problem=item.get("problem", item.get("instruction", "")),
                ground_truth=item.get("ground_truth", item.get("answer", "")),
                metadata=item.get("metadata", {}),
            ))
        return tasks

    def default_scorer(self) -> str:
        return "llm_judge"


BenchmarkRegistry.register("osworld", OSWorldBenchmark)
