"""SWE-bench benchmark loader (SWE-bench/SWE-bench_Verified)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

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
        task_ids:      Explicit SWE-bench instance ids to load, in order (optional)
        taskset_path:  JSON file with task_ids, selected_task_ids, or tasks (optional)
        dataset_indices: Explicit dataset row indices to load, in order (optional)
        include_hints: Whether to append hints_text to the problem (default: false)
    """

    name = "swe_bench"

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if value in (None, ""):
            return []
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        if isinstance(value, (list, tuple)):
            return [str(part).strip() for part in value if str(part).strip()]
        raise ValueError(f"Expected a list or comma-separated string, got {type(value).__name__}")

    @staticmethod
    def _int_list(value: Any) -> list[int]:
        if value in (None, ""):
            return []
        if isinstance(value, str):
            raw_values = [part.strip() for part in value.split(",") if part.strip()]
        elif isinstance(value, (list, tuple)):
            raw_values = list(value)
        else:
            raise ValueError(f"Expected a list or comma-separated string, got {type(value).__name__}")
        try:
            return [int(part) for part in raw_values]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid dataset_indices value: {raw_values!r}") from exc

    @staticmethod
    def _normalize_instance_id(value: str) -> str:
        text = str(value).strip()
        return text[len("swe_") :] if text.startswith("swe_") else text

    def _task_ids_from_taskset(self, taskset_path: str) -> list[str]:
        path = Path(taskset_path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise RuntimeError(f"Failed to read SWE-bench taskset file {path}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"SWE-bench taskset file is not valid JSON: {path}") from exc

        if isinstance(data, dict):
            for key in ("task_ids", "selected_task_ids"):
                values = data.get(key)
                if isinstance(values, list):
                    return self._string_list(values)
            tasks = data.get("tasks")
            if isinstance(tasks, list):
                ids: list[str] = []
                for item in tasks:
                    if isinstance(item, dict):
                        raw = item.get("instance_id") or item.get("task_id")
                    else:
                        raw = item
                    if raw:
                        ids.append(str(raw))
                return self._string_list(ids)
        if isinstance(data, list):
            return self._string_list(data)
        raise ValueError(
            f"SWE-bench taskset file {path} must contain task_ids, selected_task_ids, tasks, or a list"
        )

    def _build_task(
        self,
        *,
        idx: int,
        item: Any,
        dataset_path: str,
        include_hints: bool,
    ) -> BenchmarkTask:
        required = ["instance_id", "repo", "base_commit", "problem_statement"]
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

        return BenchmarkTask(
            task_id=f"swe_{instance_id}",
            problem=problem,
            ground_truth=patch,
            metadata={
                "source": dataset_path,
                "dataset_index": idx,
                "instance_id": instance_id,
                "repo": item.get("repo", ""),
                "base_commit": item.get("base_commit", ""),
                "version": item.get("version", ""),
                "FAIL_TO_PASS": item.get("FAIL_TO_PASS", ""),
                "PASS_TO_PASS": item.get("PASS_TO_PASS", ""),
                "test_patch": item.get("test_patch", ""),
                "environment_setup_commit": item.get("environment_setup_commit", ""),
            },
        )

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
        task_ids = self._string_list(config.get("task_ids"))
        if config.get("taskset_path"):
            taskset_ids = self._task_ids_from_taskset(str(config.get("taskset_path")))
            if task_ids and task_ids != taskset_ids:
                raise ValueError("SWE-bench config cannot set conflicting task_ids and taskset_path")
            task_ids = taskset_ids
        task_ids = [self._normalize_instance_id(value) for value in task_ids]
        dataset_indices = self._int_list(config.get("dataset_indices"))
        if task_ids and dataset_indices:
            raise ValueError("SWE-bench config may set task_ids/taskset_path or dataset_indices, not both")

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
        if task_ids:
            by_instance: dict[str, tuple[int, Any]] = {}
            for idx, item in enumerate(dataset):
                by_instance[str(item.get("instance_id", idx))] = (idx, item)
            missing = [instance_id for instance_id in task_ids if instance_id not in by_instance]
            if missing:
                raise ValueError(f"SWE-bench task_ids not found in {dataset_path}:{split}: {missing}")
            for instance_id in task_ids:
                idx, item = by_instance[instance_id]
                tasks.append(
                    self._build_task(
                        idx=idx,
                        item=item,
                        dataset_path=dataset_path,
                        include_hints=include_hints,
                    )
                )
            return tasks[: int(max_tasks)] if max_tasks is not None else tasks

        if dataset_indices:
            for idx in dataset_indices:
                try:
                    item = dataset[idx]
                except IndexError as exc:
                    raise ValueError(f"SWE-bench dataset index out of range: {idx}") from exc
                tasks.append(
                    self._build_task(
                        idx=idx,
                        item=item,
                        dataset_path=dataset_path,
                        include_hints=include_hints,
                    )
                )
            return tasks[: int(max_tasks)] if max_tasks is not None else tasks

        for idx, item in enumerate(dataset):
            tasks.append(
                self._build_task(
                    idx=idx,
                    item=item,
                    dataset_path=dataset_path,
                    include_hints=include_hints,
                )
            )
            if max_tasks is not None and len(tasks) >= max_tasks:
                break
        return tasks

    def default_scorer(self) -> str:
        return "swe_bench"


BenchmarkRegistry.register("swe_bench", SWEBenchBenchmark)
