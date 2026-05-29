"""AIME benchmark loader."""
from __future__ import annotations

import logging
import os

from alphadiana.benchmark.base import Benchmark, BenchmarkTask, load_dataset_with_retry
from alphadiana.benchmark.registry import BenchmarkRegistry

logger = logging.getLogger(__name__)


class AIMEBenchmark(Benchmark):
    """Loads AIME competition problems. Numeric answers.

    Config keys:
        dataset: HuggingFace dataset path (required, e.g. "MathArena/aime_2026")
        data_config: Dataset config name for multi-config datasets (e.g. "AIME2025-I")
        split: Dataset split (default: "train")
        problem_field: Column name for problem text (default: "problem")
        answer_field: Column name for answer (default: "answer")
        dataset_index: If set, only load the raw dataset row at this index
        dataset_indices: If set, only load the listed raw dataset rows
        max_tasks: Maximum number of tasks to load. Ignored when dataset_index
            or dataset_indices is set, and not appended to an already-sliced split.
    """

    name = "aime"

    def load_tasks(self, config: dict) -> list[BenchmarkTask]:
        try:
            from datasets import load_dataset
        except ImportError:
            raise RuntimeError(
                "The 'datasets' library is required for AIME benchmark. "
                "Install with: pip install datasets"
            )

        dataset_path = config.get("dataset")
        if not dataset_path:
            raise ValueError(
                "AIME benchmark requires 'dataset' in config "
                "(e.g. 'MathArena/aime_2026')"
            )

        split = config.get("split", "train")
        data_config = config.get("data_config")
        problem_field = config.get("problem_field", "problem")
        answer_field = config.get("answer_field", "answer")
        max_tasks = config.get("max_tasks")
        dataset_index = config.get("dataset_index")
        dataset_indices = config.get("dataset_indices")

        if max_tasks == 0:
            return []
        if dataset_index is not None and dataset_indices is not None:
            raise ValueError(
                "AIME benchmark config may set only one of 'dataset_index' or "
                "'dataset_indices'."
            )

        effective_split = split
        if (
            max_tasks is not None
            and dataset_index is None
            and dataset_indices is None
            and "[" not in str(split)
        ):
            effective_split = f"{split}[:{int(max_tasks)}]"

        try:
            dataset = load_dataset_with_retry(dataset_path, data_config, split=effective_split)
        except Exception as exc:
            hf_endpoint = os.environ.get("HF_ENDPOINT", "").strip()
            raise RuntimeError(
                "Failed to load AIME dataset from Hugging Face. "
                "If direct access is unavailable, source `scripts/rock_env.sh` first "
                "or set `HF_ENDPOINT=https://hf-mirror.com` and retry. "
                f"Current HF_ENDPOINT={hf_endpoint or '<unset>'}. "
                f"Original error: {exc}"
            ) from exc

        if len(dataset) == 0:
            return []

        sample = dataset[0]
        if problem_field not in sample:
            if "question" in sample:
                problem_field = "question"
            else:
                available = ", ".join(sorted(sample.keys()))
                raise KeyError(
                    f"AIME dataset missing configured problem_field='{problem_field}'. "
                    f"Available fields: {available}"
                )

        if answer_field not in sample:
            available = ", ".join(sorted(sample.keys()))
            raise KeyError(
                f"AIME dataset missing configured answer_field='{answer_field}'. "
                f"Available fields: {available}"
            )

        if dataset_indices is not None:
            iterator = [
                (int(idx), dataset[int(idx)])
                for idx in dataset_indices
            ]
        elif dataset_index is not None:
            dataset_index = int(dataset_index)
            if dataset_index < 0 or dataset_index >= len(dataset):
                raise IndexError(
                    f"dataset_index={dataset_index} out of range [0, {len(dataset)})"
                )
            iterator = [(dataset_index, dataset[dataset_index])]
        else:
            iterator = enumerate(dataset)

        tasks: list[BenchmarkTask] = []
        warned_missing_problem_idx = False
        for idx, item in iterator:
            if "problem_idx" in item and item["problem_idx"] is not None:
                task_id_val = item["problem_idx"]
            else:
                task_id_val = item.get("id", idx)
                if not warned_missing_problem_idx:
                    logger.warning("AIME dataset rows missing problem_idx; falling back to row/index IDs")
                    warned_missing_problem_idx = True
            tasks.append(BenchmarkTask(
                task_id=f"aime_{task_id_val}",
                problem=item[problem_field],
                ground_truth=str(item[answer_field]),
                metadata={
                    "source": dataset_path,
                    "index": idx,
                    "year": item.get("year", ""),
                    "url": item.get("url", ""),
                },
            ))
            if (
                max_tasks is not None
                and dataset_index is None
                and dataset_indices is None
                and len(tasks) >= max_tasks
            ):
                break
        return tasks

    def default_scorer(self) -> str:
        return "numeric"


BenchmarkRegistry.register("aime", AIMEBenchmark)
