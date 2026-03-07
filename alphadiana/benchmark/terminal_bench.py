"""TerminalBench benchmark loader."""
from __future__ import annotations

import json

from alphadiana.benchmark.base import Benchmark, BenchmarkTask
from alphadiana.benchmark.registry import BenchmarkRegistry


class TerminalBenchBenchmark(Benchmark):
    """Requires sandbox. Evaluates terminal interactions."""

    name = "terminal_bench"

    def load_tasks(self, config: dict) -> list[BenchmarkTask]:
        data_path = config.get("data_path")
        if not data_path:
            raise ValueError("terminal_bench requires 'data_path' in benchmark config")

        tasks: list[BenchmarkTask] = []
        with open(data_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)
                tasks.append(BenchmarkTask(
                    task_id=item["task_id"],
                    problem=item["problem"],
                    ground_truth=item["ground_truth"],
                    metadata=item.get("metadata", {}),
                ))
        return tasks

    def default_scorer(self) -> str:
        return "exact_match"


BenchmarkRegistry.register("terminal_bench", TerminalBenchBenchmark)
