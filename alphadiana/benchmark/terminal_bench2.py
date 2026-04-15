"""TerminalBench-2 benchmark loader — loads tasks from local directory."""
from __future__ import annotations

import logging
from pathlib import Path

from alphadiana.benchmark.base import Benchmark, BenchmarkTask
from alphadiana.benchmark.registry import BenchmarkRegistry

logger = logging.getLogger(__name__)


class TerminalBench2Benchmark(Benchmark):
    """Loads terminal-bench-2 tasks from a local directory clone.

    Each task lives in its own subdirectory containing:
      - task.toml  : metadata (docker_image, category, difficulty, timeouts)
      - instruction.md : natural-language task description (used as LLM prompt)

    tasks_dir should point to the repo root or a category subdirectory, e.g.:
      $TERMINAL_BENCH2_DIR or $TERMINAL_BENCH2_DIR/file-operations

    Clone with sparse checkout to save disk space:
      git clone --depth=1 --filter=blob:none --sparse \\
          https://github.com/harbor-framework/terminal-bench-2.git \\
          /path/to/terminal-bench-2
      cd /path/to/terminal-bench-2
      git sparse-checkout set file-operations

    Config keys:
        tasks_dir   : Path to category subdirectory (required)
        categories  : List of category names to include (optional metadata filter)
        max_tasks   : Maximum number of tasks to load (optional)
    """

    name = "terminal_bench2"

    def load_tasks(self, config: dict) -> list[BenchmarkTask]:
        tasks_dir_str = config.get("tasks_dir")
        if not tasks_dir_str:
            raise ValueError(
                "TerminalBench2Benchmark requires 'tasks_dir' in config, "
                "pointing to a local terminal-bench-2 clone directory "
                "(e.g. $TERMINAL_BENCH2_DIR or /path/to/terminal-bench-2)"
            )

        tasks_dir = Path(tasks_dir_str)
        if not tasks_dir.exists():
            raise FileNotFoundError(
                f"tasks_dir does not exist: {tasks_dir}. "
                "Clone terminal-bench-2 and set tasks_dir in config."
            )
        if not tasks_dir.is_dir():
            raise NotADirectoryError(f"tasks_dir is not a directory: {tasks_dir}")

        categories: list[str] | None = config.get("categories")
        max_tasks: int | None = config.get("max_tasks")

        task_dirs = sorted(d for d in tasks_dir.iterdir() if d.is_dir())
        tasks: list[BenchmarkTask] = []

        for task_dir in task_dirs:
            toml_path = task_dir / "task.toml"
            instruction_path = task_dir / "instruction.md"

            if not toml_path.exists():
                logger.warning("Skipping %s: missing task.toml", task_dir)
                continue
            if not instruction_path.exists():
                logger.warning("Skipping %s: missing instruction.md", task_dir)
                continue

            try:
                import tomli
                with open(toml_path, "rb") as f:
                    toml_data = tomli.load(f)
            except Exception as exc:
                logger.warning("Skipping %s: failed to parse task.toml: %s", task_dir, exc)
                continue

            env_section = toml_data.get("environment", {})
            meta_section = toml_data.get("metadata", {})
            verifier_section = toml_data.get("verifier", {})
            agent_section = toml_data.get("agent", {})

            docker_image = env_section.get("docker_image", "")
            if not docker_image:
                logger.warning("Skipping %s: missing environment.docker_image", task_dir)
                continue

            category = meta_section.get("category", "")
            difficulty = meta_section.get("difficulty", "")

            # Apply categories filter (metadata filter, not directory filter)
            if categories is not None and category not in categories:
                continue

            instruction = instruction_path.read_text(encoding="utf-8")

            task_id = f"tb2_{task_dir.name}"
            tasks.append(BenchmarkTask(
                task_id=task_id,
                problem=instruction,
                ground_truth="1",
                metadata={
                    "docker_image": docker_image,
                    "tests_dir": str(task_dir),
                    "category": category,
                    "difficulty": difficulty,
                    "timeout_sec": float(
                        agent_section.get("timeout_sec",
                        verifier_section.get("timeout_sec", 900.0))
                    ),
                    "task_dir": str(task_dir),
                },
            ))

            if max_tasks is not None and len(tasks) >= max_tasks:
                break

        return tasks

    def default_scorer(self) -> str:
        return "terminal_bench2"


BenchmarkRegistry.register("terminal_bench2", TerminalBench2Benchmark)
