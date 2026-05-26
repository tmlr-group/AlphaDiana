"""TerminalBench-2 benchmark loader — loads tasks from local directory."""
from __future__ import annotations

import logging
import os
from pathlib import Path

try:
    import tomllib as tomli
except ModuleNotFoundError:
    import tomli

from alphadiana.benchmark.base import Benchmark, BenchmarkTask
from alphadiana.benchmark.registry import BenchmarkRegistry

logger = logging.getLogger(__name__)


class TerminalBench2Benchmark(Benchmark):
    """Loads terminal-bench tasks from a local directory tree.

    Each task lives in its own subdirectory containing:
      - task.toml
      - instruction.md

    tasks_dir should point to a directory whose immediate children are task
    directories. Two common patterns are:

      - full task root, e.g. `/tmp/terminal-bench/tasks`
      - staged smoke root containing one copied task directory

    Config keys:
        tasks_dir   : Path to local task root (required)
        categories  : List of metadata category names to include (optional)
        task_ids    : List of task directory names or `tb2_` task ids to include (optional)
        max_tasks   : Maximum number of tasks to load (optional)
    """

    name = "terminal_bench2"

    def load_tasks(self, config: dict) -> list[BenchmarkTask]:
        tasks_dir_str = str(config.get("tasks_dir", "") or "").strip()
        if not tasks_dir_str:
            tasks_dir_str = os.environ.get("TERMINAL_BENCH2_DIR", "").strip()
        if not tasks_dir_str:
            raise ValueError(
                "TerminalBench2Benchmark requires 'tasks_dir' in config, "
                "or TERMINAL_BENCH2_DIR in the environment, pointing to a local "
                "terminal-bench-2 clone."
            )
        if "${" in tasks_dir_str:
            raise ValueError(
                "TerminalBench2Benchmark got an unresolved tasks_dir placeholder. "
                "Export TERMINAL_BENCH2_DIR or set benchmark.config.tasks_dir explicitly."
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
        requested_task_names = self._normalize_task_ids(config.get("task_ids"))

        task_dirs = sorted(d for d in tasks_dir.iterdir() if d.is_dir())
        tasks: list[BenchmarkTask] = []

        for task_dir in task_dirs:
            if requested_task_names is not None and task_dir.name not in requested_task_names:
                continue

            toml_path = task_dir / "task.toml"
            instruction_path = task_dir / "instruction.md"

            if not toml_path.exists():
                logger.warning("Skipping %s: missing task.toml", task_dir)
                continue
            if not instruction_path.exists():
                logger.warning("Skipping %s: missing instruction.md", task_dir)
                continue

            try:
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
                    "build_timeout_sec": float(env_section.get("build_timeout_sec", 600.0)),
                    "timeout_sec": float(
                        agent_section.get("timeout_sec",
                        verifier_section.get("timeout_sec", 900.0))
                    ),
                    "task_dir": str(task_dir),
                },
            ))

            if max_tasks is not None and len(tasks) >= max_tasks:
                break

        if requested_task_names is not None and not tasks:
            requested = ", ".join(sorted(requested_task_names))
            raise ValueError(
                f"TerminalBench2Benchmark found no loadable tasks matching task_ids: {requested}"
            )

        return tasks

    def default_scorer(self) -> str:
        return "terminal_bench2"

    @staticmethod
    def _normalize_task_ids(raw_task_ids: object) -> set[str] | None:
        if raw_task_ids in (None, "", []):
            return None
        if isinstance(raw_task_ids, str):
            values = [raw_task_ids]
        else:
            values = list(raw_task_ids)  # type: ignore[arg-type]
        normalized = {
            str(value).strip().removeprefix("tb2_")
            for value in values
            if str(value).strip()
        }
        return normalized or None


BenchmarkRegistry.register("terminal_bench2", TerminalBench2Benchmark)
