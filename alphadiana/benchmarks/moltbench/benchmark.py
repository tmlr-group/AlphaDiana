"""MoltBench benchmark: multi-round GPU kernel optimization evaluation.

Loads KernelBench problems and evaluates agents on iterative optimization
inside GPU-enabled Docker containers.
"""

from __future__ import annotations

import logging

from alphadiana.benchmarks.base import Benchmark, BenchmarkTask
from alphadiana.benchmarks.registry import BenchmarkRegistry
from alphadiana.benchmarks.moltbench.utils import (
    load_kernelbench_problems,
    build_task_prompt,
    build_task_json,
    fix_super_bug,
)

logger = logging.getLogger(__name__)


class MoltBenchBenchmark(Benchmark):
    """MoltBench iterative optimization benchmark.

    Supports multiple task types via config["task"]:
      - "kernelbench" (default): KernelBench L1-L4 GPU kernel optimization
      - "mini_sglang": mini-SGLang LLM serving throughput optimization

    Config keys (KernelBench):
        levels: list[int] — KernelBench levels to include (default: [1])
        problems: str | None — problem range like "1-20" or "1,3,5" (default: all)
        max_rounds: int — max optimization rounds per problem (default: 10)
        max_tasks: int | None — limit total number of tasks (optional)
        backend: str — "triton" or "cuda" (default: "triton")
        source: str — dataset source (default: "hf")

    Config keys (mini-SGLang):
        max_rounds: int — max optimization rounds (default: 3)
        baseline_throughput: float — known baseline tok/s (default: 4813.0)
        difficulty: str — "easy" (PR-guided) or "hard" (no hints) (default: "easy")
    """

    name = "moltbench"

    def load_tasks(self, config: dict) -> list[BenchmarkTask]:
        task_type = config.get("task", "kernelbench")
        if task_type == "mini_sglang":
            return self._load_mini_sglang_tasks(config)
        return self._load_kernelbench_tasks(config)

    # ── mini-SGLang ──────────────────────────────────────────────────────────

    def _load_mini_sglang_tasks(self, config: dict) -> list[BenchmarkTask]:
        """Load the mini-SGLang throughput optimization task.

        ``difficulty`` selects the system prompt the container builds via
        ``call_gateway.py``: ``easy`` ships optimization PR references and
        implementation guidance; ``hard`` only asks to "optimize throughput"
        so the agent must find bottlenecks itself. Both share the same image,
        eval script, and scoring.
        """
        # "attempts" is the canonical term (Design-2 §2: one run_eval call = one
        # attempt, NOT a "round"). max_rounds is still accepted for back-compat.
        max_attempts = int(config.get("max_attempts", config.get("max_rounds", 3)))
        baseline = float(config.get("baseline_throughput", 4813.0))
        difficulty = config.get("difficulty", "easy")
        # Friendly name of the served model, for the prompt only (path is pluggable).
        model_name = str(config.get("model_name", "the served model"))

        task_id = f"moltbench_minisglang_{difficulty}_v1"
        prompt = (
            f"Optimize mini-SGLang inference throughput for {model_name} "
            f"(difficulty: {difficulty}, {max_attempts} attempts). "
            "The agent modifies /workspace/repo/python/minisgl/ and calls "
            "/eval/run_eval to benchmark after each change."
        )

        task_json = {
            "benchmark_id": "mini_sglang",
            "level": 1,
            "problem_id": 1,
            "max_attempts": max_attempts,
            "max_rounds": max_attempts,  # legacy alias for older container entrypoints
            "difficulty": difficulty,
            "baseline_throughput": baseline,
            "workdir": "/workspace/repo",
            "primary_edit_file": "python/minisgl/scheduler/scheduler.py",
            "bench_log_path": "/tmp/eval_result.json",
            "snapshot_dir": "python/minisgl",
            "result_parser": "mini_sglang",
            "result_json_path": "/tmp/eval_result.json",
            "verify_cmd": "/eval/run_eval",
            "prompt": prompt,
            # ── Scope declaration (MoltBench Design-4 §2/§3, §9) ──────────────
            # This is a single-stage SERVING-OPTIMIZATION task: bootstrap baseline
            # -> agent improvement -> official eval. There is no drift stage and no
            # memory-gated stage, so Stability and Memory are not measurable and are
            # declared N/A (NOT silently omitted — downstream leaderboards/analysis
            # must know this task cannot back memory-to-action or drift claims).
            "moltbench_task_level": "optimization_only",
            "scoring": {
                "correctness_metric": "all benchmark requests succeed (num_error == 0)",
                "gain_metric": "OG = candidate_throughput / in-situ baseline_throughput "
                               "(warmup-excluded steady-state median, 95% CI)",
                "stability_metric": "N/A",   # single-stage: no retention/regression measurement
                "memory_metric": "N/A",      # no memory gate / paired ablation
                "drift_metric": "N/A",       # no drift stage
                "cost_metric": "attempts_used, wall_time_sec, plus harness-level "
                               "model_calls/tokens aggregated into K",
            },
        }

        task = BenchmarkTask(
            task_id=task_id,
            problem=prompt,
            ground_truth={"baseline_throughput": baseline},
            metadata={
                "max_attempts": max_attempts,
                "difficulty": difficulty,
                "moltbench_task_level": "optimization_only",
                "stability_metric": "N/A",
                "memory_metric": "N/A",
                "drift_metric": "N/A",
                "task_json": task_json,
            },
        )
        logger.info(
            "Loaded 1 mini-SGLang task (difficulty=%s, max_attempts=%d, baseline=%.1f)",
            difficulty, max_attempts, baseline,
        )
        return [task]

    # ── KernelBench ──────────────────────────────────────────────────────────

    def _load_kernelbench_tasks(self, config: dict) -> list[BenchmarkTask]:
        levels = config.get("levels", [1])
        problems_spec = config.get("problems")
        max_rounds = int(config.get("max_rounds", 10))
        max_tasks = config.get("max_tasks")
        backend = config.get("backend", "triton")
        source = config.get("source", "hf")

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
                source=source,
                prompt=prompt,
            )

            tasks.append(BenchmarkTask(
                task_id=task_id,
                problem=prompt,
                ground_truth={"level": level, "problem_id": pid, "name": name},
                metadata={
                    "max_rounds": max_rounds,
                    "backend": backend,
                    "source": source,
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
