"""AIME benchmark loader."""
from __future__ import annotations

import os

from alphadiana.benchmark.base import Benchmark, BenchmarkTask
from alphadiana.benchmark.registry import BenchmarkRegistry


class AIMEBenchmark(Benchmark):
    """Loads AIME competition problems. Numeric answers.

    Config keys:
        dataset: HuggingFace dataset path (required, e.g. "HuggingFaceH4/aime_2024")
        split: Dataset split (default: "train")
        problem_field: Column name for problem text (default: "problem")
        answer_field: Column name for answer (default: "answer")
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
                "(e.g. 'HuggingFaceH4/aime_2024')"
            )

        split = config.get("split", "train")
        data_config = config.get("data_config")
        problem_field = config.get("problem_field", "problem")
        answer_field = config.get("answer_field", "answer")

        data_config = config.get("data_config")

        try:
            dataset = load_dataset(dataset_path, data_config, split=split)
        except Exception as exc:
            hf_endpoint = os.environ.get("HF_ENDPOINT", "").strip()
            raise RuntimeError(
                "Failed to load AIME dataset from Hugging Face. "
                "If direct access is unavailable, source `dev/rock_env.sh` first "
                "or set `HF_ENDPOINT=https://hf-mirror.com` and retry. "
                f"Current HF_ENDPOINT={hf_endpoint or '<unset>'}. "
                f"Original error: {exc}"
            ) from exc

        max_tasks = config.get("max_tasks")

        tasks: list[BenchmarkTask] = []
        for idx, item in enumerate(dataset):
            task_id_val = item.get("id", idx)
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
            if max_tasks is not None and len(tasks) >= max_tasks:
                break
        return tasks

    def default_scorer(self) -> str:
        return "numeric"


BenchmarkRegistry.register("aime", AIMEBenchmark)
