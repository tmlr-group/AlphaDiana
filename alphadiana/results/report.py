"""Report generation from stored evaluation results."""

from __future__ import annotations

from collections import Counter
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
    mean_score: float
    mean_wall_time_sec: float
    total_tokens: dict
    per_category: dict[str, float]
    error_distribution: dict[str, int] = field(default_factory=dict)
    timestamp: str = ""


class ReportGenerator:
    """Generates summary reports from stored evaluation results."""

    def generate(
        self,
        result_store: "ResultStore",
        config: "ExperimentConfig",
    ) -> RunSummary:
        """Load results from the store and compute summary statistics."""
        results = result_store.load()
        total = len(results)

        # Only count tasks that were scored (excludes infrastructure errors).
        completed_results = [r for r in results if r.get("score") is not None]
        completed = len(completed_results)
        failed = total - completed

        correct_count = sum(1 for r in completed_results if r.get("correct", False))
        accuracy = correct_count / completed if completed > 0 else 0.0

        scores = [r.get("score", 0.0) for r in completed_results]
        mean_score = sum(scores) / len(scores) if scores else 0.0

        wall_times = [r.get("wall_time_sec", 0.0) for r in results]
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

        # Per-category accuracy (based on task metadata "category" field).
        category_correct: dict[str, int] = {}
        category_total: dict[str, int] = {}
        for r in completed_results:
            cat = "default"
            # Check both task_metadata and metadata for category.
            task_meta = r.get("task_metadata", {})
            resp_meta = r.get("metadata", {})
            if isinstance(task_meta, dict) and task_meta.get("category"):
                cat = task_meta["category"]
            elif isinstance(resp_meta, dict) and resp_meta.get("category"):
                cat = resp_meta["category"]
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

        return RunSummary(
            run_id=config.run_id,
            agent=config.agent_name,
            agent_version=config.agent_version,
            benchmark=config.benchmark_name,
            total_tasks=total,
            completed=completed,
            failed=failed,
            accuracy=accuracy,
            mean_score=mean_score,
            mean_wall_time_sec=mean_wall_time,
            total_tokens=total_tokens,
            per_category=per_category,
            error_distribution=error_dist,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

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
            f"| Accuracy | {summary.accuracy:.4f} |",
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
