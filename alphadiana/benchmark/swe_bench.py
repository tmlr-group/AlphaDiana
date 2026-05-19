"""SWE-bench benchmark loader (SWE-bench/SWE-bench_Verified)."""
from __future__ import annotations

import os

from alphadiana.benchmark.base import Benchmark, BenchmarkTask, load_dataset_with_retry
from alphadiana.benchmark.registry import BenchmarkRegistry


class SWEBenchBenchmark(Benchmark):
    """Loads SWE-bench_Verified software engineering tasks.

    Each task presents a GitHub issue description and expects the agent to
    produce a code patch that resolves the issue.

    Config keys:
        dataset:       HuggingFace dataset path
                       (default: "SWE-bench/SWE-bench_Verified")
        split:         Dataset split (default: "test")
        max_tasks:     Limit the number of tasks loaded (optional)
        include_hints: Whether to append hints_text to the problem (default: false)
    """

    name = "swe_bench"

    def load_tasks(self, config: dict) -> list[BenchmarkTask]:
        try:
            from datasets import load_dataset  # noqa: F401
        except ImportError:
            raise RuntimeError(
                "The 'datasets' library is required for SWE-bench benchmark. "
                "Install with: pip install datasets"
            )

        dataset_path = config.get("dataset", "SWE-bench/SWE-bench_Verified")
        split = config.get("split", "test")
        include_hints = config.get("include_hints", False)
        max_tasks = config.get("max_tasks")
        if max_tasks == 0:
            return []

        try:
            dataset = load_dataset_with_retry(dataset_path, None, split=split)
        except Exception as exc:
            hf_endpoint = os.environ.get("HF_ENDPOINT", "").strip()
            raise RuntimeError(
                "Failed to load SWE-bench dataset from Hugging Face. "
                "If direct access is unavailable, set "
                "`HF_ENDPOINT=https://hf-mirror.com` and retry. "
                f"Current HF_ENDPOINT={hf_endpoint or '<unset>'}. "
                f"Original error: {exc}"
            ) from exc

        tasks: list[BenchmarkTask] = []
        required = ["instance_id", "repo", "base_commit", "problem_statement"]
        for idx, item in enumerate(dataset):
            missing_req = [key for key in required if key not in item or item[key] is None]
            if missing_req:
                row_id = item.get("instance_id", "?")
                raise ValueError(f"SWE-bench row {row_id!r} missing: {missing_req}")

            instance_id = item.get("instance_id", str(idx))
            problem = item.get("problem_statement", "")
            if include_hints:
                hints = item.get("hints_text", "").strip()
                if hints:
                    problem = f"{problem}\n\nHints:\n{hints}"

            patch = item.get("patch", "")

            tasks.append(BenchmarkTask(
                task_id=f"swe_{instance_id}",
                problem=problem,
                ground_truth=patch,
                metadata={
                    "source": dataset_path,
                    "instance_id": instance_id,
                    "repo": item.get("repo", ""),
                    "base_commit": item.get("base_commit", ""),
                    "version": item.get("version", ""),
                    "FAIL_TO_PASS": item.get("FAIL_TO_PASS", ""),
                    "PASS_TO_PASS": item.get("PASS_TO_PASS", ""),
                    "test_patch": item.get("test_patch", ""),
                    "environment_setup_commit": item.get("environment_setup_commit", ""),
                },
            ))
            if max_tasks is not None and len(tasks) >= max_tasks:
                break
        return tasks

    def default_scorer(self) -> str:
        return "swe_bench"


BenchmarkRegistry.register("swe_bench", SWEBenchBenchmark)
