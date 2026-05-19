"""IMO-AnswerBench benchmark loader."""
from __future__ import annotations

import logging
import os

from alphadiana.benchmark.base import Benchmark, BenchmarkTask, load_dataset_with_retry
from alphadiana.benchmark.registry import BenchmarkRegistry

logger = logging.getLogger(__name__)


class ImoAnswerBenchBenchmark(Benchmark):
    """Loads IMO-AnswerBench competition problems from HuggingFace.

    Config keys:
        dataset: HuggingFace dataset path (required, e.g. "Hwilner/imo-answerbench")
        data_config: Dataset config name for multi-config datasets (optional)
        split: Dataset split (default: "train")
        problem_field: Column name for problem text (default: "Problem")
        answer_field: Column name for answer (default: "Short Answer")
        category_field: Column name for category filtering (default: "Category")
        category: If set, only include rows where row[category_field] == category
                  (e.g. "Algebra", "Combinatorics", "Geometry", "Number Theory")
        dataset_index: If set, only load the raw dataset row at this index
        max_tasks: Maximum number of tasks to load (optional)
    """

    name = "imo_answerbench"

    def load_tasks(self, config: dict) -> list[BenchmarkTask]:
        try:
            from datasets import load_dataset
        except ImportError:
            raise RuntimeError(
                "The 'datasets' library is required for IMO-AnswerBench benchmark. "
                "Install with: pip install datasets"
            )

        dataset_path = config.get("dataset")
        if not dataset_path:
            raise ValueError(
                "IMO-AnswerBench benchmark requires 'dataset' in config "
                "(e.g. 'Hwilner/imo-answerbench')"
            )

        split = config.get("split", "train")
        data_config = config.get("data_config")
        problem_field = config.get("problem_field", "Problem")
        answer_field = config.get("answer_field", "Short Answer")
        category_field = config.get("category_field", "Category")
        category = config.get("category")
        dataset_index = config.get("dataset_index")
        max_tasks = config.get("max_tasks")

        if max_tasks == 0:
            return []

        try:
            dataset = load_dataset_with_retry(dataset_path, data_config, split=split)
        except Exception as exc:
            hf_endpoint = os.environ.get("HF_ENDPOINT", "").strip()
            raise RuntimeError(
                "Failed to load IMO-AnswerBench dataset from Hugging Face. "
                "If direct access is unavailable, set "
                "`HF_ENDPOINT=https://hf-mirror.com` and retry. "
                f"Current HF_ENDPOINT={hf_endpoint or '<unset>'}. "
                f"Original error: {exc}"
            ) from exc

        if len(dataset) == 0:
            return []

        sample = dataset[0]

        if problem_field not in sample:
            available = ", ".join(sorted(sample.keys()))
            raise KeyError(
                f"IMO-AnswerBench dataset missing configured problem_field='{problem_field}'. "
                f"Available fields: {available}"
            )

        if answer_field not in sample:
            available = ", ".join(sorted(sample.keys()))
            raise KeyError(
                f"IMO-AnswerBench dataset missing configured answer_field='{answer_field}'. "
                f"Available fields: {available}"
            )

        if category is not None and category_field not in sample:
            available = ", ".join(sorted(sample.keys()))
            raise ValueError(
                f"IMO-AnswerBench: category_field={category_field!r} not present on dataset rows. "
                f"Available fields: {available}"
            )

        if dataset_index is not None:
            dataset_index = int(dataset_index)
            if dataset_index < 0 or dataset_index >= len(dataset):
                raise IndexError(
                    f"dataset_index={dataset_index} out of range [0, {len(dataset)})"
                )
            iterator = [(dataset_index, dataset[dataset_index])]
        else:
            iterator = enumerate(dataset)

        tasks: list[BenchmarkTask] = []
        for idx, item in iterator:
            if category is not None:
                if category_field not in item:
                    available = ", ".join(sorted(item.keys()))
                    raise ValueError(
                        f"IMO-AnswerBench: category_field={category_field!r} not present on dataset rows. "
                        f"Available fields: {available}"
                    )
                if str(item[category_field]).strip().lower() != str(category).strip().lower():
                    continue

            problem_id = item.get("Problem ID")
            if problem_id is None or str(problem_id).strip() == "":
                task_id = f"imo_{idx}"
                logger.warning("IMO-AnswerBench row %s missing Problem ID; using %s", idx, task_id)
            else:
                task_id = str(problem_id)

            metadata = {
                "source": dataset_path,
                "index": idx,
                "category": item.get(category_field, ""),
            }
            for key in ("Subcategory", "Source", "Problem ID"):
                if key in item:
                    metadata[key] = item[key]

            tasks.append(BenchmarkTask(
                task_id=task_id,
                problem=item[problem_field],
                ground_truth=str(item[answer_field]),
                metadata=metadata,
            ))

            if max_tasks is not None and len(tasks) >= max_tasks:
                break

        return tasks

    def default_scorer(self) -> str:
        return "imo_verify"


BenchmarkRegistry.register("imo_answerbench", ImoAnswerBenchBenchmark)
