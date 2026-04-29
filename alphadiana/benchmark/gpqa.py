"""GPQA-Diamond benchmark loader (fingertap/GPQA-Diamond)."""
from __future__ import annotations

import ast
import os
import random

from alphadiana.benchmark.base import Benchmark, BenchmarkTask, load_dataset_with_retry
from alphadiana.benchmark.registry import BenchmarkRegistry

_OPTION_LABELS = ["A", "B", "C", "D"]


def _build_mcq(question: str, correct: str, incorrects: list[str], seed: int) -> tuple[str, str]:
    """Shuffle the four answer choices and return (formatted_problem, correct_label).

    A fixed per-task seed ensures reproducibility across evaluation runs.
    """
    options = [correct] + incorrects[:3]
    rng = random.Random(seed)
    rng.shuffle(options)
    correct_label = _OPTION_LABELS[options.index(correct)]

    option_lines = "\n".join(
        f"({label}) {text}" for label, text in zip(_OPTION_LABELS, options)
    )
    problem = f"{question}\n\n{option_lines}"
    return problem, correct_label


class GPQADiamondBenchmark(Benchmark):
    """Loads GPQA-Diamond expert-level science questions.

    Each question is presented as a 4-choice multiple-choice problem with
    options shuffled deterministically per task. The ground truth is the
    correct option label (A / B / C / D).

    Config keys:
        dataset:    HuggingFace dataset path (default: "fingertap/GPQA-Diamond")
        split:      Dataset split (default: "test")
        dataset_index: If set, only load the raw dataset row at this index
        dataset_indices: If set, only load the listed raw dataset rows
        max_tasks:  Limit the number of tasks loaded (optional)
        seed:       Base random seed for option shuffling (default: 42)
    """

    name = "gpqa_diamond"

    def load_tasks(self, config: dict) -> list[BenchmarkTask]:
        try:
            from datasets import load_dataset  # noqa: F401
        except ImportError:
            raise RuntimeError(
                "The 'datasets' library is required for GPQA-Diamond benchmark. "
                "Install with: pip install datasets"
            )

        dataset_path = config.get("dataset", "fingertap/GPQA-Diamond")
        split = config.get("split", "test")
        max_tasks = config.get("max_tasks")
        dataset_index = config.get("dataset_index")
        dataset_indices = config.get("dataset_indices")
        base_seed = int(config.get("seed", 42))
        if max_tasks == 0:
            return []
        if dataset_index is not None and dataset_indices is not None:
            raise ValueError(
                "GPQA benchmark config may set only one of 'dataset_index' or "
                "'dataset_indices'."
            )

        try:
            dataset = load_dataset_with_retry(dataset_path, None, split=split)
        except Exception as exc:
            hf_endpoint = os.environ.get("HF_ENDPOINT", "").strip()
            raise RuntimeError(
                "Failed to load GPQA-Diamond dataset from Hugging Face. "
                "If direct access is unavailable, set "
                "`HF_ENDPOINT=https://hf-mirror.com` and retry. "
                f"Current HF_ENDPOINT={hf_endpoint or '<unset>'}. "
                f"Original error: {exc}"
            ) from exc

        # Detect dataset format by column names
        columns = dataset.column_names if hasattr(dataset, "column_names") else (list(dataset[0].keys()) if len(dataset) > 0 else [])
        is_fingertap_format = "question" in columns and "answer" in columns and "Question" not in columns

        if isinstance(dataset_indices, str):
            parsed_indices = ast.literal_eval(dataset_indices)
            if isinstance(parsed_indices, int):
                dataset_indices = [parsed_indices]
            else:
                dataset_indices = parsed_indices

        if dataset_indices is not None:
            iterator = []
            for raw_idx in dataset_indices:
                idx = int(raw_idx)
                if idx < 0 or idx >= len(dataset):
                    raise ValueError(f"dataset index {idx} out of range [0, {len(dataset)})")
                iterator.append((idx, dataset[idx]))
        elif dataset_index is not None:
            idx = int(dataset_index)
            if idx < 0 or idx >= len(dataset):
                raise ValueError(f"dataset_index={idx} out of range [0, {len(dataset)})")
            iterator = [(idx, dataset[idx])]
        else:
            iterator = enumerate(dataset)

        tasks: list[BenchmarkTask] = []
        for idx, item in iterator:
            if is_fingertap_format:
                # fingertap/GPQA-Diamond: question already contains formatted options
                problem = item.get("question", "")
                correct_label = item.get("answer", "A")
                if not problem or not str(correct_label).strip():
                    raise ValueError(f"GPQA row {idx}: empty or malformed answers")
                correct_answer_text = ""
            else:
                question = item.get("Question", "")
                correct_answer_text = item.get("Correct Answer", "")
                incorrects = [
                    item.get("Incorrect Answer 1", ""),
                    item.get("Incorrect Answer 2", ""),
                    item.get("Incorrect Answer 3", ""),
                ]
                if not str(correct_answer_text).strip() or not all(str(answer).strip() for answer in incorrects):
                    raise ValueError(f"GPQA row {idx}: empty or malformed answers")
                problem, correct_label = _build_mcq(
                    question, correct_answer_text, incorrects, seed=base_seed + idx
                )

            tasks.append(BenchmarkTask(
                task_id=f"gpqa_{idx}",
                problem=problem,
                ground_truth=correct_label,
                metadata={
                    "source": dataset_path,
                    "index": idx,
                    "subdomain": item.get("Subdomain", item.get("subdomain", "")),
                    "domain": item.get("High-level domain", item.get("domain", "")),
                    "correct_answer_text": correct_answer_text if not is_fingertap_format else "",
                    "explanation": item.get("Explanation", ""),
                },
            ))
            if max_tasks is not None and len(tasks) >= max_tasks:
                break
        return tasks

    def default_scorer(self) -> str:
        return "exact_match"


BenchmarkRegistry.register("gpqa_diamond", GPQADiamondBenchmark)
