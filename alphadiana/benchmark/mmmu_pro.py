"""MMMU-Pro benchmark loader (MMMU/MMMU_Pro)."""
from __future__ import annotations

import os

from alphadiana.benchmark.base import Benchmark, BenchmarkTask, load_dataset_with_retry
from alphadiana.benchmark.registry import BenchmarkRegistry

# Letters used for option labeling (MMMU-Pro has up to 10 options)
_OPTION_LABELS = list("ABCDEFGHIJ")


def _format_options(options: list[str]) -> str:
    """Return a formatted string of labeled options."""
    lines = []
    for i, opt in enumerate(options):
        label = _OPTION_LABELS[i] if i < len(_OPTION_LABELS) else str(i + 1)
        lines.append(f"({label}) {opt}")
    return "\n".join(lines)


class MMMUProBenchmark(Benchmark):
    """Loads MMMU-Pro multimodal multiple-choice tasks.

    Each task is a multiple-choice question (up to 10 options). Images are
    stored in the ``attachments`` dict of the returned BenchmarkTask using
    keys ``image_1``, ``image_2``, … (PNG bytes).

    Config keys:
        dataset:     HuggingFace dataset path (default: "MMMU/MMMU_Pro")
        data_config: Dataset subset name — "standard" or "vision"
                     (default: "standard")
        split:       Dataset split (default: "test")
        max_tasks:   Limit the number of tasks loaded (optional)
    """

    name = "mmmu_pro"

    def load_tasks(self, config: dict) -> list[BenchmarkTask]:
        try:
            from datasets import load_dataset  # noqa: F401
        except ImportError:
            raise RuntimeError(
                "The 'datasets' library is required for MMMU-Pro benchmark. "
                "Install with: pip install datasets"
            )

        dataset_path = config.get("dataset", "MMMU/MMMU_Pro")
        data_config = config.get("data_config", "standard (4 options)")
        split = config.get("split", "test")
        max_tasks = config.get("max_tasks")
        include_attachments = str(data_config).strip().lower() == "vision"

        try:
            dataset = load_dataset_with_retry(dataset_path, data_config, split=split)
        except Exception as exc:
            hf_endpoint = os.environ.get("HF_ENDPOINT", "").strip()
            raise RuntimeError(
                "Failed to load MMMU-Pro dataset from Hugging Face. "
                "If direct access is unavailable, set "
                "`HF_ENDPOINT=https://hf-mirror.com` and retry. "
                f"Current HF_ENDPOINT={hf_endpoint or '<unset>'}. "
                f"Original error: {exc}"
            ) from exc

        tasks: list[BenchmarkTask] = []
        for idx, item in enumerate(dataset):
            task_id = item.get("id", str(idx))
            question_text = item.get("question", "")

            # options may be a list, a JSON string, or a Python-literal string
            raw_options = item.get("options", [])
            if isinstance(raw_options, str):
                import ast
                import json
                try:
                    raw_options = json.loads(raw_options)
                except Exception:
                    try:
                        raw_options = ast.literal_eval(raw_options)
                    except Exception:
                        raw_options = [raw_options]

            problem = f"{question_text}\n\n{_format_options(raw_options)}"
            answer = str(item.get("answer", "")).strip().upper()

            attachments: dict[str, bytes] = {}
            if include_attachments:
                # Only the vision split should carry image payloads. The
                # standard 4/10-option configs are documented and used as
                # text-only variants, so exposing attachments there would force
                # unnecessary multimodal model calls.
                for img_key in [f"image_{i}" for i in range(1, 8)]:
                    img = item.get(img_key)
                    if img is None:
                        continue
                    try:
                        import io
                        buf = io.BytesIO()
                        img.save(buf, format="PNG")
                        attachments[img_key] = buf.getvalue()
                        attachments[f"{img_key}_mime"] = b"image/png"
                    except Exception:
                        pass

            tasks.append(BenchmarkTask(
                task_id=f"mmmu_pro_{task_id}",
                problem=problem,
                ground_truth=answer,
                metadata={
                    "source": dataset_path,
                    "data_config": data_config,
                    "subject": item.get("subject", ""),
                    "topic": item.get("topic", ""),
                    "question_type": item.get("question_type", "multiple-choice"),
                    "num_options": len(raw_options),
                },
                attachments=attachments,
            ))
            if max_tasks is not None and len(tasks) >= max_tasks:
                break
        return tasks

    def default_scorer(self) -> str:
        return "exact_match"


BenchmarkRegistry.register("mmmu_pro", MMMUProBenchmark)
