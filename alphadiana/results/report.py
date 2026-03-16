"""Report generation from stored evaluation results."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from alphadiana.config.experiment_config import ExperimentConfig
    from alphadiana.results.result_store import ResultStore


@dataclass
class RunSummary:
    """Summary statistics for a single evaluation run."""

    run_id: str
    agent: str
    agent_version: str
    benchmark: str
    total_tasks: int
    completed: int
    failed: int
    accuracy: float
    accuracy_total: float
    mean_score: float
    mean_wall_time_sec: float
    total_tokens: dict
    per_category: dict[str, float]
    error_distribution: dict[str, int] = field(default_factory=dict)
    num_samples: int = 1
    pass_at_k: float = 0.0
    avg_at_k: float = 0.0
    per_category_pass_at_k: dict[str, float] = field(default_factory=dict)
    per_category_avg_at_k: dict[str, float] = field(default_factory=dict)
    timestamp: str = ""


def _get_category(r: dict) -> str:
    """Extract category from a result record."""
    task_meta = r.get("task_metadata", {})
    resp_meta = r.get("metadata", {})
    if isinstance(task_meta, dict) and task_meta.get("category"):
        return task_meta["category"]
    if isinstance(resp_meta, dict) and resp_meta.get("category"):
        return resp_meta["category"]
    return "default"


class ReportGenerator:
    """Generates summary reports from stored evaluation results."""

    @staticmethod
    def _infer_from_results(results: list[dict], key: str, fallback: str = "") -> str:
        """Extract a run-level field from the first record that has it."""
        for r in results:
            val = r.get(key)
            if val is not None and val != "":
                return str(val)
        return fallback

    def generate(
        self,
        result_store: "ResultStore",
        config: "ExperimentConfig | None" = None,
    ) -> RunSummary:
        """Load results from the store and compute summary statistics.

        Run-level metadata (run_id, agent_name, etc.) is read from the
        JSONL records first.  *config* is an optional fallback for legacy
        files that lack these fields.
        """
        results = result_store.load()
        total = len(results)

        # Infer run-level metadata from data; fall back to config.
        _cfg = config  # may be None
        run_id = self._infer_from_results(results, "run_id") or (
            getattr(_cfg, "run_id", "") if _cfg else "") or result_store.run_id
        agent_name = self._infer_from_results(results, "agent_name") or (
            getattr(_cfg, "agent_name", "") if _cfg else "unknown")
        agent_version = self._infer_from_results(results, "agent_version") or (
            getattr(_cfg, "agent_version", "") if _cfg else "unknown")
        benchmark_name = self._infer_from_results(results, "benchmark_name") or (
            getattr(_cfg, "benchmark_name", "") if _cfg else "unknown")

        # num_samples: prefer data, then config, then infer from max samples per task.
        num_samples_raw = self._infer_from_results(results, "num_samples")
        if num_samples_raw:
            num_samples = int(num_samples_raw)
        elif _cfg is not None:
            num_samples = getattr(_cfg, "num_samples", 1)
        else:
            by_task_tmp: dict[str, int] = {}
            for r in results:
                tid = r.get("task_id", "")
                by_task_tmp[tid] = by_task_tmp.get(tid, 0) + 1
            num_samples = max(by_task_tmp.values(), default=1)

        # Only count tasks that were scored (excludes infrastructure errors).
        completed_results = [r for r in results if r.get("score") is not None]
        completed = len(completed_results)
        failed = total - completed

        correct_count = sum(1 for r in completed_results if r.get("correct", False))
        accuracy = correct_count / completed if completed > 0 else 0.0
        accuracy_total = correct_count / total if total > 0 else 0.0

        scores = [r.get("score", 0.0) for r in completed_results]
        mean_score = sum(scores) / len(scores) if scores else 0.0

        wall_times = [r.get("wall_time_sec", 0.0) for r in completed_results]
        mean_wall_time = sum(wall_times) / len(wall_times) if wall_times else 0.0

        # Aggregate token usage across all results.
        total_prompt = 0
        total_completion = 0
        for r in results:
            usage = r.get("token_usage", {})
            total_prompt += usage.get("prompt_tokens", 0)
            total_completion += usage.get("completion_tokens", 0)
        total_tokens = {
            "prompt_tokens": total_prompt,
            "completion_tokens": total_completion,
        }

        # Group results by task_id for per-category and pass@k computation.
        by_task: dict[str, list[dict]] = defaultdict(list)
        for r in results:
            by_task[r["task_id"]].append(r)

        # Per-category accuracy (based on task metadata "category" field).
        category_correct: dict[str, int] = {}
        category_total: dict[str, int] = {}
        for r in completed_results:
            cat = _get_category(r)
            category_total[cat] = category_total.get(cat, 0) + 1
            if r.get("correct", False):
                category_correct[cat] = category_correct.get(cat, 0) + 1

        per_category = {
            cat: category_correct.get(cat, 0) / category_total[cat]
            for cat in category_total
        }

        # Compute error distribution from failed tasks
        error_dist: dict[str, int] = {}
        for r in results:
            err = r.get("error")
            if isinstance(err, dict):
                etype = err.get("error_type", "unknown")
                error_dist[etype] = error_dist.get(etype, 0) + 1

        # Pass@K: fraction of unique tasks where at least 1 sample is correct.
        num_unique_tasks = len(by_task)
        tasks_passed = sum(
            1 for samples in by_task.values()
            if any(s.get("correct", False) for s in samples)
        )
        pass_at_k = tasks_passed / num_unique_tasks if num_unique_tasks > 0 else 0.0

        # Per-category pass@k
        cat_tasks_total: dict[str, set[str]] = defaultdict(set)
        cat_tasks_passed: dict[str, set[str]] = defaultdict(set)
        for task_id, samples in by_task.items():
            cat = _get_category(samples[0])
            cat_tasks_total[cat].add(task_id)
            if any(s.get("correct", False) for s in samples):
                cat_tasks_passed[cat].add(task_id)

        per_category_pass_at_k = {
            cat: len(cat_tasks_passed.get(cat, set())) / len(task_ids)
            for cat, task_ids in cat_tasks_total.items()
        }

        # Avg@K: per-task average correctness rate, then averaged across tasks.
        # For each task, compute (number of correct samples) / (number of total samples).
        task_avg_scores: list[float] = []
        for samples in by_task.values():
            n_total = len(samples)
            n_correct = sum(1 for s in samples if s.get("correct", False))
            task_avg_scores.append(n_correct / n_total if n_total > 0 else 0.0)
        avg_at_k = sum(task_avg_scores) / len(task_avg_scores) if task_avg_scores else 0.0

        # Per-category avg@k
        cat_task_avgs: dict[str, list[float]] = defaultdict(list)
        for task_id, samples in by_task.items():
            cat = _get_category(samples[0])
            n_total = len(samples)
            n_correct = sum(1 for s in samples if s.get("correct", False))
            cat_task_avgs[cat].append(n_correct / n_total if n_total > 0 else 0.0)

        per_category_avg_at_k = {
            cat: sum(avgs) / len(avgs) if avgs else 0.0
            for cat, avgs in cat_task_avgs.items()
        }

        return RunSummary(
            run_id=run_id,
            agent=agent_name,
            agent_version=agent_version,
            benchmark=benchmark_name,
            total_tasks=total,
            completed=completed,
            failed=failed,
            accuracy=accuracy,
            accuracy_total=accuracy_total,
            mean_score=mean_score,
            mean_wall_time_sec=mean_wall_time,
            total_tokens=total_tokens,
            per_category=per_category,
            error_distribution=error_dist,
            num_samples=num_samples,
            pass_at_k=pass_at_k,
            avg_at_k=avg_at_k,
            per_category_pass_at_k=per_category_pass_at_k,
            per_category_avg_at_k=per_category_avg_at_k,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    @staticmethod
    def _get_category(r: dict) -> str:
        return _get_category(r)

    def to_markdown(self, summary: RunSummary) -> str:
        """Generate a markdown report string with a summary table."""
        lines = [
            f"# Evaluation Report: {summary.run_id}",
            "",
            f"**Timestamp:** {summary.timestamp}",
            "",
            "## Summary",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Agent | {summary.agent} |",
            f"| Agent Version | {summary.agent_version} |",
            f"| Benchmark | {summary.benchmark} |",
            f"| Total Tasks | {summary.total_tasks} |",
            f"| Completed | {summary.completed} |",
            f"| Failed | {summary.failed} |",
            f"| Num Samples (k) | {summary.num_samples} |",
            f"| Accuracy (completed) | {summary.accuracy:.4f} |",
            f"| Accuracy (total) | {summary.accuracy_total:.4f} |",
            f"| Pass@{summary.num_samples} | {summary.pass_at_k:.4f} |",
            f"| Avg@{summary.num_samples} | {summary.avg_at_k:.4f} |",
            f"| Mean Score | {summary.mean_score:.4f} |",
            f"| Mean Wall Time (s) | {summary.mean_wall_time_sec:.2f} |",
            f"| Prompt Tokens | {summary.total_tokens.get('prompt_tokens', 0)} |",
            f"| Completion Tokens | {summary.total_tokens.get('completion_tokens', 0)} |",
            "",
        ]

        if summary.per_category:
            lines.extend([
                "## Per-Category Accuracy",
                "",
                "| Category | Accuracy |",
                "|----------|----------|",
            ])
            for cat, acc in sorted(summary.per_category.items()):
                lines.append(f"| {cat} | {acc:.4f} |")
            lines.append("")

        if summary.num_samples > 1 and summary.per_category_pass_at_k:
            lines.extend([
                f"## Per-Category Pass@{summary.num_samples} / Avg@{summary.num_samples}",
                "",
                f"| Category | Pass@{summary.num_samples} | Avg@{summary.num_samples} |",
                "|----------|----------|----------|",
            ])
            for cat in sorted(summary.per_category_pass_at_k.keys()):
                pk = summary.per_category_pass_at_k.get(cat, 0.0)
                ak = summary.per_category_avg_at_k.get(cat, 0.0)
                lines.append(f"| {cat} | {pk:.4f} | {ak:.4f} |")
            lines.append("")

        if summary.error_distribution:
            lines.extend([
                "## Error Distribution",
                "",
                "| Error Type | Count |",
                "|------------|-------|",
            ])
            for error_type, count in sorted(summary.error_distribution.items()):
                lines.append(f"| {error_type} | {count} |")
            lines.append("")

        return "\n".join(lines)
