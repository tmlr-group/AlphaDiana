"""MoltBench benchmark: multi-round GPU kernel optimization evaluation.

Loads KernelBench problems and evaluates agents on iterative optimization
inside GPU-enabled Docker containers.
"""

from __future__ import annotations

import logging

from alphadiana.benchmark.base import Benchmark, BenchmarkTask
from alphadiana.benchmark.registry import BenchmarkRegistry
from alphadiana.benchmark.moltbench_utils import (
    load_kernelbench_problems,
    build_task_prompt,
    build_task_json,
    fix_super_bug,
)

logger = logging.getLogger(__name__)


class MoltBenchBenchmark(Benchmark):
    """KernelBench iterative optimization benchmark.

    Config keys:
        levels: list[int] — KernelBench levels to include (default: [1])
        problems: str | None — problem range like "1-20" or "1,3,5" (default: all)
        max_rounds: int — max optimization rounds per problem (default: 10)
        max_tasks: int | None — limit total number of tasks (optional)
        backend: str — "triton" or "cuda" (default: "triton")
        source: str — dataset source (default: "hf")
    """

    name = "moltbench"

    def load_tasks(self, config: dict) -> list[BenchmarkTask]:
        levels = config.get("levels", [1])
        problems_spec = config.get("problems")
        max_rounds = int(config.get("max_rounds", 10))
        max_tasks = config.get("max_tasks")
        backend = config.get("backend", "triton")

        if isinstance(levels, int):
            levels = [levels]

        raw_problems = load_kernelbench_problems(
            levels=levels,
            problems=problems_spec,
            max_tasks=max_tasks,
        )

        tasks: list[BenchmarkTask] = []
        for p in raw_problems:
            level = p["level"]
            pid = p["problem_id"]
            name = p["name"]
            source_code = fix_super_bug(p["source_code"])

            task_id = f"moltbench_L{level}_P{pid:03d}"
            prompt = build_task_prompt(
                level=level,
                problem_id=pid,
                name=name,
                source_code=source_code,
                max_rounds=max_rounds,
                backend=backend,
            )
            task_json = build_task_json(
                level=level,
                problem_id=pid,
                max_rounds=max_rounds,
                backend=backend,
                prompt=prompt,
            )

            tasks.append(BenchmarkTask(
                task_id=task_id,
                problem=prompt,
                ground_truth={"level": level, "problem_id": pid, "name": name},
                metadata={
                    "max_rounds": max_rounds,
                    "backend": backend,
                    "task_json": task_json,
                    "source_code": source_code,
                },
            ))

        logger.info(
            "Loaded %d MoltBench tasks (levels=%s, problems=%s)",
            len(tasks), levels, problems_spec,
        )
        return tasks

    def default_scorer(self) -> str:
        return "moltbench"


BenchmarkRegistry.register("moltbench", MoltBenchBenchmark)
