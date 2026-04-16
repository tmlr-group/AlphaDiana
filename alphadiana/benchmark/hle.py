"""HLE (Humanity's Last Exam) benchmark loader."""
from __future__ import annotations

import base64
import io
import os

from alphadiana.benchmark.base import Benchmark, BenchmarkTask, load_dataset_with_retry
from alphadiana.benchmark.registry import BenchmarkRegistry


class HLEBenchmark(Benchmark):
    """Loads HLE (Humanity's Last Exam) questions from HuggingFace.

    This benchmark is gated on HuggingFace. In a fresh environment, set
    HF_TOKEN before running:

        export HF_TOKEN=your_token

    Request dataset access at: https://huggingface.co/datasets/cais/hle

    If the dataset has already been cached locally, loading can still succeed
    without HF_TOKEN.

    Config keys:
        dataset: HuggingFace dataset path (required, e.g. "cais/hle")
        data_config: Dataset config name for multi-config datasets (optional)
        split: Dataset split (default: "test")
        problem_field: Column name for problem text (default: "question")
        answer_field: Column name for answer (default: "answer")
        category_field: Column name for category filtering (default: "subject")
        category: If set, only include rows where row[category_field] == category
        answer_types: List of answer_type values to include (default: ["multipleChoice"])
        dataset_index: If set, only load the raw dataset row at this index
        max_tasks: Maximum number of tasks to load (optional)
    """

    name = "hle"

    def load_tasks(self, config: dict) -> list[BenchmarkTask]:
        try:
            from datasets import load_dataset
        except ImportError:
            raise RuntimeError(
                "The 'datasets' library is required for HLE benchmark. "
                "Install with: pip install datasets"
            )

        dataset_path = config.get("dataset")
        if not dataset_path:
            raise ValueError(
                "HLE benchmark requires 'dataset' in config "
                "(e.g. 'cais/hle')"
            )

        split = config.get("split", "test")
        data_config = config.get("data_config")
        problem_field = config.get("problem_field", "question")
        answer_field = config.get("answer_field", "answer")
        category_field = config.get("category_field", "subject")
        category = config.get("category")
        answer_types = config.get("answer_types", ["multipleChoice"])
        dataset_index = config.get("dataset_index")
        max_tasks = config.get("max_tasks")

        try:
            dataset = load_dataset_with_retry(dataset_path, data_config, split=split)
        except Exception as exc:
            hf_endpoint = os.environ.get("HF_ENDPOINT", "").strip()
            hf_token = os.environ.get("HF_TOKEN", "").strip()
            raise RuntimeError(
                "Failed to load HLE dataset from Hugging Face. "
                "If this machine has not cached the gated dataset yet, export HF_TOKEN first. "
                "If direct access is unavailable, set "
                "`HF_ENDPOINT=https://hf-mirror.com` and retry. "
                f"HF_TOKEN={'set' if hf_token else 'unset'}. "
                f"Current HF_ENDPOINT={hf_endpoint or '<unset>'}. "
                f"Original error: {exc}"
            ) from exc

        if len(dataset) == 0:
            return []

        sample = dataset[0]

        if problem_field not in sample:
            available = ", ".join(sorted(sample.keys()))
            raise KeyError(
                f"HLE dataset missing configured problem_field='{problem_field}'. "
                f"Available fields: {available}"
            )

        if answer_field not in sample:
            available = ", ".join(sorted(sample.keys()))
            raise KeyError(
                f"HLE dataset missing configured answer_field='{answer_field}'. "
                f"Available fields: {available}"
            )

        if dataset_index is not None:
            dataset_index = int(dataset_index)
            iterator = [(dataset_index, dataset[dataset_index])]
        else:
            iterator = enumerate(dataset)

        tasks: list[BenchmarkTask] = []
        for idx, item in iterator:
            # Filter by answer_type (no image filter — scoreability depends on answer format only)
            if item.get("answer_type") not in answer_types:
                continue

            # Filter by category if specified
            if category is not None and category_field in item:
                if item[category_field] != category:
                    continue

            attachments: dict[str, bytes] = {}
            img = item.get("image")
            if img is not None:
                if isinstance(img, str) and img.startswith("data:image") and "," in img:
                    header, b64data = img.split(",", 1)
                    mime = header.replace("data:", "").replace(";base64", "")
                    attachments["image_1"] = base64.b64decode(b64data)
                    attachments["image_1_mime"] = mime.encode()
                else:
                    try:
                        from PIL import Image as PILImage
                        if isinstance(img, PILImage.Image):
                            buf = io.BytesIO()
                            img.save(buf, format="PNG")
                            attachments["image_1"] = buf.getvalue()
                            attachments["image_1_mime"] = b"image/png"
                    except ImportError:
                        pass

            tasks.append(BenchmarkTask(
                task_id=f"hle_{idx}",
                problem=item[problem_field],
                ground_truth=str(item[answer_field]),
                metadata={
                    "source": dataset_path,
                    "index": idx,
                    "answer_type": item.get("answer_type", ""),
                    "category": item.get(category_field, ""),
                },
                attachments=attachments,
            ))

            if max_tasks is not None and len(tasks) >= max_tasks:
                break

        return tasks

    def default_scorer(self) -> str:
        return "exact_match"


BenchmarkRegistry.register("hle", HLEBenchmark)
