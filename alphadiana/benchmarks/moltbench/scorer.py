"""MoltBench scorer: evaluates GPU kernel optimization results.

Reads results.json from container artifacts and computes correctness,
speedup, and fast_p metrics.
"""

from __future__ import annotations

import logging
from pathlib import Path

from alphadiana.scorer.base import Scorer, ScoreResult
from alphadiana.scorer.registry import register_scorer
from alphadiana.benchmarks.moltbench.utils import (
    parse_results_json,
    compute_fast_p_flags,
)

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLDS = [1.0, 1.5, 2.0, 3.0, 5.0]


@register_scorer("moltbench")
class MoltBenchScorer(Scorer):
    """Scorer for MoltBench kernel optimization tasks.

    Reads the structured results from container artifacts and produces
    ScoreResult with speedup as the score and fast_p flags in metadata.

    Config keys:
        thresholds: list[float] — speedup thresholds for fast_p (default: [1.0, 1.5, 2.0, 3.0, 5.0])
        score_mode: str — "final" (default), "best", or "improvement"
    """

    def __init__(self) -> None:
        self._thresholds: list[float] = DEFAULT_THRESHOLDS
        self._score_mode: str = "final"

    @property
    def name(self) -> str:
        return "moltbench"

    def setup(self, config: dict) -> None:
        self._thresholds = config.get("thresholds", DEFAULT_THRESHOLDS)
        self._score_mode = config.get("score_mode", "final")

    def score(self, task, response) -> ScoreResult:
        # Extract results from agent response metadata
        artifacts_dir = response.metadata.get("artifacts_dir", "")
        if artifacts_dir:
            results = parse_results_json(artifacts_dir)
        elif isinstance(response.answer, dict):
            results = response.answer
        else:
            results = {"correctness": "MISSING", "speedup": 0.0}

        correctness = results.get("correctness", "MISSING")
        speedup = float(results.get("speedup", 0.0))
        kernel_time_ms = results.get("kernel_time_ms")
        reference_time_ms = results.get("reference_time_ms", results.get("pytorch_time_ms"))
        exit_code = response.metadata.get("exit_code")
        exit_benign = bool(response.metadata.get("exit_benign"))
        timed_out = bool(response.metadata.get("timeout"))

        is_correct = correctness.upper() == "PASS"
        fast_p_flags = compute_fast_p_flags(correctness, speedup, self._thresholds)

        # Build metadata
        meta = {
            **fast_p_flags,
            "correctness": correctness,
            "final_speedup": speedup,
            "score_mode": self._score_mode,
            "timed_out": timed_out,
            "exit_benign": exit_benign,
        }
        if exit_code is not None:
            meta["exit_code"] = exit_code
        if kernel_time_ms is not None:
            meta["kernel_time_ms"] = kernel_time_ms
        if reference_time_ms is not None:
            meta["reference_time_ms"] = reference_time_ms
            meta["pytorch_time_ms"] = reference_time_ms

        # Include artifact paths for traceability
        if artifacts_dir:
            meta["artifacts_dir"] = artifacts_dir

        if timed_out or (exit_code is not None and int(exit_code) != 0 and not exit_benign):
            status = "timed out" if timed_out else f"exited with code {exit_code}"
            return ScoreResult(
                correct=False,
                score=0.0,
                expected=str(task.ground_truth),
                predicted=f"{status}; correctness={correctness}, speedup={speedup:.2f}x",
                rationale=(
                    f"Container {status}. Final benchmark artifacts were collected, "
                    "but the run is treated as invalid for scoring."
                ),
                metadata=meta,
            )

        return ScoreResult(
            correct=is_correct,
            score=speedup if is_correct else 0.0,
            expected=str(task.ground_truth),
            predicted=f"correctness={correctness}, speedup={speedup:.2f}x",
            rationale=(
                f"Kernel {'passed' if is_correct else 'failed'} correctness. "
                f"Speedup: {speedup:.2f}x vs PyTorch reference."
            ),
            metadata=meta,
        )
