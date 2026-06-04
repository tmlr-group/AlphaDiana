"""MoltBench QJL scorer: computes optimization gain from verifier artifacts."""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

from alphadiana.benchmarks.moltbench.utils import parse_results_json
from alphadiana.scorer.base import Scorer, ScoreResult
from alphadiana.scorer.registry import register_scorer

logger = logging.getLogger(__name__)


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _load_results(response: Any) -> dict[str, Any]:
    artifacts_dir = getattr(response, "metadata", {}).get("artifacts_dir", "")
    if artifacts_dir:
        try:
            return parse_results_json(Path(artifacts_dir))
        except Exception as exc:
            logger.warning("Failed to load QJL results from %s: %s", artifacts_dir, exc)
    answer = getattr(response, "answer", None)
    return answer if isinstance(answer, dict) else {}


@register_scorer("moltbench_qjl")
class MoltBenchQJLScorer(Scorer):
    """Score QJL runs with OG = baseline_runtime_ms / optimized_runtime_ms."""

    def __init__(self) -> None:
        self._distortion_threshold = 0.15
        self._min_og_for_correct = 1.0
        self._baseline: dict[str, Any] = {}

    @property
    def name(self) -> str:
        return "moltbench_qjl"

    def setup(self, config: dict) -> None:
        self._distortion_threshold = float(config.get("distortion_threshold", 0.15))
        self._min_og_for_correct = float(config.get("min_og_for_correct", 1.0))
        self._baseline = dict(config.get("baseline", {}) or {})

    def score(self, task: Any, response: Any) -> ScoreResult:
        baseline = self._baseline
        if not baseline and isinstance(getattr(task, "ground_truth", None), dict):
            baseline = task.ground_truth

        baseline_runtime = _as_float(baseline.get("runtime_ms"), 0.0)
        if baseline_runtime <= 0:
            return ScoreResult(
                correct=False,
                score=0.0,
                expected=str(baseline),
                predicted=None,
                rationale="Invalid QJL baseline: runtime_ms must be > 0.",
                metadata={"baseline_runtime_ms": baseline_runtime},
            )

        results = _load_results(response)
        metadata = dict(getattr(response, "metadata", {}) or {})
        exit_code = metadata.get("exit_code")
        exit_benign = bool(metadata.get("exit_benign"))
        timed_out = bool(metadata.get("timeout"))

        optimized_runtime = _as_float(results.get("runtime_ms"), 0.0)
        distortion = _as_float(results.get("distortion"), float("inf"))
        if "correctness_pass" in results:
            correctness_pass = bool(results.get("correctness_pass"))
        else:
            correctness_pass = distortion < self._distortion_threshold
        distortion_ok = distortion < self._distortion_threshold
        og = baseline_runtime / optimized_runtime if optimized_runtime > 0 else 0.0
        has_metrics = optimized_runtime > 0 or "runtime_ms" in results

        score_meta: dict[str, Any] = {
            "og": og,
            "baseline_runtime_ms": baseline_runtime,
            "optimized_runtime_ms": optimized_runtime,
            "distortion": distortion,
            "distortion_threshold": self._distortion_threshold,
            "correctness_pass": correctness_pass,
            "distortion_ok": distortion_ok,
            "timed_out": timed_out,
            "exit_benign": exit_benign,
        }
        if exit_code is not None:
            score_meta["exit_code"] = exit_code
        if metadata.get("artifacts_dir"):
            score_meta["artifacts_dir"] = metadata["artifacts_dir"]
        if results:
            score_meta["bench_result"] = results

        if timed_out or (
            exit_code is not None
            and int(exit_code) != 0
            and not exit_benign
            and not has_metrics
        ):
            status = "timed out" if timed_out else f"exited with code {exit_code}"
            return ScoreResult(
                correct=False,
                score=0.0,
                expected=str(baseline),
                predicted=f"{status}; OG={og:.4f}, distortion={distortion:.6f}",
                rationale=(
                    f"Container {status}. No valid QJL verifier metrics were available "
                    "for scoring."
                ),
                metadata=score_meta,
            )

        is_correct = (
            bool(correctness_pass)
            and distortion_ok
            and optimized_runtime > 0
            and og >= self._min_og_for_correct
        )
        return ScoreResult(
            correct=is_correct,
            score=og if is_correct else 0.0,
            expected=str(baseline),
            predicted=(
                f"OG={og:.4f}, runtime={optimized_runtime:.2f}ms, "
                f"distortion={distortion:.6f}"
            ),
            rationale=(
                f"OG={og:.4f} (baseline={baseline_runtime:.2f}ms, "
                f"optimized={optimized_runtime:.2f}ms). "
                f"Distortion={distortion:.6f} "
                f"(threshold={self._distortion_threshold})."
            ),
            metadata=score_meta,
        )
