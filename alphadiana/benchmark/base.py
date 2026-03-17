"""Benchmark base class and BenchmarkTask dataclass."""

from __future__ import annotations

import logging
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class BenchmarkTask:
    """A single task/problem from a benchmark."""

    task_id: str
    problem: str
    ground_truth: Any
    metadata: dict = field(default_factory=dict)
    attachments: dict[str, bytes] = field(default_factory=dict)


def load_dataset_with_retry(
    dataset_path: str,
    config_name: str | None = None,
    *,
    split: str = "train",
    max_retries: int = 3,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
    **kwargs: Any,
) -> Any:
    """Load a HuggingFace dataset with retry and exponential backoff.

    Handles transient network errors and HF hub rate limits gracefully.
    """
    from datasets import load_dataset

    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return load_dataset(dataset_path, config_name, split=split, **kwargs)
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries:
                delay = min(base_delay * (2 ** attempt), max_delay)
                jitter = random.uniform(0, delay * 0.3)
                logger.warning(
                    "Dataset load attempt %d/%d failed for %s: %s. Retrying in %.1fs",
                    attempt + 1, max_retries + 1, dataset_path, exc, delay + jitter,
                )
                time.sleep(delay + jitter)
            else:
                raise


class Benchmark(ABC):
    """Abstract base class for benchmarks."""

    name: str = ""

    @abstractmethod
    def load_tasks(self, config: dict) -> list[BenchmarkTask]:
        """Fetch/parse the dataset and return a list of tasks."""
        ...

    def default_scorer(self) -> str:
        """Suggest a default scorer name (can be overridden by config)."""
        return "exact_match"
