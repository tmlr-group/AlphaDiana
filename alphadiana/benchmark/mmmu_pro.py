"""MMMU-Pro benchmark loader (MMMU/MMMU_Pro)."""
from __future__ import annotations

import ast
import re

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


def _collect_dataset_images(item: dict) -> list[object]:
    """Return dataset image payloads in a normalized order.

    The current Hugging Face MMMU-Pro `vision` config exposes a single `image`
    field, while older/local variants may still provide numbered `image_1` ..
    `image_7` keys. Prefer explicit numbered fields when present, otherwise
    fall back to `images` / `image`.
    """
    numbered_images = [item.get(f"image_{i}") for i in range(1, 8)]
    numbered_images = [img for img in numbered_images if img is not None]
    if numbered_images:
        return numbered_images

    images_field = item.get("images")
    if isinstance(images_field, (list, tuple)):
        images = [img for img in images_field if img is not None]
        if images:
            return images
    elif images_field is not None:
        return [images_field]

    image_field = item.get("image")
    if isinstance(image_field, (list, tuple)):
        return [img for img in image_field if img is not None]
    if image_field is not None:
        return [image_field]

    return []


def _raise_mmmu_load_error(exc: Exception, *, data_config: str) -> None:
    """Raise a user-actionable MMMU-Pro load error."""
    message = str(exc)
    lowered = message.lower()
    if isinstance(exc, PermissionError) or re.search(r"\b(401|403)\b", message) or "token" in lowered:
        raise RuntimeError(
            f"Failed to load MMMU-Pro dataset: authentication required or denied. Original error: {exc}"
        ) from exc
    if isinstance(exc, (ConnectionError, TimeoutError)):
        raise RuntimeError(
            f"Failed to load MMMU-Pro dataset due to a network error. Original error: {exc}"
        ) from exc
    if isinstance(exc, ValueError) or "builderconfig" in lowered or "config" in lowered or "split" in lowered:
        raise RuntimeError(
            f"Failed to load MMMU-Pro dataset: config name {data_config!r} is invalid or unavailable. "
            f"Original error: {exc}"
        ) from exc
    if "not found" in lowered or "datasetnotfound" in type(exc).__name__.lower():
        raise RuntimeError(
            f"Failed to load MMMU-Pro dataset: dataset not found. Original error: {exc}"
        ) from exc
    raise RuntimeError(f"Failed to load MMMU-Pro dataset. Original error: {exc}") from exc


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
        dataset_index: If set, only load the raw dataset row at this index
        dataset_indices: If set, only load the listed raw dataset rows
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
        dataset_index = config.get("dataset_index")
        dataset_indices = config.get("dataset_indices")
        max_tasks = config.get("max_tasks")
        if max_tasks == 0:
            return []

        try:
            dataset = load_dataset_with_retry(dataset_path, data_config, split=split)
        except Exception as exc:
            _raise_mmmu_load_error(exc, data_config=str(data_config))

        if dataset_index is not None and dataset_indices is not None:
            raise ValueError(
                "MMMU-Pro benchmark config may set only one of 'dataset_index' or "
                "'dataset_indices'."
            )

        if isinstance(dataset_indices, str):
            parsed_indices = ast.literal_eval(dataset_indices)
            dataset_indices = [parsed_indices] if isinstance(parsed_indices, int) else parsed_indices

        if dataset_indices is not None:
            iterator = []
            for raw_idx in dataset_indices:
                idx = int(raw_idx)
                if idx < 0 or idx >= len(dataset):
                    raise IndexError(
                        f"dataset_indices contains {idx} out of range [0, {len(dataset)})"
                    )
                iterator.append((idx, dataset[idx]))
        elif dataset_index is not None:
            idx = int(dataset_index)
            if idx < 0 or idx >= len(dataset):
                raise IndexError(
                    f"dataset_index={idx} out of range [0, {len(dataset)})"
                )
            iterator = [(idx, dataset[idx])]
        else:
            iterator = enumerate(dataset)

        tasks: list[BenchmarkTask] = []
        for idx, item in iterator:
            task_id = item.get("id", str(idx))
            question_text = item.get("question", "")

            # options may be a list, a JSON string, or a Python-literal string
            raw_options = item.get("options", [])
            if isinstance(raw_options, str):
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
            for img_index, img in enumerate(_collect_dataset_images(item), start=1):
                img_key = f"image_{img_index}"
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
