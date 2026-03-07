"""AIME benchmark loader."""
from __future__ import annotations

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
        problem_field = config.get("problem_field", "problem")
        answer_field = config.get("answer_field", "answer")

        dataset = load_dataset(dataset_path, split=split)

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
        return tasks

    def default_scorer(self) -> str:
        return "numeric"


BenchmarkRegistry.register("aime", AIMEBenchmark)
